"""Render the HA configuration of one machine (design).

Reads deploy/.env (the shared secrets, the same file on all three machines) and deploy/ha/node.env
(this machine: its name and the cluster's addresses), writes deploy/ha/out/:

    ha.env              variables the compose files use (this node, its peer, the etcd cluster)
    patroni.yml         servers: Patroni (etcd3, synchronous replication falling back to async)
    keepalived.conf     servers: the VIP, held only where the local Patroni is the leader
    prometheus.yml      servers: scrapes through the VIP, alerts to both Alertmanagers
    clickhouse-ha.xml   servers: replica macros, cluster `fabric`, embedded Keeper, Keeper client
    keeper.xml          witness: standalone ClickHouse Keeper (the third member)

Standard library only: it runs with the host's python3 before anything is started.
"""

import ipaddress
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODES = ("server1", "server2", "witness")
SECRETS = ("PATRONI_SUPERUSER_PASSWORD", "PATRONI_REPLICATION_PASSWORD", "VRRP_PASSWORD", "ETCD_TOKEN",
           "CLICKHOUSE_PASSWORD")
REQUIRED = ("HA_NODE", "HA_SERVER1_IP", "HA_SERVER2_IP", "HA_WITNESS_IP", "HA_VIP", "HA_VIP_PREFIX", "HA_INTERFACE")
# the southbound VIP on the servers' second NIC, towards the switches. All or none.
SOUTHBOUND = ("HA_SB_INTERFACE", "HA_SB_VIP", "HA_SB_PREFIX")


