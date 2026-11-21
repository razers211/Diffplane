"""The Annet context shipped in the image loads our generators and uses gnetcli."""

from pathlib import Path

import pytest

from annet.generators import _get_generators
from annet.lib import get_context

from tests.generators.harness import _STORAGE

CONTEXT = Path(__file__).resolve().parents[2] / "deploy" / "annet" / "context.yml"


@pytest.fixture(autouse=True)
def fresh_context():
    # Annet caches the context for the whole process.
    get_context.cache_clear()
    yield
    get_context.cache_clear()


def load_context(monkeypatch) -> dict:
    monkeypatch.setenv("ANN_CONTEXT_CONFIG_PATH", str(CONTEXT))
    monkeypatch.delenv("ANN_SELECTED_CONTEXT", raising=False)
    monkeypatch.delenv("ANN_GENERATORS_CONTEXT", raising=False)
    return get_context()


def test_context_loads_our_generators(monkeypatch):
    context = load_context(monkeypatch)

    generators = _get_generators(context["generators"], _STORAGE)

    assert [type(g).__name__ for g in generators] == ["Hostname", "Ntp", "Snmp", "Loopbacks", "FabricLinks", "OspfKeychain", "SwitchLogin", "Ospf", "Mlag", "Trunks", "MlagBypass", "Vlans", "Nve", "BgpEvpn", "BridgeDomains", "L2Subinterfaces", "TenantVrfs", "Gateways", "DhcpRelay", "TenantBgp", "Vlanifs", "FirewallServices", "SegmentPeering", "FirewallBgp",
                                                       "RoutingFilters", "RoutePolicies", "StaticRoutes", "BgpPeers",
                                                       "BgpAggregates", "OspfProcesses", "NetStream", "Syslog", "SftpServerKey", "Microsegmentation", "Qos", "Dci", "PortProfiles", "PortProtection"]


def test_context_uses_netbox_and_gnetcli_without_secrets(monkeypatch):
    context = load_context(monkeypatch)

    assert context["storage"]["adapter"] == "netbox"
    assert context["fetcher"]["adapter"] == "gnetcli"
    assert context["deployer"]["adapter"] == "gnetcli"
    assert context["rulebook"]["module"] == "rulebook.texts"
    assert not context["storage"].get("params"), "storage params must come from the environment"
    # gnetcli gets only its server config; the deployer also the transcript directory (the
    # orchestrator reads it on failure).
    from orchestrator.annet import DEPLOY_LOGS

    server_conf = context["fetcher"]["params"]["server_conf"]
    assert context["fetcher"]["params"] == {"server_conf": server_conf}
    assert context["deployer"]["params"] == {"server_conf": server_conf, "logs_dir": str(DEPLOY_LOGS)}


def test_each_gnetcli_server_takes_a_free_port(monkeypatch):
    """The adapters pass their own default server_conf, which overrides GNETCLI_SERVER_CONF: only the
    context's reaches them (imported fabrics: runs at once collided on 127.0.0.1:50051)."""
    from gnetcli_adapter.gnetcli_adapter import GnetcliDeployer, GnetcliFetcher

    context = load_context(monkeypatch)
    for adapter, section in ((GnetcliFetcher, "fetcher"), (GnetcliDeployer, "deployer")):
        assert adapter.with_config(**context[section]["params"]).conf.server_conf.port == "127.0.0.1:0"
