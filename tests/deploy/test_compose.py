import importlib.util
import re
from pathlib import Path

import yaml

from orchestrator.netbox import ANNET_SUPPORTED_MINOR_VERSIONS

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def load_compose() -> dict:
    return yaml.safe_load((DEPLOY / "compose.yaml").read_text())


def load_gen_env():
    spec = importlib.util.spec_from_file_location("gen_env", DEPLOY / "gen_env.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_expected_services():
    services = set(load_compose()["services"])

    assert {"netbox", "netbox-worker", "netbox-postgres", "postgres", "redis", "orchestrator",
            "collector", "prometheus", "alertmanager"} <= services


def test_only_orchestrator_publishes_ports_and_only_on_localhost():
    """Localhost unless ORCHESTRATOR_BIND says otherwise (the server: every interface). The
    flows service publishes only its NetStream UDP port, localhost unless FLOWS_BIND names the
    address the switches export to."""
    services = load_compose()["services"]

    published = {name: svc["ports"] for name, svc in services.items() if svc.get("ports")}

    assert set(published) == {"orchestrator", "flows", "syslog", "sftpd"}
    assert all(port.startswith("${ORCHESTRATOR_BIND:-127.0.0.1}:") for port in published["orchestrator"])
    assert published["flows"] == ["${FLOWS_BIND:-127.0.0.1}:${FLOWS_PORT:-2055}:2055/udp"]
    assert published["syslog"] == ["${SYSLOG_BIND:-127.0.0.1}:${SYSLOG_PORT:-514}:5514/udp"]
    assert published["sftpd"] == ["${SFTP_BIND:-127.0.0.1}:${SFTP_PORT:-2222}:2222"]
    example = (DEPLOY / ".env.example").read_text()
    assert "\nORCHESTRATOR_BIND=127.0.0.1\n" in example and "\nFLOWS_BIND=127.0.0.1\n" in example


def test_netbox_image_pinned_to_annet_supported_version():
    image = load_compose()["services"]["netbox"]["image"]

    match = re.fullmatch(r"docker\.io/netboxcommunity/netbox:v(\d+\.\d+)\.\d+-[\d.]+@sha256:[0-9a-f]{64}", image)

    assert match, f"NetBox image must be pinned to an exact version and digest, got {image}"
    assert match.group(1) in ANNET_SUPPORTED_MINOR_VERSIONS


def test_secrets_are_required_not_defaulted():
    text = (DEPLOY / "compose.yaml").read_text()
    secrets = ["NETBOX_DB_PASSWORD", "REDIS_PASSWORD", "NETBOX_SECRET_KEY",
               "NETBOX_SUPERUSER_PASSWORD", "NETBOX_TOKEN", "PLATFORM_DB_PASSWORD"]

    for name in secrets:
        uses = re.findall(r"\$\{" + name + r"([^}]*)\}", text)
        assert uses, f"{name} not used"
        assert all(use.startswith(":?") for use in uses), f"{name} must use ${{{name}:?...}}"


def test_env_example_has_no_secret_values_and_covers_compose():
    example = (DEPLOY / ".env.example").read_text()
    entries = dict(
        line.split("=", 1) for line in example.splitlines() if line and not line.startswith("#")
    )
    used = set(re.findall(r"\$\{([A-Z_]+)", (DEPLOY / "compose.yaml").read_text()))

    assert used <= set(entries)
    assert {k for k, v in entries.items() if v} == {"ORCHESTRATOR_BIND", "ORCHESTRATOR_PORT", "COOKIE_SECURE",
                                                          "DRIFT_INTERVAL_SECONDS", "TLS_HOSTNAMES",
                                                          "FLOWS_BIND", "FLOWS_PORT", "SYSLOG_BIND", "SYSLOG_PORT",
                                                          "SFTP_BIND", "SFTP_PORT"}


def test_gen_env_fills_every_secret():
    gen_env = load_gen_env()

    rendered = gen_env.render((DEPLOY / ".env.example").read_text())
    entries = dict(
        line.split("=", 1) for line in rendered.splitlines() if line and not line.startswith("#")
    )

    assert {k for k, v in entries.items() if not v} == gen_env.MANUAL
    example = dict(line.split("=", 1) for line in (DEPLOY / ".env.example").read_text().splitlines()
                   if line and not line.startswith("#"))
    filled = {k for k, v in entries.items() if v and not example[k]}
    assert all(re.search(r"PASSWORD|TOKEN|SECRET|KEY", k) for k in filled), filled   # never a setting
    assert re.fullmatch(r"[0-9a-f]{40}", entries["NETBOX_TOKEN"])
    assert len(entries["NETBOX_SECRET_KEY"]) >= 50
    assert entries["ORCHESTRATOR_PORT"] == "8000"
    assert "$" not in rendered


def test_gen_env_values_are_random():
    gen_env = load_gen_env()
    template = (DEPLOY / ".env.example").read_text()

    assert gen_env.render(template) != gen_env.render(template)


def test_annet_matches_short_hostnames_exactly():
    env = load_compose()["services"]["orchestrator"]["environment"]

    assert env["NETBOX_EXACT_HOST_FILTER"] == "true"


def test_orchestrator_uses_the_platform_database():
    orchestrator = load_compose()["services"]["orchestrator"]

    assert "@postgres:5432/platform" in orchestrator["environment"]["DATABASE_URL"]
    assert "postgres" in orchestrator["depends_on"]


def test_deploys_are_limited_to_the_lab_by_default():
    env = load_compose()["services"]["orchestrator"]["environment"]

    assert env["DEPLOY_ALLOWED_SITES"] == "${DEPLOY_ALLOWED_SITES:-}"


def test_mounted_code_wins_over_the_installed_copy():
    # `pip install .` copies generators/ and rulebook/ into site-packages; compose mounts the
    # live folders at /app. Every process must import the mounted ones, or a manual
    # `annet diff` runs older generators than the orchestrator's deploys (example).
    dockerfile = (DEPLOY.parent / "orchestrator" / "Dockerfile").read_text()
    assert re.search(r"^\s*PYTHONPATH=/app\b", dockerfile, re.M)
    mounts = load_compose()["services"]["orchestrator"]["volumes"]
    for package in ("generators", "rulebook", "seed"):
        assert f"../{package}:/app/{package}:ro" in mounts


def load_alert_rules() -> list[dict]:
    groups = yaml.safe_load((DEPLOY / "prometheus" / "alerts.yml").read_text())["groups"]
    return [rule for group in groups for rule in group["rules"]]


def test_prometheus_loads_the_rules_and_sends_to_alertmanager():
    config = yaml.safe_load((DEPLOY / "prometheus" / "prometheus.yml").read_text())
    services = load_compose()["services"]

    assert config["rule_files"] == ["/etc/prometheus/alerts.yml"]
    assert "./prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro" in services["prometheus"]["volumes"]
    targets = [t for am in config["alerting"]["alertmanagers"] for sc in am["static_configs"] for t in sc["targets"]]
    assert targets == ["alertmanager:9093"]
    assert re.fullmatch(r"docker\.io/prom/alertmanager:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", services["alertmanager"]["image"])


def test_alert_rules_owner_asked_for():
    """out of sync 5 min, drift check stopped, collector cannot poll 5 min.
    fan or power failed, temperature or optic level past its own threshold.
    BGP/EVPN and OSPF peers, M-LAG down 2 minutes, errors rising on a port in use.
    edge-port protection acting on a port. intent documents differing between NetBox and the database."""
    rules = {r["alert"]: r for r in load_alert_rules()}

    assert set(rules) == {"FabricDeviceOutOfSync", "FabricDriftCheckStopped", "FabricCollectorDeviceDown",
                          "FabricPowerModuleFailed", "FabricFanMissing", "FabricTemperatureHigh",
                          "FabricOpticRxPower", "FabricOpticTxPower", "FabricOpticTemperature",
                          "FabricBgpPeerDown", "FabricOspfNeighbourDown", "FabricMlagHeartbeatLost",
                          "FabricMlagPeerLinkDown", "FabricInterfaceErrors",
                          "FabricTrafficAnomaly", "FabricLinkFlapping", "FabricPortProtectionTripped",
                          "FabricIntentStoreDiffers"}
    for control in ("FabricBgpPeerDown", "FabricOspfNeighbourDown"):   # planned partners never alarm
        assert "fabric_planned_address" in rules[control]["expr"]
    for mlag in ("FabricMlagHeartbeatLost", "FabricMlagPeerLinkDown"):
        assert "fabric_mlag_peer_planned" in rules[mlag]["expr"]
    for optic in ("FabricOpticRxPower", "FabricOpticTxPower", "FabricOpticTemperature"):   # ports in use only
        assert "fabric_port_in_use == 1" in rules[optic]["expr"]
    assert rules["FabricDeviceOutOfSync"]["for"] == "5m"
    assert rules["FabricCollectorDeviceDown"]["for"] == "5m"
    assert "absent(fabric_drift_last_run_timestamp_seconds)" in rules["FabricDriftCheckStopped"]["expr"]


def test_alert_rules_use_metrics_the_platform_exports():
    exported = set()
    for source in ("orchestrator/drift.py", "orchestrator/faceplate.py", "orchestrator/controlplane.py", "orchestrator/anomaly.py",
                   "orchestrator/intent_store.py", "collector/metrics.py"):
        exported |= set(re.findall(r'"(fabric_[a-z_]+)"', (DEPLOY.parent / source).read_text()))
    from collector.state import COUNTERS   # named f"fabric_interface_{name}" (collector/metrics.py)

    exported |= {f"fabric_interface_{name}_total" for name in COUNTERS}
    for rule in load_alert_rules():
        used = set(re.findall(r"\bfabric_[a-z_]+", rule["expr"]))
        assert used and used <= exported, f"{rule['alert']} uses unknown metrics {used - exported}"


def test_alertmanager_reads_the_orchestrators_config():
    """Its receiver is set in the GUI: the orchestrator writes the file, Alertmanager only reads it."""
    services = load_compose()["services"]
    alertmanager, orchestrator = services["alertmanager"], services["orchestrator"]

    assert "--config.file=/config/alertmanager.yml" in alertmanager["command"]
    assert "alertmanager-config:/config:ro" in alertmanager["volumes"]
    assert "alertmanager-config:/alertmanager-config" in orchestrator["volumes"]
    assert orchestrator["environment"]["ALERTMANAGER_CONFIG"] == "/alertmanager-config/alertmanager.yml"
    assert alertmanager["depends_on"]["orchestrator"]["condition"] == "service_healthy"
    assert "ports" not in alertmanager


def test_gnetcli_is_built_with_the_known_hosts_patch_and_its_tests():
    """gnetcli accepts any SSH host key unless our patch (deploy/gnetcli/) is applied."""
    dockerfile = (DEPLOY.parent / "orchestrator" / "Dockerfile").read_text()

    assert "COPY deploy/gnetcli/known-hosts.patch" in dockerfile
    assert "git apply /tmp/known-hosts.patch" in dockerfile
    assert "git apply /tmp/known-hosts.patch \\\n && go get" in dockerfile and "go test ./..." in dockerfile   # its tests too
    assert "go install github.com/annetutil/gnetcli" not in dockerfile   # the unpatched build
    patch = (DEPLOY / "gnetcli" / "known-hosts.patch").read_text()
    assert "WithKnownHostsFile" in patch and "known_hosts_test.go" in patch


def test_orchestrator_verifies_ssh_host_keys():
    env = load_compose()["services"]["orchestrator"]["environment"]

    assert env["KNOWN_HOSTS_FILE"].startswith("/tmp/")


def test_counters_are_polled_and_scraped_every_5_seconds():
    """counters every 5 s, state and LLDP every 25 s; Prometheus scrapes
    at the counter interval, since traffic is the rate over the last two samples."""
    collector = load_compose()["services"]["collector"]
    config = yaml.safe_load((DEPLOY / "prometheus" / "prometheus.yml").read_text())

    assert collector["environment"]["COLLECTOR_COUNTER_INTERVAL"] == "5"
    assert collector["environment"]["COLLECTOR_INTERVAL"] == "25"
    assert collector["environment"]["COLLECTOR_CONTROL_INTERVAL"] == "25"   # with the state polls
    job = next(j for j in config["scrape_configs"] if j["job_name"] == "collector")
    assert job["scrape_interval"] == "5s"


def test_orchestrator_serves_https_only_with_a_secure_cookie():
    """self-signed HTTPS, in the example and in production."""
    dockerfile = (DEPLOY.parent / "orchestrator" / "Dockerfile").read_text()
    orchestrator = load_compose()["services"]["orchestrator"]

    assert 'CMD ["python", "-m", "orchestrator.serve"]' in dockerfile
    assert "uvicorn" not in dockerfile.split("CMD", 1)[1]
    assert orchestrator["environment"]["COOKIE_SECURE"] == "${COOKIE_SECURE:-true}"
    assert "orchestrator-tls:/tls" in orchestrator["volumes"]
    assert "https://localhost:8000/health" in " ".join(orchestrator["healthcheck"]["test"])


def test_prometheus_verifies_the_orchestrator_certificate_and_never_sees_the_key():
    config = yaml.safe_load((DEPLOY / "prometheus" / "prometheus.yml").read_text())
    job = next(j for j in config["scrape_configs"] if j["job_name"] == "orchestrator")
    volumes = load_compose()["services"]["prometheus"]["volumes"]

    assert job["scheme"] == "https" and job["tls_config"]["ca_file"] == "/tls-public/fabric.crt"
    assert "insecure_skip_verify" not in job["tls_config"]
    assert "orchestrator-tls-public:/tls-public:ro" in volumes
    assert not any(v.startswith("orchestrator-tls:") for v in volumes)


def test_ztp_dhcp_is_off_unless_its_profile_is_started():
    """dnsmasq answers on the management network only when asked for (profile ztp), and
    reads the files the orchestrator writes (never writes them itself)."""
    services = load_compose()["services"]
    ztp = services["ztp"]
    assert ztp["profiles"] == ["ztp"] and ztp["network_mode"] == "host"
    assert ztp["volumes"] == ["ztp:/ztp:ro"]
    assert "ztp:/ztp" in services["orchestrator"]["volumes"]
    assert services["orchestrator"]["environment"]["ZTP_DIR"] == "/ztp"
    run = (DEPLOY / "ztp" / "run.sh").read_text()
    assert "-s /ztp/dnsmasq.conf" in run   # an empty config (ZTP not configured) keeps dnsmasq off


def test_the_test_directory_runs_only_when_asked_for():
    """OpenLDAP and FreeRADIUS with throwaway users, only with the directory-test profile, no port."""
    services = load_compose()["services"]
    for name in ("directory-ldap", "directory-radius"):
        assert services[name]["profiles"] == ["directory-test"] and "ports" not in services[name]
        assert re.fullmatch(r"docker\.io/[a-z/-]+:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", services[name]["image"])   # pinned
    assert (DEPLOY / "directory-test" / "bootstrap.ldif").exists()


def test_every_image_is_pinned_by_digest():
    """A tag can be moved to other contents; a digest cannot. Dependabot proposes new digests."""
    root = Path(__file__).resolve().parents[2]
    refs = []
    for path in [*root.glob("deploy/**/*.yaml"), *root.glob("**/Dockerfile")]:
        if "node_modules" in path.parts:
            continue
        for line in path.read_text().splitlines():
            if m := re.match(r"\s*(?:image:|FROM)\s+(?:--\S+\s+)*(\S+)", line):
                refs.append((path.name, m.group(1)))
    unpinned = [r for r in refs if not r[1].endswith(":local") and "@sha256:" not in r[1]]
    assert refs and unpinned == []


def test_the_lockfile_pins_every_dependency_with_hashes():
    import tomllib

    root = Path(__file__).resolve().parents[2]
    lock = (root / "requirements.lock").read_text()
    pinned = {m.group(1).lower().replace("_", "-") for m in re.finditer(r"^([A-Za-z0-9_.-]+)==\S+ \\$", lock, re.M)}
    wanted = {re.match(r"[A-Za-z0-9_.-]+", d).group(0).lower().replace("_", "-")
              for d in tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]}
    assert wanted <= pinned
    assert lock.count("==") == len(pinned) and "--hash=sha256:" in lock
