"""HA cluster files (design): rendered configs, the compose roles,
the agent that starts the active services only on the Patroni leader, and the shared secrets."""

import importlib.util
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
HA = DEPLOY / "ha"
NODE = {"HA_NODE": "server1", "HA_SERVER1_IP": "10.0.0.11", "HA_SERVER2_IP": "10.0.0.12",
        "HA_WITNESS_IP": "10.0.0.13", "HA_VIP": "10.0.0.50", "HA_VIP_PREFIX": "24", "HA_INTERFACE": "ens18"}
SECRETS = {"PATRONI_SUPERUSER_PASSWORD": "su", "PATRONI_REPLICATION_PASSWORD": "rep", "VRRP_PASSWORD": "abcdefgh12",
           "ETCD_TOKEN": "fabric-x", "CLICKHOUSE_PASSWORD": "ch"}


def module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    found = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(found)
    return found


render = module(HA / "render.py", "ha_render")
BASE_PROMETHEUS = (DEPLOY / "prometheus" / "prometheus.yml").read_text()


def files(**node):
    return render.render({**NODE, **node}, SECRETS, BASE_PROMETHEUS)


def test_a_server_gets_patroni_keepalived_prometheus_and_clickhouse():
    out = files()
    assert set(out) == {"ha.env", "patroni.yml", "keepalived.conf", "prometheus.yml", "clickhouse-ha.xml"}
    env = dict(line.split("=", 1) for line in out["ha.env"].splitlines())
    assert (env["HA_NODE_IP"], env["HA_PEER"], env["HA_PEER_IP"], env["HA_KEEPER_ID"]) == ("10.0.0.11", "server2", "10.0.0.12", "1")
    assert env["HA_ETCD_CLUSTER"] == "server1=http://10.0.0.11:2380,server2=http://10.0.0.12:2380,witness=http://10.0.0.13:2380"
    patroni = yaml.safe_load(out["patroni.yml"])
    dcs = patroni["bootstrap"]["dcs"]
    assert (dcs["synchronous_mode"], dcs["synchronous_mode_strict"]) == (True, False)   # sync, async if alone
    assert dcs["postgresql"]["parameters"]["jit"] == "off"
    assert patroni["etcd3"]["hosts"] == "10.0.0.11:2379,10.0.0.12:2379,10.0.0.13:2379"
    assert patroni["postgresql"]["connect_address"] == "10.0.0.11:5432"
    assert patroni["postgresql"]["basebackup"] == [{"checkpoint": "fast"}]


def test_the_vip_follows_the_patroni_leader_over_unicast_vrrp():
    conf = files(HA_NODE="server2")["keepalived.conf"]
    assert "http://127.0.0.1:8008/primary" in conf and "track_script" in conf
    assert "unicast_src_ip 10.0.0.12" in conf and "    10.0.0.11\n" in conf
    assert "10.0.0.50/24 dev ens18" in conf and "auth_pass abcdefgh\n" in conf   # keepalived keeps 8 characters