class HaError(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    found = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            found[key.strip()] = value.strip()
    return found


def check(node: dict, secrets: dict) -> None:
    missing = [k for k in REQUIRED if not node.get(k)] + [k for k in SECRETS if not secrets.get(k)]
    if missing:
        raise HaError(f"missing: {', '.join(missing)} (ha/node.env, or run gen_env.py --ha for .env)")
    if node["HA_NODE"] not in NODES:
        raise HaError(f"HA_NODE {node['HA_NODE']!r}: one of {', '.join(NODES)}")
    ips = [node[f"HA_{n.upper()}_IP"] for n in NODES] + [node["HA_VIP"]]
    try:
        addresses = [ipaddress.IPv4Address(ip) for ip in ips]
        subnet = ipaddress.IPv4Interface(f"{node['HA_VIP']}/{node['HA_VIP_PREFIX']}").network
    except ValueError as exc:
        raise HaError(str(exc)) from exc
    if len(set(addresses)) != 4:
        raise HaError("the three machines and the VIP need four different addresses")
    if not all(a in subnet for a in addresses[:2]):
        raise HaError(f"server1 and server2 must be in the VIP's subnet {subnet}")
    given = [k for k in SOUTHBOUND if node.get(k)]
    if given and len(given) != len(SOUTHBOUND):
        raise HaError(f"southbound VIP: set all of {', '.join(SOUTHBOUND)} or none")
    if given:
        try:
            sb = ipaddress.IPv4Interface(f"{node['HA_SB_VIP']}/{node['HA_SB_PREFIX']}")
        except ValueError as exc:
            raise HaError(f"southbound VIP: {exc}") from exc
        if sb.ip in addresses:
            raise HaError("the southbound VIP must differ from the three machines and the VIP")
        if sb.ip in subnet:
            raise HaError(f"the southbound VIP must be outside the VIP's subnet {subnet} (the second NIC's network)")
        if node["HA_SB_INTERFACE"] == node["HA_INTERFACE"]:
            raise HaError("HA_SB_INTERFACE must be the second NIC, not HA_INTERFACE")


def derived(node: dict) -> dict[str, str]:
    me = node["HA_NODE"]
    ips = {n: node[f"HA_{n.upper()}_IP"] for n in NODES}
    peer = {"server1": "server2", "server2": "server1"}.get(me, "")
    return {
        "HA_NODE": me,
        "HA_NODE_IP": ips[me],
        "HA_PEER": peer,
        "HA_PEER_IP": ips.get(peer, ""),
        "HA_SERVER1_IP": ips["server1"], "HA_SERVER2_IP": ips["server2"], "HA_WITNESS_IP": ips["witness"],
        "HA_VIP": node["HA_VIP"],
        "HA_SB_VIP": node.get("HA_SB_VIP") or "",
        "HA_ETCD_CLUSTER": ",".join(f"{n}=http://{ips[n]}:2380" for n in NODES),
        "HA_KEEPER_ID": str(NODES.index(me) + 1),
        # The repository's absolute path on this machine: the agent drives compose from it.
        "HA_REPO": str(HERE.parents[1]),
    }


def patroni(d: dict, secrets: dict) -> str:
    me, ip = d["HA_NODE"], d["HA_NODE_IP"]
    etcd = ",".join(f"{d[f'HA_{n.upper()}_IP']}:2379" for n in NODES)
    return f"""# Rendered by deploy/ha/render.py for {me}; do not edit.
scope: fabric
name: {me}
restapi:
  listen: 0.0.0.0:8008
  connect_address: {ip}:8008
etcd3:
  hosts: {etcd}
bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    # Synchronous while both servers are up, asynchronous when the
    # standby is down, so the active never stops taking writes.
    synchronous_mode: true
    synchronous_mode_strict: false
    postgresql:
      use_pg_rewind: true
      parameters:
        jit: "off"          # NetBox's config-context query
        max_connections: 200
        wal_level: replica
        hot_standby: "on"
        max_wal_senders: 10
        max_replication_slots: 10
        wal_log_hints: "on"
  initdb:
    - encoding: UTF8
    - data-checksums
  post_bootstrap: /post_bootstrap.sh
postgresql:
  listen: 0.0.0.0:5432
  connect_address: {ip}:5432
  data_dir: /var/lib/postgresql/data/pgdata
  # pg_basebackup's default spread checkpoint keeps a new replica waiting minutes on an idle leader.
  basebackup:
    - checkpoint: fast
  pg_hba:
    - local all all trust
    - host replication replicator 0.0.0.0/0 scram-sha-256
    - host all all 0.0.0.0/0 scram-sha-256
  authentication:
    superuser:
      username: postgres
      password: "{secrets['PATRONI_SUPERUSER_PASSWORD']}"
    replication:
      username: replicator
      password: "{secrets['PATRONI_REPLICATION_PASSWORD']}"
    rewind:
      username: postgres
      password: "{secrets['PATRONI_SUPERUSER_PASSWORD']}"
tags:
  nofailover: false
  noloadbalance: false
"""


def keepalived(d: dict, node: dict, secrets: dict) -> str:
    vips = f"    {d['HA_VIP']}/{node['HA_VIP_PREFIX']} dev {node['HA_INTERFACE']}"
    notify = ""
    if d["HA_SB_VIP"]:
        # Both VIPs in one instance, so they never split. The active server's traffic to the switches
        # leaves from the southbound VIP (SNAT, keepalived/southbound.sh), the standby's not at all.
        sb_if = node["HA_SB_INTERFACE"]
        vips += f"\n    {d['HA_SB_VIP']}/{node['HA_SB_PREFIX']} dev {sb_if}"
        args = f"{d['HA_SB_VIP']} {sb_if}"
        notify = "".join(f'  notify_{state} "/usr/local/bin/southbound.sh {action} {args}"\n'
                         for state, action in (("master", "up"), ("backup", "down"), ("fault", "down"), ("stop", "down")))
    return f"""# Rendered by deploy/ha/render.py for {d['HA_NODE']}; do not edit.
# The VIP follows Patroni: only the server whose Patroni is the leader passes
# the check, so the database, the active services and the address move together.
global_defs {{
  enable_script_security
  script_user root
}}
vrrp_script chk_patroni_leader {{
  script "/usr/bin/curl -sf -m 2 -o /dev/null http://127.0.0.1:8008/primary"
  interval 2
  fall 2
  rise 2
}}
vrrp_instance fabric {{
  state BACKUP
  interface {node['HA_INTERFACE']}
  virtual_router_id {node.get('HA_VRID') or 51}
  priority 100
  advert_int 1
  unicast_src_ip {d['HA_NODE_IP']}
  unicast_peer {{
    {d['HA_PEER_IP']}
  }}
  authentication {{
    auth_type PASS
    auth_pass {secrets['VRRP_PASSWORD'][:8]}
  }}
  virtual_ipaddress {{
{vips}
  }}
  track_script {{
    chk_patroni_leader
  }}
{notify}}}
"""


def prometheus(d: dict, base: str) -> str:
    """The single-server prometheus.yml with every target through the VIP and both Alertmanagers."""
    vip = d["HA_VIP"]
    out = base.replace('targets: ["alertmanager:9093"]',
                       f'targets: ["{d["HA_SERVER1_IP"]}:9093", "{d["HA_SERVER2_IP"]}:9093"]')
    out = out.replace('targets: ["collector:9108"]', f'targets: ["{vip}:9108"]')
    out = out.replace('targets: ["orchestrator:8000"]', f'targets: ["{vip}:8000"]')
    out = out.replace("server_name: orchestrator", f"server_name: {vip}")
    for gone in ('"alertmanager:9093"', '"collector:9108"', '"orchestrator:8000"'):
        if gone in out:
            raise HaError(f"prometheus.yml: {gone} left (its layout changed: update render.py)")
    return f"# Rendered by deploy/ha/render.py for {d['HA_NODE']}; do not edit.\n" + out


def _keeper_servers(d: dict) -> str:
    return "\n".join(f"""      <server><id>{i}</id><hostname>{n}</hostname><port>9234</port></server>"""
                     for i, n in enumerate(NODES, 1))


def clickhouse(d: dict, secrets: dict) -> str:
    me = d["HA_NODE"]
    keepers = "\n".join(f"    <node><host>{n}</host><port>9181</port></node>" for n in NODES)
    replicas = "\n".join(f"""        <replica><host>{n}</host><port>9000</port><user>flows</user>
          <password>{secrets['CLICKHOUSE_PASSWORD']}</password></replica>""" for n in ("server1", "server2"))
    return f"""<!-- Rendered by deploy/ha/render.py for {me}; do not edit. Flows replicated on both
     servers (ReplicatedMergeTree), Keeper on the three machines (embedded here, standalone on the witness). -->
<clickhouse>
  <macros><shard>1</shard><replica>{me}</replica></macros>
  <interserver_http_host>{me}</interserver_http_host>
  <interserver_http_port>9009</interserver_http_port>
  <listen_host>0.0.0.0</listen_host>
  <remote_servers>
    <fabric>
      <shard>
        <internal_replication>true</internal_replication>
{replicas}
      </shard>
    </fabric>
  </remote_servers>
  <zookeeper>
{keepers}
  </zookeeper>
  <distributed_ddl><path>/clickhouse/task_queue/ddl</path></distributed_ddl>
{keeper_server(d)}
</clickhouse>
"""


def keeper_server(d: dict) -> str:
    return f"""  <keeper_server>
    <tcp_port>9181</tcp_port>
    <server_id>{d['HA_KEEPER_ID']}</server_id>
    <log_storage_path>/var/lib/clickhouse/coordination/log</log_storage_path>
    <snapshot_storage_path>/var/lib/clickhouse/coordination/snapshots</snapshot_storage_path>
    <raft_configuration>
{_keeper_servers(d)}
    </raft_configuration>
  </keeper_server>"""


def keeper(d: dict) -> str:
    return f"""<!-- Rendered by deploy/ha/render.py for {d['HA_NODE']}; do not edit. -->
<clickhouse>
  <listen_host>0.0.0.0</listen_host>
  <logger><level>information</level><console>true</console></logger>
{keeper_server(d)}
</clickhouse>
"""


def render(node: dict, secrets: dict, prometheus_base: str) -> dict[str, str]:
    check(node, secrets)
    d = derived(node)
    files = {"ha.env": "".join(f"{k}={v}\n" for k, v in d.items())}
    if d["HA_NODE"] == "witness":
        files["keeper.xml"] = keeper(d)
    else:
        files.update({"patroni.yml": patroni(d, secrets), "keepalived.conf": keepalived(d, node, secrets),
                      "prometheus.yml": prometheus(d, prometheus_base), "clickhouse-ha.xml": clickhouse(d, secrets)})
    return files


def main() -> int:
    try:
        files = render(read_env(HERE / "node.env"), read_env(HERE.parent / ".env"),
                       (HERE.parent / "prometheus" / "prometheus.yml").read_text())
    except (HaError, FileNotFoundError) as exc:
        print(f"render: {exc}", file=sys.stderr)
        return 1
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    # patroni.yml, keepalived.conf and clickhouse-ha.xml hold passwords: the folder is the owner's
    # alone; the files stay readable so the containers' own users (postgres, clickhouse, nobody) can
    # read them through their bind mounts.
    out.chmod(0o700)
    for name, text in files.items():
        (out / name).write_text(text)
        (out / name).chmod(0o644)
    print(f"rendered {', '.join(sorted(files))} for {files['ha.env'].split()[0].split('=')[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
