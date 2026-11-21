"""Read-only NETCONF probe: what does this switch actually return?

Saves the server's capabilities and the raw replies to a few candidate LLDP and
interface queries, so the collector is written against real data rather than guessed
YANG paths. Only <get> is used; nothing is changed on the device.

    python -m collector.probe leaf1 --out /tmp/netconf-probe
    python -m collector.probe leaf1 --schema huawei-lldp > huawei-lldp.yang
    python -m collector.probe leaf1 --routes --out /tmp/netconf-routes
    python -m collector.probe leaf1 --display "display bgp peer"            (huawei-cli)
    python -m collector.probe leaf1 --ssh --out /tmp/ssh-probe              (SSH display)
    python -m collector.probe leaf1 --control-plane --out /tmp/cp-probe
    python -m collector.probe leaf1 --cp-paths --out /tmp/cp-paths          (the polls themselves)
    python -m collector.probe leaf1 --hw-paths --out /tmp/hw-paths          (hardware polls, timed)

The device's management address and pinned NETCONF host key come from NetBox
(orchestrator/hostkeys.py); an unpinned device is refused. Credentials:
GNETCLI_DEV_LOGIN / GNETCLI_DEV_PASSWORD (the lab login).
"""

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

from ncclient import manager
from ncclient.operations import RPCError

from collector.protection import QUERY as PROTECTION_STATE
from collector.__main__ import shared_login

VRP = 'xmlns="http://www.huawei.com/netconf/vrp" content-version="1.0" format-version="1.0"'

# name -> subtree filter. Both Huawei schemas are tried: older VRP releases use the
# "netconf/vrp" schema, newer ones the huawei-* YANG modules; which one a CE6881H
# on our release answers is exactly what this probe finds out.
QUERIES = {
    "yang-lldp": '<lldp xmlns="urn:huawei:yang:huawei-lldp"/>',
    "yang-ifm": '<ifm xmlns="urn:huawei:yang:huawei-ifm"/>',
    "vrp-lldp": f"<lldp {VRP}/>",
    "vrp-ifm": f"<ifm {VRP}/>",
    "ietf-interfaces-state": '<interfaces-state xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces"/>',
    "ietf-interfaces": '<interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces"/>',
}

# where V300 keeps the routing tables and BGP routes. Schemas are saved too,
# so the paths come from the device's own YANG, not from guesses.
ROUTE_SCHEMAS = ("huawei-routing", "huawei-bgp", "huawei-bgp-routing-table", "huawei-network-instance",
                 "huawei-fib", "huawei-routing-policy", "huawei-xpl", "huawei-l3vpn")
ROUTE_QUERIES = {
    "network-instance": '<network-instance xmlns="urn:huawei:yang:huawei-network-instance"/>',
    "bgp": '<bgp xmlns="urn:huawei:yang:huawei-bgp"/>',
    "routing": '<routing xmlns="urn:huawei:yang:huawei-routing"/>',
    "fib": '<fib xmlns="urn:huawei:yang:huawei-fib"/>',
    # Second round: only the RIB entries of every instance (the first reply had them
    # with all the instance config), and the BGP tables, which a plain <bgp/> does not return.
    "rib-only": '<network-instance xmlns="urn:huawei:yang:huawei-network-instance"><instances><instance><name/>'
                '<afs xmlns="urn:huawei:yang:huawei-l3vpn"><af><routing xmlns="urn:huawei:yang:huawei-routing">'
                '<routing-manage><topologys><topology><routes><ipv4-unicast-routes/></routes></topology></topologys>'
                '</routing-manage></routing></af></afs></instance></instances></network-instance>',
    "bgp-route": '<bgp xmlns="urn:huawei:yang:huawei-bgp"><base-process>'
                 '<bgp-route xmlns="urn:huawei:yang:huawei-bgp-routing-table"/></base-process></bgp>',
    "bgp-route-ipv4": '<bgp xmlns="urn:huawei:yang:huawei-bgp"><base-process>'
                      '<bgp-route xmlns="urn:huawei:yang:huawei-bgp-routing-table"><ipv4-unicast/></bgp-route>'
                      '</base-process></bgp>',
}