def test_prometheus_scrapes_through_the_vip_and_alerts_both_alertmanagers():
    prom = yaml.safe_load(files()["prometheus.yml"])
    jobs = {j["job_name"]: j for j in prom["scrape_configs"]}
    assert jobs["collector"]["static_configs"][0]["targets"] == ["10.0.0.50:9108"]
    assert jobs["orchestrator"]["static_configs"][0]["targets"] == ["10.0.0.50:8000"]
    assert jobs["orchestrator"]["tls_config"]["server_name"] == "10.0.0.50"
    assert prom["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["10.0.0.11:9093", "10.0.0.12:9093"]


def test_clickhouse_is_a_replica_of_cluster_fabric_with_keeper_on_three_machines():
    xml = files(HA_NODE="server2")["clickhouse-ha.xml"]
    assert "<replica>server2</replica>" in xml and "<server_id>2</server_id>" in xml
    assert xml.count("<port>9234</port>") == 3 and xml.count("<port>9181</port>") == 3   # raft peers, Keeper clients
    assert "<tcp_port>9181</tcp_port>" in xml


SB = {"HA_SB_INTERFACE": "ens19", "HA_SB_VIP": "10.9.0.50", "HA_SB_PREFIX": "24"}


def test_the_southbound_vip_moves_with_the_vip_and_snats_the_active_servers_traffic():
    conf = files(**SB)["keepalived.conf"]
    assert "10.0.0.50/24 dev ens18\n    10.9.0.50/24 dev ens19\n" in conf   # one instance: never split
    assert 'notify_master "/usr/local/bin/southbound.sh up 10.9.0.50 ens19"' in conf
    for state in ("backup", "fault", "stop"):
        assert f'notify_{state} "/usr/local/bin/southbound.sh down 10.9.0.50 ens19"' in conf
    assert "notify" not in files()["keepalived.conf"]   # no second NIC: as before
    assert "HA_SB_VIP=10.9.0.50" in files(**SB)["ha.env"]


@pytest.mark.parametrize("change, reason", [
    ({"HA_SB_PREFIX": ""}, "set all of"),
    ({"HA_SB_VIP": "10.0.0.12"}, "must differ"),
    ({"HA_SB_VIP": "10.0.0.60"}, "outside the VIP's subnet"),
    ({"HA_SB_INTERFACE": "ens18"}, "second NIC"),
])
def test_a_wrong_southbound_vip_is_refused(change, reason):
    with pytest.raises(render.HaError, match=reason):
        files(**{**SB, **change})


def fake_iptables(tmp_path: Path, present: int) -> dict:
    """`iptables` that answers -C true `present` times, recording every call."""
    bin_dir = tmp_path / "ipt"
    bin_dir.mkdir()
    (tmp_path / "left").write_text(str(present))
    (bin_dir / "iptables").write_text(f"""#!/bin/sh
echo "$@" >> {tmp_path}/calls
if [ "$3" = -C ]; then n=$(cat {tmp_path}/left); [ "$n" -gt 0 ] || exit 1; echo $((n - 1)) > {tmp_path}/left; fi
exit 0
""")
    (bin_dir / "iptables").chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


@pytest.mark.parametrize("action, present, expected", [
    ("up", 0, ["-t nat -C", "-t nat -I"]),
    ("up", 2, ["-t nat -C", "-t nat -D", "-t nat -C", "-t nat -D", "-t nat -C", "-t nat -I"]),   # never twice
    ("down", 1, ["-t nat -C", "-t nat -D", "-t nat -C"]),
])
def test_the_snat_rule_is_added_once_on_master_and_removed_otherwise(tmp_path, action, present, expected):
    script = HA / "keepalived" / "southbound.sh"
    subprocess.run(["sh", str(script), action, "10.9.0.50", "ens19"], env=fake_iptables(tmp_path, present),
                   check=True, timeout=10, capture_output=True)
    calls = (tmp_path / "calls").read_text().splitlines()
    assert [" ".join(c.split()[:3]) for c in calls] == expected
    assert all(c.endswith("POSTROUTING -o ens19 -j SNAT --to-source 10.9.0.50") for c in calls)


def test_the_witness_gets_only_keeper():
    out = files(HA_NODE="witness")
    assert set(out) == {"ha.env", "keeper.xml"} and "<server_id>3</server_id>" in out["keeper.xml"]


@pytest.mark.parametrize("change, reason", [
    ({"HA_NODE": "server3"}, "one of server1, server2, witness"),
    ({"HA_VIP": ""}, "missing: HA_VIP"),
    ({"HA_VIP": "10.0.0.11"}, "four different addresses"),
    ({"HA_SERVER2_IP": "10.9.0.12"}, "in the VIP's subnet"),
])
def test_a_wrong_node_file_is_refused(change, reason):
    with pytest.raises(render.HaError, match=reason):
        files(**change)


class ComposeLoader(yaml.SafeLoader):
    """Compose's own merge tags (!override, !reset), which plain YAML does not know."""


ComposeLoader.add_constructor("!override", lambda loader, node: loader.construct_mapping(node) if isinstance(node, yaml.MappingNode) else loader.construct_sequence(node))
ComposeLoader.add_constructor("!reset", lambda loader, node: [] if isinstance(node, yaml.SequenceNode) else {})


def compose(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=ComposeLoader)


def test_only_the_active_services_follow_the_leader_and_images_are_pinned():
    services = compose(HA / "server.yaml")["services"]
    active = {n for n, s in services.items() if "active" in (s.get("profiles") or [])}
    agent = (HA / "agent.sh").read_text()
    assert active == {"redis", "netbox", "netbox-worker", "orchestrator", "collector", "flows", "syslog", "sftpd", "ztp"}
    assert f'ACTIVE="{" ".join(["redis", "netbox", "netbox-worker", "orchestrator", "collector", "flows", "syslog", "sftpd", "ztp"])}"' in agent
    assert services["netbox-postgres"]["profiles"] == ["single"] and services["postgres"]["profiles"] == ["single"]
    assert services["keepalived"]["network_mode"] == "host"
    for doc in (compose(HA / "server.yaml"), compose(HA / "witness.yaml")):
        for name, svc in doc["services"].items():
            if "image" not in svc:
                continue   # an override of compose.yaml's service (its image is pinned there)
            image = svc["image"]
            assert ":" in image and not image.endswith(":latest"), (name, image)


def fake_bin(tmp_path: Path, wget_ok: bool) -> dict:
    """`docker` that records its arguments, `wget` that answers as a leader or a replica."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f'#!/bin/sh\necho "$@" >> {tmp_path}/docker.log\n')
    (bin_dir / "wget").write_text(f"#!/bin/sh\nexit {0 if wget_ok else 1}\n")
    for f in bin_dir.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HA_ONCE": "1", "HA_INTERVAL": "0"}


@pytest.mark.parametrize("leader, expected", [
    (True, "--profile active up -d --no-build --pull never redis netbox netbox-worker orchestrator collector flows syslog sftpd ztp"),
    (False, "--profile active stop redis netbox netbox-worker orchestrator collector flows syslog sftpd ztp"),
])
def test_the_agent_starts_the_active_services_only_on_the_leader(tmp_path, leader, expected):
    done = subprocess.run(["sh", str(HA / "agent.sh")], env=fake_bin(tmp_path, leader), check=True, timeout=20,
                          capture_output=True, text=True)
    assert done.stdout.strip().endswith(f"applied={'leader' if leader else 'replica'}")
    calls = (tmp_path / "docker.log").read_text().splitlines()
    assert len(calls) == 1 and calls[0].endswith(expected)
    assert calls[0].startswith("compose --env-file .env --env-file ha/out/ha.env -f compose.yaml -f ha/server.yaml")


def test_gen_env_adds_the_cluster_secrets_once():
    gen = module(DEPLOY / "gen_env.py", "gen_env_ha")
    once = gen.add_ha("A=1\n")
    keys = [line.split("=")[0] for line in once.splitlines() if "=" in line]
    assert keys[1:] == ["PATRONI_SUPERUSER_PASSWORD", "PATRONI_REPLICATION_PASSWORD", "VRRP_PASSWORD", "ETCD_TOKEN"]
    assert gen.add_ha(once) == once
