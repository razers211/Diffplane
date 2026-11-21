# Operations

Day-to-day operation of Diffplane. Commands run from `deploy/` on the Docker host (prefix
`sudo` if your Docker needs it). See [concepts](concepts.md) and the [security model](security-model.md)
for the rules behind them.

## First-time setup

On a new install (no account in the platform database) the GUI shows a setup page instead of the
login. It asks for a one-time code that the orchestrator prints in its log at start:

```sh
sudo docker compose logs orchestrator | grep SETUP
```

Enter it with the first admin's name and password. Ten wrong codes replace the code with a new
one (printed again); a restart prints a new one too. Once any account exists the setup page and
its API are gone for good. A welcome page then leads to the next steps: accounts, switch
credentials (Admin › Settings) and the first fabric.

## Users and roles

One role per user, on a ladder; each role can do what the ones before it can:

| Role       | Can                                                                     |
|------------|-------------------------------------------------------------------------|
| `viewer`   | read everything (devices, topology, segments, diffs, drift, deploys, audit) |
| `operator` | change intent (segments, services, routing), run diffs, request deploys |
| `approver` | approve deploys, their own requests included                            |
| `admin`    | also accounts, settings, per-fabric deploy permission, deleting a fabric |

Admins manage accounts in the GUI (**Admin › Accounts**): create, change a role, set a new
password (which signs that user out), delete. The last admin cannot be deleted or demoted, and
nobody deletes their own account. Every user changes their own password from **Password** in
the top bar. The command line below still works, for example to make the first admin of an
existing install:

```sh
docker compose exec -it orchestrator python -m orchestrator.users create alice --role operator
docker compose exec -it orchestrator python -m orchestrator.users set-role alice approver
docker compose exec -it orchestrator python -m orchestrator.users set-role alice admin
docker compose exec -it orchestrator python -m orchestrator.users set-password alice
```

New users are viewers unless `--role` is given. Users created before roles existed are
approvers. Role changes take effect on the user's next request. Every user change is in the
audit trail (Changes › **Audit**).

**Accounts limited to fabrics:** in Admin › Accounts, the *Fabrics* column of a viewer or
operator opens *Every fabric* or *Only these fabrics*. A limited account sees nothing of the other
fabrics (fabric picker, switches, map, changes, audit; the API refuses them), works in one of its
fabrics at a time (no "All fabrics"), and cannot create or import a fabric or change the
inventory. For an operator, *may approve its deploys* per fabric lets them approve that fabric's
deploys, their own requests included, and approve its switches' ZTP keys (pinning host keys by hand and approving imports stay with approvers). Admins and approvers always reach every fabric: making an
account approver or admin drops its limit; making an operator a viewer drops its approvals.

## Directory sign-in

Admin › **Directory** (admins): Active Directory over LDAPS, and RADIUS. A local account always signs
in locally, whatever the directory's state: keep at least one local admin as the break-glass account.
Anyone else is asked of AD first, then of RADIUS when AD does not know them or does not answer. A wrong
AD password is refused, not passed on to RADIUS. Their role and fabrics come from the group mapping
(AD) or the RADIUS attribute (`operator:example+,east`), and are rewritten at every sign-in. Someone in no
mapped group is refused and shown as "no access" in Accounts. **Test a sign-in** shows what a user
would get, and the groups AD returns.

**Lab check with the test directory** (OpenLDAP and FreeRADIUS with throwaway users, never started
otherwise). OpenLDAP has no nested-group search, so the filters below differ from AD's defaults.

1. Make the test certificates, then start the test directory (from `deploy/`):

   ```sh
   ./directory-test/make-certs.sh
   sudo docker compose --profile directory-test up -d --force-recreate directory-ldap directory-radius
   cat directory-test/certs/ca.crt
   ```

   The image's bundled certificate has expired, so the script makes a test CA and a certificate for
   `directory-ldap` (valid a year, kept out of git). The last command prints the CA certificate.
2. Fill in Admin › Directory:

   | Field | Value |
   |---|---|
   | Servers | `ldaps://directory-ldap:636` |
   | Service account | `cn=admin,dc=example,dc=net` |
   | Service account password | `ldap-test-admin` |
   | Search base | `dc=example,dc=net` |
   | User filter | `(uid={username})` |
   | Group filter | `(&(objectClass=groupOfNames)(member={user_dn}))` |
   | CA certificate | the PEM from step 1 |
   | RADIUS servers | `directory-radius` |
   | Shared secret | `radius-test-secret` |

3. Group mapping:
   - `cn=netops,ou=groups,dc=example,dc=net`: operator, fabric example, approves;
   - `cn=noc,ou=groups,dc=example,dc=net`: viewer, every fabric.
4. Test a sign-in, then sign in for real as each (a private window):

   | User | Password | Expected |
   |---|---|---|
   | `alice` | `alice-test-pw` | AD, operator on example, approves |
   | `carol` | `carol-test-pw` | AD, viewer, every fabric |
   | `erin` | `erin-test-pw` | AD knows her, no mapped group: refused |
   | `bob` | `bob-test-pw` | RADIUS, operator on example, approves |
   | `dave` | `dave-test-pw` | RADIUS, viewer |
   | `frank` | `frank-test-pw` | RADIUS accepts, no role: refused |

5. Stop the test directory:

   ```sh
   sudo docker compose --profile directory-test stop directory-ldap directory-radius
   ```

   Then:
   - "Test a sign-in" for alice says the directory did not answer;
   - your local admin still signs in.
6. Clean up:

   ```sh
   sudo docker compose --profile directory-test rm -sf directory-ldap directory-radius
   rm -rf directory-test/certs
   ```

   Then empty the Directory fields again, and delete the test accounts from Accounts.

## Settings