async def target(name: str) -> tuple:
    """(management address, host key check) of a NetBox device."""
    from orchestrator import hostkeys
    from orchestrator.config import Settings
    from orchestrator.netbox import NetBoxClient

    settings = Settings.from_env()
    netbox = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
    try:
        device = await netbox.find_one("dcim/devices", name=name)
    finally:
        await netbox.aclose()
    if device is None or hostkeys.address(device) is None:
        raise SystemExit(f"{name}: no such device with a management address in NetBox")
    check = hostkeys.netconf_check(device)
    if check is None:
        raise SystemExit(f"{name}: no pinned NETCONF host key (python -m orchestrator.hostkeys pin {name})")
    return hostkeys.address(device), check


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only NETCONF probe")
    parser.add_argument("device", help="NetBox device name")
    parser.add_argument("--port", type=int, default=830)
    parser.add_argument("--out", default="/tmp/netconf-probe")
    parser.add_argument("--schema", help="print this YANG module from the device (get-schema) and exit")
    parser.add_argument("--routes", action="store_true", help="routing table and BGP queries and their schemas")
    parser.add_argument("--display", action="append", default=[],
                        help="run this `display ...` command through huawei-cli execute-command and print its output")
    parser.add_argument("--control-plane", action="store_true",
                        help="modules the switch announces for BGP, EVPN, NVE, M-LAG, OSPF, MAC, ARP and hardware: "
                             "their schemas and one <get> of each top container")
    parser.add_argument("--cp-paths", action="store_true",
                        help="the control-plane subtrees the collector would poll, each timed")
    parser.add_argument("--hw-paths", action="store_true",
                        help="the hardware subtrees the collector would poll, each timed")
    parser.add_argument("--trace", action="store_true",
                        help="MAC, ARP and EVPN tables a path trace would read, each timed")
    parser.add_argument("--microseg", action="store_true",
                        help="microsegmentation resources, counters and states, each timed (V300R025)")
    parser.add_argument("--ports", action="store_true",
                        help="port settings: description, admin status, MTU, speed, negotiation, jumbo frames")
    parser.add_argument("--inventory", action="store_true",
                        help="model, software and each port's hardware and medium type, each timed")
    parser.add_argument("--protection", action="store_true",
                        help="storm control, loop detection, STP edge/BPDU and error-down state")
    parser.add_argument("--snmp", action="store_true",
                        help="SNMP agent, versions, views, v3 groups, users and target hosts, no key or community")
    parser.add_argument("--speed", action="store_true",
                        help="each interface's bandwidth and speed as the switch reports them")
    parser.add_argument("--ssh", action="store_true",
                        help="read the import commands over SSH (port 22 pin), secret lines left out")
    args = parser.parse_args()
    if args.ssh:
        return _ssh(args.device, Path(args.out))
    for command in args.display:
        if not command.startswith("display ") or any(c in command for c in "\n;|"):
            parser.error(f"only single `display ...` commands: {command!r}")
    host, check = asyncio.run(target(args.device))
    # the collector's read account: the GUI's first, as on the HA servers, then the .env's
    login, password = shared_login() or (os.environ.get("COLLECTOR_LOGIN") and (
        os.environ["COLLECTOR_LOGIN"], os.environ.get("COLLECTOR_PASSWORD", ""))) or (
        os.environ["GNETCLI_DEV_LOGIN"], os.environ["GNETCLI_DEV_PASSWORD"])

    with manager.connect(
        host=host,
        port=args.port,
        username=login,
        password=password,
        hostkey_verify=True,
        unknown_host_cb=check,
        allow_agent=False,
        look_for_keys=False,
        device_params={"name": "default"},
        timeout=60,
    ) as session:
        if args.schema:
            sys.stdout.write(session.get_schema(args.schema).data)
            return 0
        if args.display:
            return _display(session, args.display)

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        if args.routes:
            return _routes(session, out)
        if args.control_plane:
            return _control_plane(session, out)
        if args.cp_paths:
            return _cp_paths(session, out)
        if args.trace:
            return _cp_paths(session, out, TRACE_PATHS, ())
        if args.microseg:
            return _cp_paths(session, out, MICROSEG_PATHS, ())
        if args.speed:
            return _cp_paths(session, out, SPEED_PATHS, ())
        if args.ports:
            return _cp_paths(session, out, PORT_PATHS, ())
        if args.inventory:
            return _cp_paths(session, out, INVENTORY_PATHS, ())
        if args.protection:
            return _cp_paths(session, out, PROTECTION_PATHS, ())
        if args.snmp:
            return _cp_paths(session, out, SNMP_PATHS, ())
        if args.hw_paths:
            return _cp_paths(session, out, HW_PATHS, ("openconfig-platform", "openconfig-platform-transceiver"))
        capabilities = sorted(session.server_capabilities)
        (out / "capabilities.txt").write_text("\n".join(capabilities) + "\n")
        print(f"{len(capabilities)} capabilities -> {out / 'capabilities.txt'}")
        for keyword in ("lldp", "ifm", "ietf-interfaces", "openconfig", "netconf/vrp"):
            hits = [c for c in capabilities if keyword in c]
            print(f"  {keyword}: {len(hits)}" + (f"  e.g. {hits[0][:120]}" if hits else ""))

        for name, subtree in QUERIES.items():
            try:
                reply = session.get(filter=("subtree", subtree))
            except RPCError as exc:
                print(f"{name}: rpc-error: {exc}")
                continue
            except Exception as exc:  # timeouts, transport errors: keep probing the rest
                print(f"{name}: {type(exc).__name__}: {exc}")
                continue
            (out / f"{name}.xml").write_text(reply.data_xml)
            print(f"{name}: {len(reply.data_xml)} bytes -> {out / (name + '.xml')}")
    return 0


