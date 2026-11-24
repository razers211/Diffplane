# Onboarding an existing fabric

The path from a running fabric to one Diffplane deploys to, in three stages: **import**, then
**watch it diff-only for a week**, then **turn deploys on**. Nothing is sent to a switch before
the last stage, and an admin decides when that starts. The steps link to
[Operations](operations.md) for the details.

Before you start, check the [supported switches](supported-switches.md): anything not listed
there is unverified. Multi-switch behaviour (M-LAG negotiation, EVPN between VTEPs, routing
between leaves) has not yet been verified on real hardware, so keep the first deploys small and
watched.

## 1. Import (an hour)

Follow [Importing an existing fabric](operations.md#importing-an-existing-fabric):
addresses, host keys compared with each console and approved, the switches read with `display`
commands only, then the preview.

Read the preview's **report** before you import. It has one card per switch, those needing a
look first:

- **would change**: the patch the switch's first deploy would send. An empty patch (*reproduced
  exactly*) is the goal. A non-empty one means the platform would make this switch differ from
  how it runs today: find out why before going on.
- **kept as found**: values the switch uses that differ from what Diffplane would derive (a
  DFS group number, timers, a VPN instance name...). They stay as they are; each can be *made
  standard* later, with its patch shown first.
- **not managed**: what Diffplane's model has no place for. It is left alone, except where the
  line says the first deploy removes it (a bridge domain you chose to drop, for example).

The card at the top covers the whole fabric (fabric-wide values, segments, services).

**Done when** every patch is empty or you have agreed, in writing, to each change it makes.
Then **Import**: the fabric is written to NetBox with its switches active and pinned, and
deploys to it stay off.

## 2. Diff-only, for a week

Diffplane now reads the fabric but changes nothing. Leave it like this for about a week, over
the fabric's normal changes, and check:

- **Drift** ([Drift](operations.md#drift)): every switch is checked every 15 minutes. Changes ›
  Pending should stay empty. A switch that starts to differ means either someone changed it by
  hand (expected sometimes: decide whether intent or the switch is right) or a generator does
  not reproduce something it should (report it, with the switch's lines). The alert
  `FabricDeviceOutOfSync` fires after 5 minutes of difference, so you hear about it.
- **Hand-made routing** (static routes, BGP peers, policies): it is imported into intent
  automatically, never removed. Network › Routing says, per switch, what was adopted and what
  was left unmanaged.
- **The read path**: every switch polled (Switches page), links up where they are cabled, the
  health score on Overview › Health, BGP and OSPF peers on each switch's Control plane tab. A
  problem here is a monitoring problem, not a config one, and it is worth fixing before deploys:
  a deploy is verified with these same reads.
- **Config backups** ([Config backups](operations.md#config-backups)): one version per switch a
  day. They are your record of how each switch looked before Diffplane ever changed it.
- **Found on import** (Fabric page): what the fabric keeps as found, and what is not managed,
  as in the report. Nothing needs doing here yet.

**Done when** a week has passed with no unexplained drift and every switch polled.

## 3. Turn deploys on

1. **An admin turns deploys on** for this fabric (Fabric page, *Deploys to <fabric>*, **Allow
   deploys…**, the fabric's name typed to confirm). Every deploy still needs a patch, an approval and a
   verification of each switch.
2. **Make the first change small**, one you can check by eye on the switches it touches
   (a segment attached to one free port, for example). Read every line of the patch before
   approving. Approve it **in a change window** if the fabric carries production traffic
   (the approve step offers Now or In a change window).
3. **Watch the rollout.** It runs in waves that never take both switches of an M-LAG pair at
   once, and each switch is verified (empty diff, polled again, no fabric link lost) before the
   next wave starts. A failure stops new waves and names the switch.
4. **Then larger changes**, one concern at a time, and only then *make standard* the values
   kept as found, each with its own patch and deploy, where you want the fabric to take
   Diffplane's usual form.

To go back to diff-only at any point, an admin clicks **Turn deploys off** on the Fabric page;
deploys already prepared are refused at approval.