Admins change the platform's settings in **Admin › Settings**: the switch logins (platform
and collector), SNMP community, M-LAG DFS key, ZTP's admin account and DHCP network, the drift
interval, how long a sign-in lasts, and the certificate's names. A value set there replaces the
one in `deploy/.env` and applies at once (the certificate's names at the next start); **Back to
.env** removes it. Passwords and keys are never shown again once set, by the page or the API;
they are kept in the platform database (back it up as such). Every change is audited (secrets by
name only).

The collector, a separate container, reads its login from a file the orchestrator writes into
the `platform-shared` volume, at each reconnect: after changing it, the collector uses the new
login the next time a session to a switch is opened.

The NetBox token, the database and Redis passwords, NetBox's secret key, and where the
orchestrator listens stay in `deploy/.env` only (shown read only): change them there, with care,
then restart.

## Fabric settings and deleting a fabric

On the **Fabric** page:

- **SNMP location and contact, gateway MAC** (operators): saved as intent, so the yellow bar shows
  the switches that change; deploy as usual. Re-seeding a YAML fabric keeps the GUI's values, as
  for NTP. A new gateway MAC changes every VBDIF of the fabric at the next deploy.
- **Deploys to this fabric** (admins): on or off. Turning them on asks for the fabric's name.
  `DEPLOY_ALLOWED_SITES` in `.env` is only the default. Approving a deploy checks again, so a
  fabric turned off after a deploy was prepared cannot be deployed.
- **Delete this fabric** (admins): shows what goes first; refused while a switch is active
  (decommission it first) or a deploy runs; the fabric's name is typed to confirm. Everything of
  the fabric in NetBox is removed (switches, cables, addresses and pools, segments, services,
  routing intent, racks, its ASN if no other fabric uses it). Nothing is sent to the switches;
  the history of deploys, checks and the audit trail stays. A fabric with a YAML in
  `seed/fabrics/` is created again by the next seed.

**A leaf's role:** switch page › Settings › Role (operators): *Make it a border-leaf* or
*leaf*; the M-LAG peer changes with it. It is an intent change (pending bar, then review and
deploy as usual). Refused while a port of either switch carries segments (detach them first), for
a centralized fabric's gateway pair, and back to leaf while routing intent uses a segment VPN
instance that only the border role puts there. Re-seeding keeps the role set in the GUI.

## Drift

Every 15 minutes (`DRIFT_INTERVAL_SECONDS` in `deploy/.env`) the orchestrator runs `annet
diff` on every active device of every fabric. Read-only: it fetches the running config and
compares it with the config generated from NetBox. the GUI also starts one for the
fabric a few seconds after any intent change is saved there.

Every page shows a yellow bar while something waits (a switch whose latest diff is not empty, a
deploy waiting for approval or rolling out), and the **Changes** menu entry counts the switches
that differ. **Changes › Pending** lists them with their diffs, next to the intent changes saved
since the fabric's last verified deploy:

- **differs**: someone changed the switch, *or* intent was changed in the GUI and not deployed
  yet. The platform does not guess which: open the diff, and compare with the saved changes;
- **could not be checked**: Annet could not fetch or generate (unreachable device, credentials,
  generator error); open the error.

From there an operator prepares the deploy, an approver approves it on the same page, and the
rollout runs there too. **Changes › History** keeps every deploy and every check.

A scheduled run is skipped while a deploy rolls out (a half-deployed fabric is not drift) or
while another diff job runs. A skipped run is retried after a minute, not a whole interval
later. Skips for any other reason (NetBox unreachable, ...) are logged as warnings
(`docker compose logs orchestrator`). **Check the switches now** (operator, Changes › Pending)
starts a run at once.

A verified deploy also counts as a check: verification needs the device's full diff to be
empty, and that result becomes the device's latest (linked to the deploy), so drift clears at once.

Prometheus scrapes the orchestrator's `/metrics` with the bearer token `METRICS_TOKEN` (from
`deploy/.env`, handed to Prometheus as a Compose secret; anything else gets 401):

- `fabric_drift_device_state{device, site, state}`: 1 for the device's current state;
- `fabric_drift_out_of_sync_devices`;
- `fabric_drift_last_run_timestamp_seconds`, `fabric_drift_interval_seconds`.

**Resolving drift:** if the switch is right, change the intent (GUI, or the fabric YAML and
re-seed) until the diff is empty. If NetBox is right, prepare and approve a deploy; the
patch puts the switch back.

## Intent documents

Intent documents (fabric settings, segments, routing, overrides, DC links) live in the platform
database, every version kept. Each change is published to a NetBox config context of the same
name, which is the copy the generators read: never edit those contexts in NetBox. At every start
the orchestrator makes NetBox's copy equal to the database again (a context changed or added in
NetBox by hand is put back or removed). An install from before this store keeps its intent: at
the first start NetBox's contexts are taken over as they are.

```sh
sudo docker compose exec orchestrator python -m orchestrator.intent_store check      # where NetBox's copy differs
sudo docker compose exec orchestrator python -m orchestrator.intent_store republish  # NetBox's copy made equal to the database
```

If a save answers "saved in the platform database but was not published to NetBox" (NetBox was
unreachable), or the alert `FabricIntentStoreDiffers` fires, run `republish` once NetBox answers;
until then diffs and deploys use the old value. After a restore, the first start publishes the
restored database's documents to NetBox.

## Alerts

Prometheus evaluates three rules (`deploy/prometheus/alerts.yml`) and sends firing alerts
to Alertmanager:

| Alert                       | Fires when                                                                 |
|-----------------------------|----------------------------------------------------------------------------|
| `FabricDeviceOutOfSync`     | a device has been out of sync for 5 minutes (drift is checked every 15, so a manual change alerts within about 20) |
| `FabricDriftCheckStopped`   | no drift run started for 3 intervals, or none since the orchestrator started (for 10 minutes) |
| `FabricCollectorDeviceDown` | every NETCONF poll of a device has failed for 5 minutes                    |
| `FabricIntentStoreDiffers`  | NetBox's published copy of an intent document differs from the platform database for 10 minutes (`python -m orchestrator.intent_store check` lists them, `republish` repairs) |