# Lines that may hold secrets (password hashes, keys, communities): never saved by the probe.
SECRET_LINE = re.compile(r"cipher|password|%\^%#|%@%#|\$1[a-z]\$|key|community|local-user", re.IGNORECASE)


def _ssh(name: str, out: Path) -> int:
    from orchestrator import hostkeys, ssh_read

    async def device() -> dict:
        from orchestrator.config import Settings
        from orchestrator.netbox import NetBoxClient

        settings = Settings.from_env()
        netbox = NetBoxClient(settings.netbox_url, settings.netbox_token, settings.netbox_timeout)
        try:
            return await netbox.find_one("dcim/devices", name=name)
        finally:
            await netbox.aclose()

    found = asyncio.run(device())
    if found is None or hostkeys.address(found) is None:
        raise SystemExit(f"{name}: no such device with a management address in NetBox")
    keys = hostkeys.pins(found)[hostkeys.SSH_PORT]
    if not keys:
        raise SystemExit(f"{name}: no pinned SSH host key")
    commands = [c for c in ssh_read.ALLOWED if c.startswith("display ")]
    started = time.monotonic()
    sysname, outputs = ssh_read.read(hostkeys.address(found), keys, os.environ["GNETCLI_DEV_LOGIN"],
                                     os.environ["GNETCLI_DEV_PASSWORD"], commands)
    print(f"sysname {sysname}, {time.monotonic() - started:.1f} s")
    out.mkdir(parents=True, exist_ok=True)
    for command, output in zip(commands, outputs):
        lines = output.splitlines()
        kept = [line for line in lines if not SECRET_LINE.search(line)]
        path = out / (command.removeprefix("display ").replace(" ", "-") + ".txt")
        path.write_text("\n".join(kept) + "\n")
        print(f"{command}: {len(lines)} lines ({len(lines) - len(kept)} left out) -> {path}")
    return 0


def _display(session, commands: list[str]) -> int:
    """Read-only CLI through NETCONF (huawei-cli execute-command; 8 commands at most per call)."""
    import time
    from xml.sax.saxutils import escape

    from lxml import etree

    cmds = "".join(f"<cli-cmd><sequence>{i}</sequence><command>{escape(c)}</command></cli-cmd>"
                   for i, c in enumerate(commands, 1))
    rpc = etree.fromstring(f'<execute-command xmlns="urn:huawei:yang:huawei-cli"><cli-cmds>{cmds}</cli-cmds></execute-command>')
    start = time.monotonic()
    reply = session.dispatch(rpc)
    print(f"# {len(commands)} command(s) in {time.monotonic() - start:.1f} s")
    sys.stdout.write(reply.xml if hasattr(reply, "xml") else str(reply))
    print()
    return 0


def _routes(session, out: Path) -> int:
    import time

    for module in ROUTE_SCHEMAS:
        try:
            (out / f"{module}.yang").write_text(session.get_schema(module).data)
            print(f"schema {module}: saved")
        except Exception as exc:
            print(f"schema {module}: {type(exc).__name__}: {exc}")
    for name, subtree in ROUTE_QUERIES.items():
        start = time.monotonic()
        try:
            reply = session.get(filter=("subtree", subtree))
        except Exception as exc:  # rpc-error, timeout: keep probing the rest
            print(f"{name}: {type(exc).__name__}: {exc}")
            continue
        (out / f"{name}.xml").write_text(reply.data_xml)
        print(f"{name}: {len(reply.data_xml)} bytes in {time.monotonic() - start:.1f} s -> {out / (name + '.xml')}")
    return 0