**Where alerts go:** Admin › Settings › **Alerts** (admins): *none* (the default: alerts
are held in Alertmanager, not sent), *email* (recipients, sender, SMTP server `host:port` with
TLS, optional login and password) or *webhook* (a URL that receives Alertmanager's JSON). The SMTP
password and the webhook URL are secrets: write-only, masked in every answer. On every change and
at each start the orchestrator writes Alertmanager's configuration into the volume
`alertmanager-config` (the file is the orchestrator's; never edit it by hand) and asks Alertmanager
to reload it; the page shows a refused reload or a receiver that misses a field (alerts stay held
until it is complete). **Send a test alert** posts `FabricTestAlert` to Alertmanager; it arrives
after Alertmanager's grouping wait (about 30 s). No port is published. List alerts inside the
containers:

```sh
sudo docker compose exec alertmanager amtool alert query --alertmanager.url=http://localhost:9093
sudo docker compose exec prometheus wget -qO- http://localhost:9090/api/v1/alerts   # pending ones too
```

Check the files after a change:

```sh
sudo docker compose exec prometheus promtool check rules /etc/prometheus/alerts.yml
sudo docker compose exec alertmanager amtool check-config /config/alertmanager.yml
```

Test the rules against recorded cases (hardware alerts; promtool in a throwaway container):

```sh
sudo docker compose run --rm --no-deps -v ./prometheus:/rules:ro \
  --entrypoint promtool prometheus test rules /rules/alerts.test.yml
```

## SSH host keys

The platform only talks to a switch whose SSH host keys are pinned in NetBox: Annet
(diffs, deploys, drift) on port 22 and the collector on port 830 (NETCONF). An unpinned
switch is refused, and so is one that presents another key: "check failed" on the Drift
tab, `FabricCollectorDeviceDown` after 5 minutes, and nothing is sent to it.

Pin a switch once it is active with its management address (device page, Management, Activate):

```sh
sudo docker compose exec -it orchestrator python -m orchestrator.hostkeys pin leaf1
sudo docker compose exec -it orchestrator python -m orchestrator.hostkeys show leaf1
```

From the GUI: device page, Management, **Fetch keys** shows what the switch offers on both
ports with SHA256 and MD5 fingerprints; after comparing them with the console, an approver
ticks the box and pins. The orchestrator fetches again at that moment and refuses if the
switch now offers any other key. From the command line:

`pin` fetches every key type the switch offers on both ports, prints their SHA256
fingerprints and writes them only after you type `yes`. **Compare them with the switch's own
display of its host keys, on its console, before typing yes**: fetching over the network
proves nothing by itself. On CE6881H V300 the switch has one RSA key (offered as
`rsa-sha2-512`/`rsa-sha2-256`, the same on both ports), shown by
`display rsa local-key-pair public`. Or copy the public key from the console and pass it with
`--key "<type> <base64>"`: then every key the switch offers must be one you gave.
Pins and removals are in the audit trail and in NetBox's change log.

**A switch replaced, reset or re-keyed** (RMA, factory reset, keys regenerated on the
switch, or another switch given a decommissioned switch's address): its keys change, so the
platform refuses it, exactly as it would refuse an impostor. Check on the new switch's
console that it is the one you installed, then `pin` it again; the old and new fingerprints
are shown side by side.

**A switch decommissioned:** on its device page, Management, Decommission: it goes back to
`planned`, its management address is freed and its pins are forgotten, so the collector, drift,
deploys and the known-hosts file drop it. Its fabric addresses and its config on the switch
stay. Activate it again (and pin it again) to bring it back.

**A switch removed:** only a planned switch (decommission it first); a leaf goes with its M-LAG
peer. Device page, Management, **Remove switch** lists what goes with it: its ports, cables,
loopbacks, VTEP, bypass and point-to-point addresses, and the ports at the far end of its links,
which lose their link config at their next deploy. Refused while it hosts a service or has
routing intent, segments or server trunks. A switch still listed in the fabric YAML comes back at
the next seed: remove it there too. A pin belongs to a NetBox device, not to an address: a new device
record on a reused address starts unpinned.

## HTTPS

The orchestrator serves the GUI and API over **HTTPS only**, with a self-signed certificate
in the lab and in production. It makes the certificate on first start
and keeps it in the `orchestrator-tls` volume; the key never leaves it. At every start it
reuses the certificate unless it expires within 30 days (validity 825 days) or does not
cover every name in `TLS_HOSTNAMES`; then it makes a new one. **Restart the orchestrator at
least once every two years**, or the certificate expires.

```sh
sudo docker compose logs orchestrator | grep TLS        # current SHA256 fingerprint
sudo docker compose exec -T orchestrator cat /tls-public/fabric.crt </dev/null > fabric.crt
```

- **Browsers** warn about a self-signed certificate. Before accepting it, compare the
  SHA256 fingerprint the browser shows with the one in the log. Better: import
  `fabric.crt` into the trust store of the operators' machines; then there is no warning,
  and a different certificate (a new one, or an impostor) is flagged.
- **Production name:** set `TLS_HOSTNAMES` in `deploy/.env` to the names and addresses
  people use (keep `localhost,127.0.0.1,orchestrator`), then
  `sudo docker compose up -d orchestrator`. The new certificate has a new fingerprint:
  re-import it.
- Prometheus scrapes `/metrics` over HTTPS and trusts exactly this certificate
  (`orchestrator-tls-public` volume, certificate only).
- The session cookie is `Secure` (`COOKIE_SECURE=true`): it is never sent over plain HTTP.

## Backups

The `backup` service writes a folder per day to `deploy/backups/<UTC time>/`:
`netbox.dump` (all intent: devices, cables, IPAM, segments, services), `platform.dump`
(users, jobs, approvals, audit) and `netbox-media.tar.gz`. Folders older than 14 days are
deleted. The dumps contain secrets (NetBox 4.4 stores classic API tokens unhashed, plus
password hashes): keep the folder private and copy it off the host (it is gitignored).

```sh
ls deploy/backups/
docker compose exec backup /backup.sh once        # a backup right now
docker compose logs backup
```

### Restore

Practise this before you need it: take a fresh backup, create a marker segment, restore,
and check that the row counts match and the marker is gone.

The backup container writes the dumps as root, so they are read with `sudo cat` and piped
in (a plain `< file` runs as your user). `</dev/null` keeps snap Docker's `exec -T` from
hanging when nothing is piped.

```sh
B=backups/<folder>                      # the backup to restore
sudo docker compose stop orchestrator collector netbox netbox-worker

# NetBox database
sudo docker compose exec -T netbox-postgres sh -c 'dropdb -U netbox netbox && createdb -U netbox netbox' </dev/null
sudo cat $B/netbox.dump | sudo docker compose exec -T netbox-postgres pg_restore -U netbox -d netbox --no-owner

# Platform database
sudo docker compose exec -T postgres sh -c 'dropdb -U platform platform && createdb -U platform platform' </dev/null
sudo cat $B/platform.dump | sudo docker compose exec -T postgres pg_restore -U platform -d platform --no-owner

sudo docker compose start netbox netbox-worker
sudo cat $B/netbox-media.tar.gz | sudo docker compose exec -T netbox sh -c 'tar -xzf - -C /opt/netbox/netbox/media'
sudo docker compose start orchestrator collector
curl --cacert fabric.crt https://127.0.0.1:8000/health
```

To check a restore, compare row counts before the backup and after the restore (the
orchestrator starts a drift job at boot, so `jobs` and `diff_snapshots` may be one higher):

```sh
sudo docker compose exec -T netbox-postgres psql -U netbox -d netbox -Atc "select 'devices',count(*) from dcim_device union all select 'interfaces',count(*) from dcim_interface union all select 'cables',count(*) from dcim_cable union all select 'ips',count(*) from ipam_ipaddress union all select 'prefixes',count(*) from ipam_prefix union all select 'vrfs',count(*) from ipam_vrf union all select 'l2vpns',count(*) from vpn_l2vpn union all select 'contexts',count(*) from extras_configcontext union all select 'tokens',count(*) from users_token" </dev/null
sudo docker compose exec -T postgres psql -U platform -d platform -Atc "select 'users',count(*) from users union all select 'jobs',count(*) from jobs union all select 'snapshots',count(*) from diff_snapshots union all select 'audit',count(*) from audit_events" </dev/null
```

Then run **Check the switches now** on Changes › Pending: after a restore, NetBox may describe an older
state than the switches. Read every diff before deploying anything.

## Config backups

The platform keeps each active, pinned switch's running config (`annet show current`, read-only on
the switch):
- after every verified deploy (in the deploy's log);
- once a day (`BACKUP_INTERVAL_SECONDS`, default 86400; skipped while a deploy rolls out);
- with **Back up now** on the switch page (operators).

A config that is unchanged since the switch's last version, comment lines aside, is not kept again.
Versions older than 90 days are removed, except each switch's last 20. History is on the switch page,
**Config and diff › History**: pick one version to read it, two to compare them. What the GUI and
API show is always masked (cipher text, hashes, passwords, and the platform's secrets).

The configs are stored encrypted in the platform database with `CONFIG_BACKUP_KEY` from
`deploy/.env`. New installs get one from `gen_env.py`. **An existing `.env` needs it added by hand**,
then a restart of the orchestrator:

```sh
python3 -c 'import base64,os; print("CONFIG_BACKUP_KEY=" + base64.urlsafe_b64encode(os.urandom(32)).decode())' >> deploy/.env
sudo docker compose up -d orchestrator
```

Without it, backups are off and the History panel says so. Keep a copy of the key outside the
server: the database backups (above) hold the versions, but they cannot be read without the key. A
different key makes older versions unreadable; the platform says so per version.

## First production onboarding (diff-only)

Goal: model an existing production fabric so that `annet diff` is empty on every device,
without the platform sending a single command. Deploys stay off for that fabric until an
admin turns them on.

1. **Keep deploys off.** `DEPLOY_ALLOWED_SITES` in `deploy/.env` must not list the new
   fabric (the default is empty: no fabric). The orchestrator refuses deploys to any other fabric.
   Turning it on later is a deliberate decision.
2. **Confirm the open facts**: exact switch models and VRP release (see
   [supported switches](supported-switches.md); anything else is unverified), NETCONF availability,
   fabric size, border leaves.
3. **Write the fabric YAML** (`seed/fabrics/<dc>.yaml`, format in `example.yaml`): devices
   with roles and models, M-LAG pairs and peer-links, cabling, address pools, ASN, VLANs,
   NTP/SNMP. Every device is created planned; switches added later come from the GUI
   (Devices, Add switches). Management addresses are set in the GUI (device page,
   Management, Activate); only active devices are read by drift and the collector.
4. **Seed it**, **pin every active switch** (see SSH host keys; nothing connects before),
   and check the result (Switches page and the map on the Overview):
   ```sh
   docker compose exec orchestrator python -m seed seed/fabrics/<dc>.yaml
   ```
   With the collector running, every link should be `ok` (LLDP matches the cabling).
5. **Recreate the existing tenants as intent** in the GUI, on that fabric: segments with
   their real VNIs and bridge-domain IDs, port attachments with their real VLAN IDs,
   firewall services with their real VLANs, addresses, next hops and ASNs, routing and
   peerings.
6. **Run the diff** (Changes › Pending, **Check the switches now**, for the selected fabric) and read every
   device's diff:
   - `+` lines: the generators would add or change this. Either intent is missing or
     different (fix the YAML or the GUI), or the generator's form differs from the
     switch's (a generator bug: report it, with the switch's lines).
   - `-` lines: config on the switch that the platform owns but no intent explains. The
     platform owns by kind on every leaf: all VPN instances except
     `_management_vpn_`/`_public_`, all Vlanifs except the M-LAG bypass one,
     `port link-type`/`port trunk allow-pass` on every front-panel port, bridge domains,
     L2 sub-interfaces, NVE VNIs, BGP-VPN instances. Each `-` line would be **removed** by the
     first deploy: model it, or agree that it goes. Routing objects (static
     routes, BGP peers, policies, filters, aggregates, OSPF processes) are different:
     hand-made ones are imported into intent automatically and never removed; check the
     Routing tab's auto-import report for what was adopted and what is left unmanaged.
   - Anything the platform does not own (AAA, MEth0/0/0, `_management_vpn_`, trap hosts,
     ...) never appears and is never touched.
7. **Repeat 5–6** until every device's diff is empty, or what remains is an agreed change.
   Keep the final diff output with the change record.
8. **Turn deploys on** only when you are ready: add the fabric to
   `DEPLOY_ALLOWED_SITES`, restart the orchestrator. First deploy: one device, one
   generator (e.g. `Snmp`), in a maintenance window; then one leaf pair at a time.
   Multi-device behaviour (M-LAG negotiation, EVPN between VTEPs, routing between
   leaves) has not been verified on real hardware: watch the topology and BGP state after
   each step.

## Importing an existing fabric

GUI: **Fabric** page, **Import fabric**. Read-only until the last step.

1. Give every switch's management address with its mask. The orchestrator fetches the host
   keys each offers on ports 22 and 830. An address that is already a management address in
   NetBox (a switch the platform has) can be previewed but never imported.
2. An approver compares each fingerprint with the switch's console (`display rsa local-key-pair
   public`) and approves; the keys are fetched again and must be the same. A switch whose keys
   are not approved is never logged into.
3. **Read approved switches**: SSH with the collector's read account (`COLLECTOR_LOGIN`, else
   `GNETCLI_DEV_LOGIN`), only `display version`, `display lldp neighbor brief` and
   `display current-configuration`. The outputs stay in the orchestrator's memory (they hold
   secrets) until the import is written or the orchestrator restarts.
4. **Preview**: name the fabric, give the gateways pool if no routed segment shows it, change
   roles (border leaves), architecture or pools if needed. Read every misfit and every
   non-empty patch: the patch is what the first deploy would send. Switches the given ones
   point at but that were not given (an unbuilt peer or spine) become planned placeholders.
5. **Import** writes the fabric to NetBox, the read switches active and pinned. Deploys stay
   off for it (`DEPLOY_ALLOWED_SITES`); continue as in "First production onboarding" from step 6.

## Bringing up a new fabric (greenfield)

For a DC built from scratch (format: `seed/fabrics/example.yaml`). Every device starts planned;
nothing connects to a switch until it is activated in the GUI with its management address and pinned.
Deploys to the fabric stay off (`DEPLOY_ALLOWED_SITES`) until an admin turns them on.

1. **Seed the fabric** with every device planned and review the generated configs (the goldens
   in `tests/golden/<dc>/`, or `annet gen <device>` in the orchestrator container).
2. **On each new switch, by hand on the console** (or by zero-touch provisioning, below), only
   what the platform needs to reach it.
   These are the lines a lab switch shows; the platform never manages
   them (management plane), so they stay as typed:
   ```
   interface MEth0/0/0
    ip binding vpn-instance _management_vpn_
    ip address <management address> <mask>
   ip route-static vpn-instance _management_vpn_ 0.0.0.0 0.0.0.0 <management gateway>
   stelnet server enable
   snetconf server enable
   netconf
    protocol inbound ssh port 830
   ssh server-source -i MEth0/0/0
   undo ssh server-source all-interface
   undo ssh ipv6 server-source all-interface
   ssh server rsa-key min-length 3072
   ssh authorization-type default aaa
   ssh server cipher aes256_gcm aes128_gcm aes256_ctr aes192_ctr aes128_ctr
   ssh server hmac sha2_512 sha2_256
   ssh server key-exchange dh_group_exchange_sha256 dh_group16_sha512 curve25519_sha256
   ```
   plus the local accounts (`aaa`: the platform's account for Annet, `GNETCLI_DEV_LOGIN`, and
   the collector's, `COLLECTOR_LOGIN`, with `service-type ssh`) and an RSA host key of at least
   3072 bits. The commands that create accounts and keys are not in the running config, so
   they are not verified here: check them on the first switch. Still to verify: the
   lowest privilege level that allows NETCONF `<get>` (for a read-only collector account),
   and `bgp <4-byte ASN>` as a no-commit test while the switch has no BGP yet.
3. **Make it active**: on its device page, Management, Activate with its management address
   (with mask), then pin its host keys after comparing the fingerprints on the console (see SSH host keys). The collector
   starts polling it; the map on the Overview shows its links as LLDP finds them.
4. **First diff**: the whole generated config shows as additions. Read it once completely.
5. **First deploys**, once deploys are on for the fabric: one device at a time,
   one concern at a time, verified before the next:
   `Hostname,Snmp,Ntp`, then `Loopbacks,FabricLinks,Ospf`, then `Mlag,MlagBypass`, then
   `Nve,BgpEvpn`; then the next device. Bring up both devices of a pair before any tenant.
   Watch the map on the Overview and BGP state after each step: multi-device behaviour (M-LAG,
   EVPN between VTEPs) has not been verified on real hardware.

## Zero-touch provisioning

A new switch can bring itself up instead of being configured by hand on its console (step 2
of the greenfield bring-up). Unverified until a blank switch exists: which DHCP options V300 ZTP
reads, the intermediate file format and plain passwords in the startup file. Check them on the
first blank switch and keep the console at hand.

1. **Configure once** in `deploy/.env`: `ZTP_DHCP_INTERFACE` (this host's interface on the
   management network), `ZTP_DHCP_NETWORK` (e.g. `172.24.0.0/24`), `ZTP_SERVER_ADDRESS` (this
   host's address there), and the admin account `ZTP_ADMIN_LOGIN` / `ZTP_ADMIN_PASSWORD`. The
   first-boot config also creates the platform accounts (`GNETCLI_DEV_LOGIN`,
   `COLLECTOR_LOGIN`), so their passwords must be set.
2. **Self-test** (isolated Docker network, never the management LAN):
   `sudo docker compose --profile ztp build && sudo sh ztp/selftest.sh`.
3. **Start DHCP/TFTP**: `sudo docker compose --profile ztp up -d`. It answers only registered
   MACs, each with its fixed address, never from a pool; unknown devices get no answer. Make sure
   no other DHCP server on the management network answers the switch first.
4. **Register** the switch on its device page (it must be planned): MEth MAC (from its label),
   management address with mask, gateway. "Show its files" shows the intermediate file and the
   first-boot config (passwords masked). Its files are served for `ZTP_WINDOW_HOURS` (24 by
   default); after that the page says so and **Register again** serves them for another window.
5. **Power it on blank.** It gets its address, downloads its files and starts with SSH and
   NETCONF on. Within 30 s of SSH answering, its host keys appear on its device page.
6. **Approve**: compare the fingerprints with `display rsa local-key-pair public` on its
   console, tick, approve (approver). The keys are fetched again and must be the same; the switch
   is pinned and activated, and its files are deleted.
7. **Stop DHCP/TFTP** when no switch is waiting: `sudo docker compose stop ztp`.

The first-boot config holds the platform passwords in clear (TFTP on the management
network): see the [security model](security-model.md). It exists from registration until approval.

## Server install

One server, no high availability.

**Server.** Linux with Docker Engine and its Compose plugin, installed from Docker's own
packages 
(snap Docker needs `</dev/null` on every `exec -T`). `sudo docker stats --no-stream` shows what
each container uses.
Disk: Prometheus keeps 15 days (about 15 MB a day per switch at 5 s counters), backups 14
days of NetBox and platform dumps.

**Two interfaces .** One for people (the GUI); one on the switches'
management network, through which the server reaches the switches (SSH 22 for Annet, NETCONF
830 for the collector, route view and imports) and serves ZTP (DHCP and TFTP). If the
switches' management addresses are not all on that interface's subnet, add a static route to
them through it. The GUI is published on every interface, port 8000 (design rule); Docker's
published ports bypass host firewalls such as ufw, so filter in front of the server if needed.

### Prepare the server

```sh
git clone <repo> diffplane && cd diffplane
```

Make `deploy/.env` with `python3 gen_env.py` (or copy an existing install's; `NETBOX_TOKEN`
must then stay with its NetBox database), `chmod 600` it, then set:

```sh
ORCHESTRATOR_BIND=0.0.0.0
TLS_HOSTNAMES=<server name>,<people-side address>,<switch-side address>,localhost,127.0.0.1,orchestrator
ZTP_DHCP_INTERFACE=<switch-side interface>     # only if ZTP is used
ZTP_DHCP_NETWORK=<its network>
ZTP_SERVER_ADDRESS=<switch-side address>
```

Start it:

```sh
cd deploy
sudo docker compose up -d --build
sudo docker compose ps                         # all healthy
```

To move an existing install, take a backup there (`docker compose exec backup /backup.sh once`)
and restore it here as under **Backups, Restore**. Never run two installs against the same
switches.


## High availability

Two servers and a witness: **Patroni** keeps
Postgres (NetBox's and the platform's databases) on both servers, synchronous while both are up and
asynchronous when one is down; **etcd** on all three machines is Patroni's quorum; **keepalived** puts
the VIP on the server whose Patroni is the leader; the **ha-agent** starts NetBox, Redis, the
orchestrator, the collector and flows there, and stops them on the other server. ClickHouse
replicates the flows (Keeper on the three machines), Prometheus runs on both servers and scrapes
through the VIP, the two Alertmanagers form a cluster. Every command below goes through
`deploy/ha/fabric-ha`, which renders this machine's configuration first (`deploy/ha/render.py`).

Never let two installs manage the same switch.

### Machines

Ubuntu 24.04 LTS on all three, on one subnet, static addresses, clocks synchronised
(`timedatectl`: `System clock synchronized: yes`), and one free address for the VIP (check it moves,
see below). Sizes: servers 4 vCPU / 8 GB / 80 GB SSD, witness 2 vCPU / 2 GB / 20 GB SSD.

On each, Docker from Docker's repository, and the repository from git:

```sh
sudo apt-get update && sudo apt-get install -y ca-certificates curl git python3
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER                  # then log out and in again
git clone <repo> ~/diffplane
```

**The VIP moves** (before anything else; on server1, then server2, `ens18` and the address yours):

```sh
sudo ip addr add <VIP>/<prefix> dev ens18 && sudo arping -c 3 -U -I ens18 <VIP>   # apt install iputils-arping
# from your laptop: ping <VIP> answers
sudo ip addr del <VIP>/<prefix> dev ens18
```

**Ports between the three machines** (if a firewall is on, allow them from the other two only):
2379-2380 etcd; 5432 Postgres and 8008 Patroni (servers); 9000, 9009, 9181, 9234 ClickHouse and
Keeper; 9093-9094 Alertmanager (servers); VRRP (IP protocol 112) between the servers. From people:
8000 on the VIP. Docker's published ports bypass ufw: filter in front of the machines if needed.

### Configuration

On **server1**, once:

```sh
cd ~/diffplane/deploy
python3 gen_env.py && python3 gen_env.py --ha    # .env with every secret, then the cluster's
```

Then, in that `.env`:

```sh
ORCHESTRATOR_BIND=0.0.0.0
TLS_HOSTNAMES=<VIP>,<VIP's DNS name if any>,localhost,127.0.0.1,orchestrator
DEPLOY_ALLOWED_SITES=                            # deploys off until an admin turns them on
```

Copy that **same** `.env` to `deploy/.env` on server2 and the witness (`scp`, then `chmod 600`).
On each of the three, `deploy/ha/node.env` from `deploy/ha/node.env.example`: the same addresses
everywhere, `HA_NODE` = `server1`, `server2` or `witness`. `deploy/ha/fabric-ha config -q` on a
server (or `fabric-ha config -q` on the witness) checks everything renders.

### Southbound VIP (optional)

When the switches' management network is not the servers' network, each server gets a **second NIC**
there and the active server holds a second VIP on it, together with the first one (one VRRP
instance: they never split). Everything the platform sends to the switches (SSH, NETCONF, host-key
fetches) leaves from that VIP; the switches send NetStream and reach ZTP on it.

1. **Hypervisor:** add a second network device to server1 and server2 on the switches' bridge or VLAN;
   its IP filter off or the southbound VIP allowed (as for the first VIP).
2. **Netplan**, on each server: the second NIC with the server's own address there and a static
   route to each switch management subnet through that network's gateway (no default route), then
   `sudo netplan apply`; `ip route get <a switch's MEth address>` names the second NIC.
3. **`deploy/ha/node.env`**, the same on both servers: `HA_SB_INTERFACE` (the second NIC),
   `HA_SB_VIP` (a free address in its subnet), `HA_SB_PREFIX`.
4. On both servers: `deploy/ha/fabric-ha build keepalived && deploy/ha/fabric-ha up -d
   --force-recreate keepalived`.
5. In the GUI: Fabric › NetStream collector = the southbound VIP, port 2055; Fabric › Syslog receiver =
   the southbound VIP, port 514; for ZTP, Admin ›
   Settings: DHCP interface = the second NIC, this server's address on it = the southbound VIP.

Checks, on the active server (the standby shows neither):

```sh
ip -4 addr show <second NIC>                         # its own address and the southbound VIP
sudo iptables -t nat -S POSTROUTING | grep SNAT      # -o <second NIC> -j SNAT --to-source <VIP>
deploy/ha/fabric-ha logs keepalived | grep southbound   # "traffic out of ... now leaves from ..."
```

From a test host in the southbound network, `sudo tcpdump -ni any tcp port 22` while the platform
fetches a switch's host keys (or `fabric-ha exec orchestrator python -c "import socket;
socket.create_connection(('<test host>', 22), 3)"`) must show the southbound VIP as the source; and
`echo x | nc -u -w1 <southbound VIP> 2055` from it must reach the active server
(`sudo tcpdump -ni <second NIC> udp port 2055`). Repeat both after a failover and a switchover.

### Start

In this order, each from `~/diffplane`:

```sh
deploy/ha/fabric-ha up -d                        # 1. witness: etcd, Keeper
deploy/ha/fabric-ha build && deploy/ha/fabric-ha up -d    # 2. server1  3. server2 (builds take a while)
deploy/ha/fabric-ha patronictl list              # server1 Leader, server2 Sync Standby, streaming
deploy/ha/fabric-ha logs ha-agent                # server1: "the Patroni leader: starting ..."
ip -br addr | grep <VIP>                         # on server1 only
```

On the very first start, server1's `up -d` ends with "container fabric-patroni-1 is unhealthy" and
Patroni logs `Detected Etcd version 3.0.0` / `v3alpha: 404` until server2's etcd has joined: etcd
decides its cluster version only once all three members have answered, and Patroni needs that
version. That is expected: start server2, then run `deploy/ha/fabric-ha up -d` on server1 again.

**The certificate (once).** The first active orchestrator makes it in `deploy/ha/tls/` (with
`tls-public/`; both owned by the orchestrator's user, `nobody`). Copy both folders to the other
server before it ever becomes leader, so both serve the same certificate and both Prometheus trust
it. On the leader, then on the other server:

```sh
sudo tar -C deploy/ha -czpf /tmp/tls.tgz tls tls-public && sudo chown $USER /tmp/tls.tgz
scp /tmp/tls.tgz <other server>:/tmp/ && rm /tmp/tls.tgz
```

```sh
sudo tar -C deploy/ha -xzpf /tmp/tls.tgz && rm /tmp/tls.tgz    # the key stays 0600, owner nobody
deploy/ha/fabric-ha restart prometheus
```

The archive holds the private key: delete it on both sides as above. It lasts 825 days; after a
renewal, copy again.

The same folder holds the image server's SFTP host key (`sftp_host_key`), made by the first
orchestrator. The switches accept only that key, so copy the folders as above (the leader's, after its orchestrator has started). A server whose
orchestrator never had the folder makes a key of its own, and every switch's image download from it
then fails until the leader's folder is copied.
Check: `sudo ssh-keygen -lf deploy/ha/tls-public/sftp_host_key.pub` prints the same fingerprint on both
servers.

**First-time setup** at `https://<VIP>:8000/`: the setup code is in `deploy/ha/fabric-ha logs
orchestrator` on the leader; first admin; skip the switch credentials and the first fabric. Then the
fabrics, all planned: `deploy/ha/fabric-ha exec -T orchestrator python -m seed seed/fabrics/example.yaml`.

**Checks:**

```sh
deploy/ha/fabric-ha patronictl list                                       # both streaming, lag 0
deploy/ha/fabric-ha exec etcd etcdctl endpoint health --cluster           # three healthy
deploy/ha/fabric-ha exec clickhouse clickhouse-client --user flows --password "$CLICKHOUSE_PASSWORD" \
  -q "select replica_name, is_readonly, absolute_delay from system.replicas"   # once flows created its tables
deploy/ha/fabric-ha exec alertmanager amtool cluster show --alertmanager.url=http://localhost:9093
```

### Failover drill

1. Before: note the users, the fabrics' segments, the audit count (Changes › Audit) and the deploy history.
2. **Power off the leader** (from the hypervisor, not a clean shutdown). Within about a minute:
   `patronictl list` on the other server shows it Leader, its ha-agent starts the active services,
   the VIP is there and `https://<VIP>:8000/` answers with the same users, intent and history (the
   browser keeps the certificate if it was copied). The agreed time is 15 minutes.
3. Make a change (a segment), so the old leader has something to catch up.
4. **Power the old leader on**: it rejoins as Sync Standby (`patronictl list`), its ha-agent stops
   its active services, the change is there.
5. **Planned switchover and back**: `deploy/ha/fabric-ha switchover --candidate server1 --force`,
   check, then back to the other. Each time the VIP and the services follow.
6. **The witness off**: the servers keep working (two of three etcd members); a server off at the
   same time would leave no quorum and Patroni demotes the leader, by design (no split brain).

### Restore drill (on the cluster)

Backups run on both servers (`deploy/backups/`, root's). To restore one into the cluster, on the
leader, from `~/diffplane` (`fabric-ha exec backup /backup.sh once` takes one now):

```sh
B=deploy/backups/<folder>
deploy/ha/fabric-ha stop ha-agent
deploy/ha/fabric-ha stop redis netbox netbox-worker orchestrator collector flows   # never a bare `stop`: it stops Patroni too
deploy/ha/fabric-ha exec -T patroni sh -c 'dropdb -U postgres netbox && createdb -U postgres -O netbox netbox'
sudo cat $B/netbox.dump | deploy/ha/fabric-ha exec -T patroni pg_restore -U postgres -d netbox --no-owner --role=netbox
deploy/ha/fabric-ha exec -T patroni sh -c 'dropdb -U postgres platform && createdb -U postgres -O platform platform'
sudo cat $B/platform.dump | deploy/ha/fabric-ha exec -T patroni pg_restore -U postgres -d platform --no-owner --role=platform
deploy/ha/fabric-ha start ha-agent               # restarts the active services within seconds
```

The standby replicates the restore by itself. Compare row counts as under **Backups, Restore** (the
same queries through `fabric-ha exec -T patroni psql -U postgres -d netbox|platform`).

### Updates

Push to git, then on all three: `git pull`, and on each server `deploy/ha/fabric-ha build &&
deploy/ha/fabric-ha up -d` (the standby first, then the leader). The agent recreates the leader's
active services with the new images within 30 seconds; the standby's stay stopped.
Never build on the leader while a deploy is approved or rolling out: the recreated orchestrator
fails the job on the switch it was deploying (the next deploy finishes it). Check Changes first.
`fabric-ha build` also pulls the base images, so a newer `postgres:17-alpine` gives a new Patroni
image and `up -d` recreates Patroni: on the leader that is a failover (the standby takes the
leader, both VIPs and the active services within a minute; the old leader rejoins as Sync Standby). Expect it, or
`fabric-ha switchover` to the updated standby before running `up -d` on the leader.
When an update changes the list of active services (`ha/agent.sh`), also run
`deploy/ha/fabric-ha restart ha-agent` on each server: the agent reads that list when it starts.

## Known operational notes

- After a failed deploy, check `docker compose logs orchestrator`; if later Annet runs
  hang, restart the orchestrator (`docker compose restart orchestrator`).
- A failed deploy's error ends with the last lines of gnetcli's transcript (the command the
  switch rejected). Transcripts live in the orchestrator container under
  `/tmp/annet-deploy-logs` and contain secrets (DFS key, SNMP community) in plain text;
  they are lost when the container is recreated.
- Generators, the rulebook and the seed are mounted into the container: changes apply to the
  next Annet run without a rebuild. API and GUI changes need `docker compose up --build -d`.
- If `docker compose up` stops with `dependency failed to start: container fabric-netbox-1
  exited (143)` and the NetBox log ends with `⚠ Could not load Unit configuration`, its web
  server did not come up in a container that had been restarted rather than recreated
  Recreate it, which keeps all
  data (it lives in the `netbox-postgres` and media volumes):
  `docker compose up -d --force-recreate netbox`, then `docker compose up -d`.
- Load on a switch's management port stalls the collector. On a lab switch,
  `ping -s 1400 -i 0.01` to MEth0/0/0 (100 packets/s) was followed by a NETCONF poll timing
  out and port 830 refusing connections for about 90 s (2 failed polls, 1 reconnect). The
  likely cause is VRP's protection of traffic to the CPU; this is inferred, not verified. For a
  traffic test, keep the load small and short, and expect polls to be lost while it runs.

## Flows

NetStream is turned on per fabric from the Fabric page (collector address and UDP port); the switches
then export 1-in-1024 samples over MEth in `_management_vpn_`. The `flows` service (GoFlow2 inside)
receives them and writes to the `clickhouse` service: raw rows 7 days, per minute 90 days, per hour
by segment pair 13 months.

- `deploy/.env` needs `CLICKHOUSE_PASSWORD` (gen_env.py makes one for a new install; for an existing
  one: `echo "CLICKHOUSE_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >> deploy/.env`).
- `FLOWS_BIND` is this host's address that the Fabric page names as the collector; localhost by default, which no switch can reach.
- Is anything arriving? `sudo tcpdump -ni any udp port 2055 -c 5` on the host, and
  `sudo docker compose logs flows --since 10m` (stored and dropped counts every 5 s while flows come in).

## Syslog

Syslog is turned on per fabric from the Fabric page (receiver address and UDP port, 514 by default);
at their next deploy the switches send **warnings and worse** over MEth in `_management_vpn_`, from
their MEth address, with one line the platform owns:
`info-center loghost <receiver> vpn-instance _management_vpn_ source-ip <MEth> level warning [port <p>]`.
Any other loghost on a switch (a SIEM, say) is left alone. The `syslog` service keeps the messages of
switches the platform knows (by the datagram's source address) in ClickHouse for 90 days; each
switch's **Logs** tab shows them.

- `SYSLOG_BIND` is this host's address that the Fabric page names as the receiver, `SYSLOG_PORT` that page's port; localhost and 514 by default.
  `sudo docker compose up -d syslog` after changing them.
- HA: the receiver is the southbound VIP, and only the active server runs `syslog`; the
  port listens on every address there.
- Is anything arriving? `sudo tcpdump -ni any udp port 514 -c 5` on the host, and
  `sudo docker compose logs syslog --since 10m` (stored and dropped counts while messages come in;
  "dropped" means an unknown sender or not syslog).

## Switch login over RADIUS

Fabric › Switch login: people log in as `name@<domain>`; the default domain, with the platform's accounts, stays local-only. The RADIUS server must accept the name with its domain: FreeRADIUS's default `filter_username` rejects `user@radius` (a realm without a dot), so turn that check off or use a dotted domain handling. It answers `Huawei-Exec-Privilege` (3 for `system-view`); no attribute gives the lowest level. Requests come from each switch's MEth address through `_management_vpn_` (ports 1812 and 1813): add those addresses as clients. A deploy with these lines goes one switch at a time under `commit trial 300` and is taken back if the platform cannot log in again.

## Switch software upgrades

Switches pull system software and patches from the platform's own SFTP server (`sftpd`) over MEth
in `_management_vpn_`, one switch at a time, each switch's plan approved like a patch.

- **Setup:** `SFTP_BIND` is this host's address the switches reach, `SFTP_PORT` its port (2222); Settings › Switch software names the address
  the switches are told (`SFTP_ADDRESS`). Every switch gets the server's host key at its next
  deploy (generator `SftpServerKey`); a switch without it refuses the download. HA: the southbound
  VIP, images re-uploaded on the new active server if missing there.
- **Images:** Switches › Software, admins only. Upload the `.cc` and the `.PAT`, typing Huawei's
  SHA-256 when you have it (a mismatch is refused). A file name is what lands on flash: the same
  file name as the running one means "nothing to do".
- **Request:** pick the system software, its patch and the switches (in order). The plan per
  switch lists the old images it deletes (neither running nor next startup), the downloads, the
  digest checks, the startups and the reboot; approval confirms the reboots may happen now.
- **Rollout:** a copy already on flash with the right size is only digest-checked; the download
  bar shows what the image server has sent (the switch prints nothing until the file is
  complete; over a slow link a 590 MB `.cc` can take half an hour). The M-LAG peer
  must be up with peer-link and heartbeat before and again right before its partner reboots.
  The reboot saves the configuration; the job waits up to 10 minutes for SSH to go down and 30 to
  come back, then checks release, patch, an empty diff, a collector poll and links.
- **Deploys** queue while an upgrade rolls out (one rollout at a time); intent is frozen.
- **A failed job** never reboots: the switch keeps running what it ran. Check `display startup`:
  a startup already set stays set (it takes effect at the next reboot, by hand or the next job).
  A restart of the orchestrator fails a running upgrade job; request it again.