# Control plane and hardware: which modules this switch announces, from its capabilities.
CP_KEYWORDS = ("bgp", "evpn", "nvo3", "vxlan", "mlag", "m-lag", "dfs", "ospf", "mac", "arp", "bfd",
               "devm", "cpu-memory", "fan", "power", "temperature", "optical", "transceiver", "sensor")
# Element values never written to disk (configured keys, passwords).
SECRET_XML = re.compile(r"(<(?:[\w-]*:)?([\w-]*(?:password|cipher|key|secret)[\w-]*)(?:\s[^>]*)?>)[^<]*(</)", re.IGNORECASE)
CIPHER_TEXT = re.compile(r"%@%#.*?%@%#|%\+%#.*?%\+%#|%\^%#.*?%\^%#|%\$%\$.*?%\$%\$", re.DOTALL)   # VRP cipher text in any leaf (a community is a key)


def _control_plane(session, out: Path) -> int:
    modules = sorted({m.group(1) for c in session.server_capabilities
                      if (m := re.search(r"module=([\w-]+)", c))})
    wanted = [m for m in modules if m.startswith("huawei-") and any(k in m for k in CP_KEYWORDS)
              and not any(x in m for x in ("deviations", "-type", "extension"))]
    (out / "modules.txt").write_text("\n".join(wanted) + "\n")
    print(f"{len(wanted)} matching modules -> {out / 'modules.txt'}")
    for module in wanted:
        try:
            (out / f"{module}.yang").write_text(session.get_schema(module).data)
        except Exception as exc:
            print(f"schema {module}: {type(exc).__name__}: {exc}")
        top = module.removeprefix("huawei-")
        start = time.monotonic()
        try:
            reply = session.get(filter=("subtree", f'<{top} xmlns="urn:huawei:yang:{module}"/>'))
        except Exception as exc:   # augmenting modules have no top container: an rpc-error is expected
            print(f"get {module}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
            time.sleep(1)
            continue
        text = SECRET_XML.sub(r"\1******\3", reply.data_xml)
        (out / f"{module}.xml").write_text(text)
        print(f"get {module}: {len(text)} bytes in {time.monotonic() - start:.1f} s")
        time.sleep(1)   # one at a time, gently (port 830 refused connections under load)
    return 0


# the subtrees found in the first round's schemas (probe-data/cp-probe, routes/).
_NI = 'xmlns="urn:huawei:yang:huawei-network-instance"'
CP_PATHS = {
    # base-process/peers/peer/afs/af/state (huawei-bgp, fields in submodule huawei-bgp-common); the
    # instance-processs/.../peer-states list is BGP multi-instance, refused for the base process.
    "bgp-peer-states": f'<network-instance {_NI}><instances><instance><name/><bgp xmlns="urn:huawei:yang:huawei-bgp">'
                       '<base-process><peers><peer><address/><afs><af><type/><state><current-state/><remote-as/>'
                       '<last-state/><up-down-duration/><receive-prefix/><receive-active-prefix/><advertise-prefix/>'
                       '</state></af></afs></peer></peers></base-process></bgp></instance></instances></network-instance>',
    "ospf-neighbors": f'<network-instance {_NI}><instances><instance><name/><ospfv2 xmlns="urn:huawei:yang:huawei-ospfv2">'
                      '<sites><site><neighbors/></site></sites></ospfv2></instance></instances></network-instance>',
    "route-statistics": f'<network-instance {_NI}><instances><instance><name/><afs xmlns="urn:huawei:yang:huawei-l3vpn"><af>'
                        '<routing xmlns="urn:huawei:yang:huawei-routing"><routing-manage><topologys><topology><routes>'
                        '<ipv4-route-statistics/></routes></topology></topologys></routing-manage></routing></af></afs>'
                        '</instance></instances></network-instance>',
    "nvo3-tunnels": '<nvo3 xmlns="urn:huawei:yang:huawei-nvo3"><tunnels/><vteps/></nvo3>',
    "mlag-states": '<m-lag xmlns="urn:huawei:yang:huawei-m-lag"><negotiation-states/></m-lag>',
    "mac-statistics": '<mac xmlns="urn:huawei:yang:huawei-mac"><mac-statistics/></mac>',
}


# from the first round's huawei-devm and huawei-cpu-memory replies (probe-data/cp-probe).
_DEVM = 'xmlns="urn:huawei:yang:huawei-devm"'
HW_PATHS = {
    # optical-module and driver extend huawei-devm from their own modules (seen in the first round's reply).
    "optics": f'<devm {_DEVM}><ports><port><position/><optical-module xmlns="urn:huawei:yang:huawei-pic"/>'
              '</port></ports></devm>',
    "fans-power": f'<devm {_DEVM}><driver xmlns="urn:huawei:yang:huawei-driver"><fans/><pms/><power-supplys/>'
                  '</driver></devm>',
    "cpu-memory": '<cpu-memory xmlns="urn:huawei:yang:huawei-cpu-memory"><board-cpu-infos/><board-memory-infos/>'
                  '</cpu-memory>',
}


# the speed a port runs at, for capacity percentages. Down ports showed their nominal speed in
# dynamic/bandwidth and trunks 0 (first probe); what an up port and an up trunk show is
# what this round reads.
SPEED_PATHS = {
    "ifm-bandwidth": '<ifm xmlns="urn:huawei:yang:huawei-ifm"><interfaces><interface><name/><dynamic><oper-status/>'
                     '<bandwidth/></dynamic><trunk xmlns="urn:huawei:yang:huawei-ifm-trunk"><max-bandwidth/></trunk>'
                     '</interface></interfaces></ifm>',
    "ietf-speed": '<interfaces-state xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces"><interface><name/>'
                  '<oper-status/><speed/></interface></interfaces-state>',
}


# where hosts are found. Lists are config false in the saved YANG (probe-data/cp-probe):
# huawei-mac bd-dynamic-macs / evpn-dynamic-macs, huawei-arp query-entries, and the EVPN routes that
# huawei-bgp-evpn augments into huawei-bgp-routing-table (found that module's BGP tables refused).
_BGP = 'xmlns="urn:huawei:yang:huawei-bgp"'
TRACE_PATHS = {
    "mac-bd": '<mac xmlns="urn:huawei:yang:huawei-mac"><bd-dynamic-macs/></mac>',
    "mac-evpn": '<mac xmlns="urn:huawei:yang:huawei-mac"><evpn-dynamic-macs/></mac>',
    "arp": '<arp xmlns="urn:huawei:yang:huawei-arp"><query-entries/></arp>',
    "evpn-routes": f'<bgp {_BGP}><base-process><bgp-route xmlns="urn:huawei:yang:huawei-bgp-routing-table">'
                   '<l2vpn-evpn xmlns="urn:huawei:yang:huawei-bgp-evpn"/></bgp-route></base-process></bgp>',
    "evpn-instance-routes": '<evpn xmlns="urn:huawei:yang:huawei-evpn"/>',
}



# microsegmentation state on V300R025 (huawei-microsegmentation, revision): per-chip
# resources, per-policy and per-classifier counters, per-rule states. Read only.
_MSEG = 'xmlns="urn:huawei:yang:huawei-microsegmentation"'
MICROSEG_PATHS = {
    "mseg-resources": f'<microsegmentation {_MSEG}><resources/></microsegmentation>',
    "mseg-policy-stats": f'<microsegmentation {_MSEG}><segment-policy-statistics/></microsegmentation>',
    "mseg-classifier-stats": f'<microsegmentation {_MSEG}><classifier-based-statistics/></microsegmentation>',
    "mseg-global-stats": f'<microsegmentation {_MSEG}><global-policy-statistics/></microsegmentation>',
    "mseg-rule-states": f'<microsegmentation {_MSEG}><traffic-segment-policy-states/></microsegmentation>',
    "mseg-member-states": f'<microsegmentation {_MSEG}><ipv4-member-states/></microsegmentation>',
}
# what a port profile would own, as the switch holds it today
PORT_PATHS = {
    "ifm-settings": '<ifm xmlns="urn:huawei:yang:huawei-ifm"><interfaces><interface><name/><description/><admin-status/>'
                    '<mtu/><trunk xmlns="urn:huawei:yang:huawei-ifm-trunk"><jumbo-frame xmlns="urn:huawei:yang:huawei-pic"/>'
                    '</trunk></interface></interfaces></ifm>',
    "pic-ethernet": '<devm xmlns="urn:huawei:yang:huawei-devm"><ports><port><position/><name/>'
                    '<ethernet xmlns="urn:huawei:yang:huawei-pic"><speed/><duplex/><negotiation/><negotiation-mode/>'
                    '<jumbo-frame/></ethernet></port></ports></devm>',
}

# what the platform would learn about a switch on its own (named leaves only: no secrets)
INVENTORY_PATHS = {
    "system-info": '<system xmlns="urn:huawei:yang:huawei-system"><system-info><product-name/><product-version/>'
                   '<hardware-model/><patch-version/><software-name/><platform-version/><esn/></system-info></system>',
    "devm-ports": '<devm xmlns="urn:huawei:yang:huawei-devm"><ports><port><position/><name/><type/><hard-type/>'
                  '<medium-type/><optical-equipped/></port></ports></devm>',
    "mpu-boards": '<devm xmlns="urn:huawei:yang:huawei-devm"><mpu-boards><mpu-board><position/><board-type/>'
                  '</mpu-board></mpu-boards></devm>',
}

STORM = "urn:huawei:yang:huawei-storm-control"
LOOP = "urn:huawei:yang:huawei-loopdetect"
PROTECTION_PATHS = {   # config and state of edge-port protection
    "storm-control": f'<ifm xmlns="urn:huawei:yang:huawei-ifm"><interfaces><interface><name/>'
                     f'<storm-control xmlns="{STORM}"/><storm-suppress xmlns="{STORM}"/></interface></interfaces></ifm>',
    "loopdetect-global": f'<loopdetect xmlns="{LOOP}"/>',
    "loopdetect-ports": f'<ifm xmlns="urn:huawei:yang:huawei-ifm"><interfaces><interface><name/>'
                        f'<loopdetect xmlns="{LOOP}"/></interface></interfaces></ifm>',
    "error-down": '<ifm xmlns="urn:huawei:yang:huawei-ifm"><interfaces><interface><name/><error-down/></interface></interfaces></ifm>',
    "auto-recovery": '<ifm xmlns="urn:huawei:yang:huawei-ifm"><auto-recovery-times/></ifm>',
    "mstp-default": '<mstp xmlns="urn:huawei:yang:huawei-mstp"><default-process/></mstp>',
    "mstp-global": '<mstp xmlns="urn:huawei:yang:huawei-mstp"><global/></mstp>',
    "state": PROTECTION_STATE,   # the collector's own query, timed as polled
}

SNMP = "urn:huawei:yang:huawei-snmp"
SNMP_PATHS = {   # named leaves only, so no key, community or security name is ever asked for
    "agent": f'<snmp xmlns="{SNMP}"><agent-flag/><engine/><system><protocol-source-interface-name/><vpn-instance-name/>'
             '<is-public-net/><listen-port/><trap-source-interface-name/><password-min-length/>'
             '<usm-user-cmplx-check/></system><source-interfaces/></snmp>',
    "views": f'<snmp xmlns="{SNMP}"><mib-views/></snmp>',
    "v3-groups": f'<snmp xmlns="{SNMP}"><v3-groups/></snmp>',
    "usm-users": f'<snmp xmlns="{SNMP}"><usm-users><usm-user><user-name/><group-name/><auth-protocol/><priv-protocol/>'
                 '<is-auth-localized/><is-priv-localized/><acl-number/><active-status/></usm-user></usm-users></snmp>',
    "communities": f'<snmp xmlns="{SNMP}"><communitys><community><alias-name/><access-right/><view-name/><acl/>'
                   '</community></communitys></snmp>',
    "target-hosts": f'<snmp xmlns="{SNMP}"><target-hosts><target-host><nms-name/><address/><vpn-instance-name/>'
                    '<is-public-net/><notify-type/><port-number/><usm-name/><security-level/><interface-name/><status/>'
                    '</target-host></target-hosts></snmp>',
}


def _cp_paths(session, out: Path, paths: dict | None = None,
              schemas=("huawei-bgp-common", "huawei-bgp-type")) -> int:
    for module in schemas:   # the BGP peer-state fields live in a submodule
        try:
            (out / f"{module}.yang").write_text(session.get_schema(module).data)
            print(f"schema {module}: saved")
        except Exception as exc:
            print(f"schema {module}: {type(exc).__name__}: {str(exc).splitlines()[0][:120]}")
    for name, subtree in (paths or CP_PATHS).items():
        start = time.monotonic()
        try:
            reply = session.get(filter=("subtree", subtree))
        except Exception as exc:
            print(f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0][:160]}")
            time.sleep(1)
            continue
        text = CIPHER_TEXT.sub("******", SECRET_XML.sub(r"\1******\3", reply.data_xml))
        (out / f"{name}.xml").write_text(text)
        print(f"{name}: {len(text)} bytes in {time.monotonic() - start:.2f} s")
        time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
