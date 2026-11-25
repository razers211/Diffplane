"""Rolling back to an earlier verified deploy's intent.

A rollback changes intent, never a switch directly: the planner compares today's intent with a
rollback point (orchestrator/intent_snapshots.py) and lists the intent operations that restore
it, in an order the orchestrator's own checks accept; applying them runs those operations one by
one (step 3b), then the switches get the change through the normal pipeline (diff, approval,
deploy one device at a time, verification). The management plane is never in intent, so it is
never in a rollback.

Scope: "this switch" restores only that switch's intent (segment attachments on its
ports, its trunks, services hosted on it, its routing target, the pair's for a pair) plus any
segment those need; segments' own settings are left alone unless the segment is recreated.
"fabric" restores everything in the snapshot, the fabric settings the GUI owns included.

Order (each later step relies on the earlier ones):
  1. routing objects that use VPN instances leave the affected routing targets (back in step 12),
     so removing a segment or service is not refused by the routing guard;
  2. attachments that go are detached, changed VLAN IDs set;
  3. segments that lose their firewall service are moved off it;
  4. services that go (or change more than their ASN) are deleted;
  5. segments that go, or change bridge domain or gateway subnet, are made not routed, then deleted;
  6. trunks that go are deleted;
  7. segments renamed; 8. segments created; 9. trunks created; 10. services created, ASNs set;
  11. segments routed (exact gateway subnet), then their peers;
  12. attachments added; routing targets set to the snapshot; fabric settings (fabric scope).
"""

from dataclasses import dataclass, field


@dataclass
class Scope:
    """`devices`: the switch and its M-LAG peer (None: the whole fabric); `target`: its routing target."""

    devices: set[str] | None = None
    target: str | None = None
    peers: dict[str, str] = field(default_factory=dict)   # device -> M-LAG peer, for the whole fabric

    @property
    def fabric(self) -> bool:
        return self.devices is None

    def has(self, device: str | None) -> bool:
        return self.fabric or device in self.devices


def op(kind: str, text: str, **args) -> dict:
    return {"kind": kind, "text": text, **args}


def _segments(data: dict) -> dict[int, dict]:
    return {s["vni"]: s for s in data.get("segments", [])}


def _attachments(data: dict, scope: Scope) -> dict[tuple, int | None]:
    return {(a["device"], a["interface"], s["vni"]): a["vid"]
            for s in data.get("segments", []) for a in s["attachments"] if scope.has(a["device"])}


def _mlag_ports(*datas: dict) -> set[tuple[str, str]]:
    return {(device, f"Eth-Trunk{t['number']}") for data in datas for device, trunks in (data.get("trunks") or {}).items()
            for t in trunks if t["m_lag"]}


def _one_side(keys, scope: Scope, mlag: set[tuple[str, str]]):
    """An M-LAG trunk attachment is written on both leaves by one operation: keep one side."""
    kept = []
    for key in sorted(keys):
        device, interface = key[0], key[1]
        peer = scope.peers.get(device)
        if (device, interface) in mlag and peer is not None and peer < device and (peer, interface, *key[2:]) in keys:
            continue
        kept.append(key)
    return kept


def _services(data: dict, scope: Scope) -> dict[str, dict]:
    """Services hosted in the scope (a pair's service is stored on one leaf; the scope holds both)."""
    return {s["name"]: s for s in data.get("services", []) if scope.has(s.get("device"))}


SERVICE_FIELDS = ("device", "port", "vlan", "address", "next_hop", "kind", "addresses")


def _vrf(service: dict) -> str:
    """A service's VPN instance; a snapshot from before has its own."""
    return service.get("vrf") or service["name"]


def _instances(segments: dict[int, dict]) -> dict[str, int]:
    """Routed segments' VPN instances -> their VNI."""
    from generators.fabric import segment_vpn

    return {segment_vpn(s["name"], s["bd_id"]): v for v, s in segments.items() if s.get("routing") and s.get("bd_id") is not None}


def _vpn_free(routing: dict) -> dict:
    """A target's routing intent without the objects that live in a VPN instance."""
    return {kind: [o for o in objects if not o.get("vpn")] if isinstance(objects, list) else objects
            for kind, objects in routing.items()}


def plan(current: dict, snapshot: dict, scope: Scope) -> dict:
    """The operations that bring today's intent back to the snapshot's, within the scope; and
    what the scope cannot restore (reported, not done)."""
    ops: list[dict] = []
    notes: list[str] = []
    cur_segs, snap_segs = _segments(current), _segments(snapshot)
    mlag = _mlag_ports(current, snapshot)

    # Which segments this rollback touches as a whole (create, delete, reroute, rename).
    if scope.fabric:
        wanted = set(snap_segs)
        gone = set(cur_segs) - set(snap_segs)
    else:
        wanted = {vni for (device, _, vni) in _attachments(snapshot, scope)}
        gone = set()
        for vni in sorted(wanted & set(cur_segs)):
            if cur_segs[vni]["bd_id"] != snap_segs[vni]["bd_id"] or cur_segs[vni]["name"] != snap_segs[vni]["name"]:
                notes.append(f"segment {cur_segs[vni]['name']} (VNI {vni}) changed fabric-wide since this point "
                             "(name or bridge domain): only a fabric rollback restores that")
    replaced = {vni for vni in wanted & set(cur_segs) if scope.fabric and cur_segs[vni]["bd_id"] != snap_segs[vni]["bd_id"]}
    regateway = {vni for vni in (wanted & set(cur_segs)) - replaced
                 if scope.fabric and cur_segs[vni]["routing"] and snap_segs[vni]["routing"]
                 and cur_segs[vni]["routing"]["gateway"] != snap_segs[vni]["routing"]["gateway"]}
    remove_segments = gone | replaced
    create_segments = (wanted - set(cur_segs)) | replaced

    # 1. routing targets: VPN objects out first.
    cur_routing = (current.get("routing") or {}).get("devices") or {}
    snap_routing = (snapshot.get("routing") or {}).get("devices") or {}
    targets = sorted(t for t in set(cur_routing) | set(snap_routing)
                     if (scope.fabric or t == scope.target) and cur_routing.get(t) != snap_routing.get(t))
    for target in targets:
        stripped = _vpn_free(cur_routing.get(target) or {})
        if stripped != (cur_routing.get(target) or {}):
            ops.append(op("routing_set", f"routing of {target}: objects in VPN instances out for now",
                          target=target, routing=stripped))

    # 2. attachments.
    cur_att, snap_att = _attachments(current, scope), _attachments(snapshot, scope)
    for key in _one_side(set(cur_att) - set(snap_att) | {k for k in cur_att if k[2] in remove_segments}, scope, mlag):
        device, interface, vni = key
        ops.append(op("detach", f"detach {cur_segs[vni]['name']} from {device} {interface}",
                      device=device, interface=interface, vni=vni))
    for key in _one_side({k for k in set(cur_att) & set(snap_att) if cur_att[k] != snap_att[k] and k[2] not in remove_segments},
                         scope, mlag):
        device, interface, vni = key
        ops.append(op("set_vid", f"{cur_segs[vni]['name']} on {device} {interface}: "
                      f"{'untagged' if snap_att[key] is None else f'VLAN {snap_att[key]}'}",
                      device=device, interface=interface, vni=vni, vid=snap_att[key]))

    # 3-4. services.
    cur_svc, snap_svc = _services(current, scope), _services(snapshot, scope)
    # A service in the VPN instance of a segment that is made not routed or renamed goes with it.
    renamed = {v for v in (wanted & set(cur_segs)) - replaced if scope.fabric and cur_segs[v]["name"] != snap_segs[v]["name"]}
    losing = {i for i, v in _instances(cur_segs).items() if v in remove_segments | regateway | renamed}
    delete_services = {n for n in cur_svc if n not in snap_svc or _vrf(cur_svc[n]) != _vrf(snap_svc[n])
                       or _vrf(cur_svc[n]) in losing
                       or any(cur_svc[n].get(f) != snap_svc[n].get(f) for f in SERVICE_FIELDS)}
    moved: dict[int, dict] = {}   # segments taken off a service that goes: vni -> their routing now
    for vni, segment in sorted(cur_segs.items()):
        routing = segment["routing"]
        # (also a segment that goes: the service is deleted before step 5 unroutes it)
        if routing and routing.get("firewall") in delete_services:
            moved[vni] = routing
            ops.append(op("route", f"segment {segment['name']}: no firewall for now (its service is replaced)",
                          vni=vni, firewall=None, peers=routing.get("peers") or [], subnet=None))
    for name in sorted(delete_services):
        ops.append(op("delete_service", f"delete service {name}", name=name))

    # 5. segments that go.
    for vni in sorted(remove_segments | regateway):
        segment = cur_segs[vni]
        if segment["routing"]:
            ops.append(op("unroute", f"segment {segment['name']}: not routed (frees {segment['routing']['gateway']})", vni=vni))
    for vni in sorted(remove_segments):
        ops.append(op("delete_segment", f"delete segment {cur_segs[vni]['name']} (VNI {vni})", vni=vni))

    # 6. trunks that go.
    cur_trunks = {(d, t["number"]): t for d, ts in (current.get("trunks") or {}).items() for t in ts if scope.has(d)}
    snap_trunks = {(d, t["number"]): t for d, ts in (snapshot.get("trunks") or {}).items() for t in ts if scope.has(d)}
    changed = {k for k in set(cur_trunks) & set(snap_trunks) if cur_trunks[k] != snap_trunks[k]}
    for device, number in _one_trunk((set(cur_trunks) - set(snap_trunks)) | changed, cur_trunks, scope):
        ops.append(op("delete_trunk", f"delete Eth-Trunk{number} on {device}", device=device, trunk=f"Eth-Trunk{number}"))

    # 7-8. segments renamed, created.
    if scope.fabric:
        for vni in sorted((wanted & set(cur_segs)) - replaced):
            if cur_segs[vni]["name"] != snap_segs[vni]["name"]:
                ops.append(op("rename", f"rename segment {cur_segs[vni]['name']} to {snap_segs[vni]['name']}",
                              vni=vni, name=snap_segs[vni]["name"]))
    for vni in sorted(create_segments):
        segment = snap_segs[vni]
        ops.append(op("create_segment", f"create segment {segment['name']} (VNI {vni}, bridge domain {segment['bd_id']})",
                      name=segment["name"], vni=vni, bd_id=segment["bd_id"]))

    # 9. trunks created.
    for device, number in _one_trunk((set(snap_trunks) - set(cur_trunks)) | changed, snap_trunks, scope):
        trunk = snap_trunks[(device, number)]
        ops.append(op("create_trunk", f"create {'M-LAG ' if trunk['m_lag'] else ''}Eth-Trunk{number} on {device} "
                      f"({', '.join(trunk['members'])})", device=device, number=number, members=trunk["members"],
                      mlag=trunk["m_lag"]))

    # 10. services created, ASNs set: those in their own instance first, then those joining another
    # service's; those in a segment's instance once the segment is routed (after step 11).
    in_segment = {n for n, s in snap_svc.items() if _vrf(s) in _instances(snap_segs)}
    later: list[str] = []
    for name in sorted(snap_svc, key=lambda n: (_vrf(snap_svc[n]) != n, n)):
        service = snap_svc[name]
        if name in delete_services or name not in cur_svc:
            if name in in_segment:
                later.append(name)
                continue
            ops.append(op("create_service", f"create service {name} on {service['device']} {service['port']} "
                          f"(VLAN {service['vlan']})" + ("" if _vrf(service) == name else f" in VPN instance {_vrf(service)}"),
                          service=service))
        else:
            if cur_svc[name].get("asn") != service.get("asn"):
                ops.append(op("service_asn", f"service {name}: firewall ASN {service.get('asn') or 'none'}",
                              name=name, asn=service.get("asn")))
            if cur_svc[name].get("ipv6") != service.get("ipv6"):
                six = service.get("ipv6")
                ops.append(op("service_ipv6", f"service {name}: IPv6 {six['address'] if six else 'off'}",
                              name=name, ipv6=six))

    # 11. routing of segments: gateways first (exact subnet), then peers.
    route_vnis = sorted(v for v in wanted if snap_segs[v]["routing"] and (
        v in create_segments or v in regateway or (scope.fabric and cur_segs.get(v, {}).get("routing") != snap_segs[v]["routing"])
        or (scope.fabric and v in moved)))
    for vni in route_vnis:
        routing = snap_segs[vni]["routing"]
        firewall = None if routing.get("firewall") in later else routing.get("firewall")   # made below
        ops.append(op("route", f"segment {snap_segs[vni]['name']}: routed, gateway {routing['gateway']}"
                      + (f", firewall {firewall}" if firewall else ""),
                      vni=vni, firewall=firewall, peers=[], subnet=routing["gateway"]))
    for vni in route_vnis:
        if snap_segs[vni]["routing"].get("peers"):
            names = ", ".join(snap_segs[p]["name"] if p in snap_segs else str(p) for p in snap_segs[vni]["routing"]["peers"])
            ops.append(op("route", f"segment {snap_segs[vni]['name']}: peered with {names}", vni=vni,
                          firewall=snap_segs[vni]["routing"].get("firewall"), peers=snap_segs[vni]["routing"]["peers"],
                          subnet=None))
    # Services in a segment's VPN instance, now that it is routed; then the segments they are firewall of.
    for name in later:
        service = snap_svc[name]
        ops.append(op("create_service", f"create service {name} on {service['device']} {service['port']} "
                      f"(VLAN {service['vlan']}) in segment instance {_vrf(service)}", service=service))
    for vni in sorted(v for v in wanted if (snap_segs[v]["routing"] or {}).get("firewall") in later):
        routing = snap_segs[vni]["routing"]
        ops.append(op("route", f"segment {snap_segs[vni]['name']}: firewall {routing['firewall']}", vni=vni,
                      firewall=routing["firewall"], peers=routing.get("peers") or [], subnet=None))
    for vni, routing in sorted(moved.items()):
        if vni in route_vnis or scope.fabric:
            continue
        # This switch's rollback replaced the service: the segment gets its firewall back if the
        # service is there again, otherwise it stays without one (said in the notes).
        firewall = routing["firewall"] if routing["firewall"] in snap_svc else None
        if firewall is None:
            notes.append(f"segment {cur_segs[vni]['name']} loses firewall {routing['firewall']} (not on this switch at the rollback point)")
        ops.append(op("route", f"segment {cur_segs[vni]['name']}: firewall {firewall or 'none'}", vni=vni,
                      firewall=firewall, peers=routing.get("peers") or [], subnet=None))
    # a routed segment's IPv6 as at the rollback point, after its routing and before its relay
    # (DHCPv6 servers use the relay loopback); a new /64 from the pool, not the old one.
    for vni in sorted(v for v in wanted if snap_segs[v]["routing"] and (v in create_segments or scope.fabric)):
        six = snap_segs[vni]["routing"].get("ipv6")
        now6 = ((cur_segs.get(vni) or {}).get("routing") or {}).get("ipv6")
        if (six or {}).get("hosts") != (now6 or {}).get("hosts") or (six or {}).get("servers") != (now6 or {}).get("servers") \
                or bool(six) != bool(now6):
            ops.append(op("ipv6", f"segment {snap_segs[vni]['name']}: IPv6 " + (f"on ({six['hosts']})" if six else "off"),
                          vni=vni, enabled=bool(six), hosts=(six or {}).get("hosts") or "slaac",
                          servers=(six or {}).get("servers") or []))
    # a routed segment's DHCP relay as at the rollback point, after its routing (it needs its
    # gateway and peers); a segment made again, or every segment of a fabric-wide restore.
    for vni in sorted(v for v in wanted if snap_segs[v]["routing"] and (v in create_segments or scope.fabric)):
        relay = snap_segs[vni]["routing"].get("relay")
        if relay != ((cur_segs.get(vni) or {}).get("routing") or {}).get("relay") or vni in create_segments and relay:
            servers = (relay or {}).get("servers") or []
            ops.append(op("relay", f"segment {snap_segs[vni]['name']}: DHCP relay "
                          + (", ".join(s["address"] for s in servers) if servers else "off"), vni=vni, servers=servers))
    if scope.fabric:
        for vni in sorted((wanted & set(cur_segs)) - set(route_vnis)):
            if cur_segs[vni]["routing"] and not snap_segs[vni]["routing"] and vni not in remove_segments | regateway:
                ops.append(op("unroute", f"segment {cur_segs[vni]['name']}: not routed", vni=vni))

    # 12. attachments added, routing targets, fabric settings.
    for key in _one_side({k for k in snap_att if k not in cur_att or k[2] in create_segments}, scope, mlag):
        device, interface, vni = key
        ops.append(op("attach", f"attach {snap_segs[vni]['name']} to {device} {interface}"
                      f" ({'untagged' if snap_att[key] is None else f'VLAN {snap_att[key]}'})",
                      device=device, interface=interface, vni=vni, vid=snap_att[key]))
    if "ports" in snapshot:   # port settings (older snapshots have no port settings: left as they are)
        from orchestrator.port_settings import DEFAULT as default   # older snapshots' values have no speed
        cur_ports, snap_ports = current.get("ports") or {}, snapshot["ports"]
        for device in sorted(set(cur_ports) | set(snap_ports)):
            if not scope.has(device):
                continue
            here, there = cur_ports.get(device) or {}, snap_ports.get(device) or {}
            for port in sorted(set(here) | set(there)):
                values = {**default, **there.get(port, {})}
                if {**default, **here.get(port, {})} != values:
                    ops.append(op("port", f"{device} {port}: settings as at the rollback point",
                                  device=device, port=port, values=values))
    for target in targets:
        ops.append(op("routing_set", f"routing of {target}: as at the rollback point", target=target,
                      routing=snap_routing.get(target) or {}))
    if scope.fabric:
        cur_fab, snap_fab = current.get("fabric") or {}, snapshot.get("fabric") or {}
        if (cur_fab.get("ntp_servers"), cur_fab.get("ntp_vpn_instance")) != (snap_fab.get("ntp_servers"), snap_fab.get("ntp_vpn_instance")):
            ops.append(op("ntp", "NTP servers as at the rollback point", servers=snap_fab.get("ntp_servers") or [],
                          vpn_instance=snap_fab.get("ntp_vpn_instance")))
        if cur_fab.get("snmp") != snap_fab.get("snmp"):
            snmp = snap_fab.get("snmp") or {}
            ops.append(op("snmp", "SNMP location, contact, v2c, readers and trap targets as at the rollback point",
                          location=snmp.get("location", ""), contact=snmp.get("contact", ""),
                          v2c=snmp.get("v2c", True), readers=snmp.get("readers") or {}, traps=snmp.get("traps") or {}))
        if cur_fab.get("netstream") != snap_fab.get("netstream"):
            ns = snap_fab.get("netstream") or {}
            ops.append(op("netstream", "NetStream " + (f"on, to {ns['host']}:{ns['port']}" if ns.get("enabled") else "off"),
                          enabled=bool(ns.get("enabled")), host=ns.get("host"), port=ns.get("port")))
        cur_sl, snap_sl = ({k: (f.get("syslog") or {}).get(k) for k in ("enabled", "host", "port")} for f in (cur_fab, snap_fab))
        if cur_sl != snap_sl:   # syslog (`owned` only grows: it is not restored)
            ops.append(op("syslog", "syslog " + (f"on, to {snap_sl['host']}:{snap_sl['port']}" if snap_sl["enabled"] else "off"),
                          enabled=bool(snap_sl["enabled"]), host=snap_sl["host"], port=snap_sl["port"]))
        if cur_fab.get("microsegmentation") != snap_fab.get("microsegmentation"):
            doc = snap_fab.get("microsegmentation") or {"mode": "off", "epgs": {}, "contracts": []}
            ops.append(op("microseg", f"microsegmentation {doc.get('mode') or 'off'}, {len(doc.get('contracts') or ())} "
                          "contract(s) as at the rollback point", document=doc))
        if cur_fab.get("qos") != snap_fab.get("qos"):
            doc = snap_fab.get("qos") or {"enabled": False, "segments": {}, "ports": {}}
            ops.append(op("qos", f"QoS {'on' if doc.get('enabled') else 'off'}, {len(doc.get('segments') or ())} segment "
                          f"and {sum(len(p) for p in (doc.get('ports') or {}).values())} port mark(s) as at the rollback point",
                          document=doc))
        shown = lambda d: {"mode": (d or {}).get("mode") or "off", "ports": (d or {}).get("ports") or {}}   # noqa: E731
        if shown(cur_fab.get("protection")) != shown(snap_fab.get("protection")):   # protection (`owned` stays as it is)
            doc = shown(snap_fab.get("protection"))
            ops.append(op("protection", f"port protection {doc['mode']}, {sum(len(p) for p in doc['ports'].values())} "
                          "protected port(s) as at the rollback point", document=doc))
        if (cur_fab.get("port_profiles") or {}).get("enabled") != (snap_fab.get("port_profiles") or {}).get("enabled"):
            on = bool((snap_fab.get("port_profiles") or {}).get("enabled"))
            ops.append(op("port_profiles", f"port settings {'managed' if on else 'not managed'} as at the rollback point",
                          enabled=on))
        if "dci" in snapshot and (current.get("dci") or []) != snapshot["dci"]:
            ops.append(op("dci", f"DC links as at the rollback point ({', '.join(l['name'] for l in snapshot['dci']) or 'none'})",
                          links=snapshot["dci"]))
        if snapshot.get("overrides") is not None and current.get("overrides") != snapshot["overrides"]:
            ops.append(op("overrides", "what the fabric keeps as found, as at the rollback point",
                          document=snapshot["overrides"]))
        if snap_fab.get("gateway_mac") and cur_fab.get("gateway_mac") != snap_fab.get("gateway_mac"):
            ops.append(op("gateway_mac", f"gateway MAC {snap_fab['gateway_mac']}", mac=snap_fab["gateway_mac"]))
    return {"operations": ops, "notes": notes}


def _one_trunk(keys, trunks: dict, scope: Scope) -> list[tuple[str, int]]:
    """An M-LAG trunk is created and deleted on both leaves by one operation: keep one side."""
    kept = []
    for device, number in sorted(keys):
        peer = scope.peers.get(device)
        if trunks[(device, number)]["m_lag"] and peer is not None and peer < device and (peer, number) in keys:
            continue
        kept.append((device, number))
    return kept


async def scope_for(nb, fabric: str, device: str | None) -> Scope:
    """The rollback's scope: the fabric, or one switch with its M-LAG peer and routing target."""
    from orchestrator.routing_intent import RoutingStore
    from orchestrator.segments import SegmentError, _cf, _ref_id

    devices = await nb.find("dcim/devices", site=fabric)
    names = {d["id"]: d["name"] for d in devices}
    peers = {d["name"]: names[_ref_id(_cf(d, "mlag_peer"))] for d in devices if _ref_id(_cf(d, "mlag_peer")) in names}
    if device is None:
        return Scope(peers=peers)
    if device not in names.values():
        raise SegmentError(f"no switch {device!r} in {fabric}")
    _, target, pair = await RoutingStore(nb).target_of(device)
    return Scope(devices=set(pair) or {device}, target=target or device, peers=peers)


async def preview(nb, store, fabric: str, snapshot_id: int, device: str | None) -> dict:
    """What restoring the rollback point would do to intent; nothing is written."""
    from orchestrator.intent_snapshots import capture_intent
    from orchestrator.segments import SegmentError

    snapshot = await store.get(fabric, snapshot_id)
    if snapshot is None:
        raise SegmentError(f"no rollback point {snapshot_id} of {fabric}")
    scope = await scope_for(nb, fabric, device)
    result = plan(await capture_intent(nb, fabric), snapshot["data"], scope)
    return {"fabric": fabric, "snapshot": {k: v for k, v in snapshot.items() if k != "data"},
            "documents": snapshot["data"].get("documents") or {},   # versions it returns to (none before they were kept)
            "scope": "fabric" if scope.fabric else sorted(scope.devices), **result}


async def apply(nb, store, fabric: str, snapshot_id: int, device: str | None, expected: int) -> dict:
    """Restore the rollback point: plan again now, check it is still what the person saw
    (`expected` operations), then run each operation through the orchestrator's own checks,
    stopping at the first refusal. What was done stays done (it is intent; the pending bar shows it)."""
    from orchestrator.segments import SegmentError

    planned = await preview(nb, store, fabric, snapshot_id, device)
    if len(planned["operations"]) != expected:
        raise SegmentError(f"intent changed since the preview ({len(planned['operations'])} operations now, "
                           f"{expected} shown): preview again")
    return await execute(nb, fabric, planned)


async def execute(nb, fabric: str, planned: dict) -> dict:
    """Run a plan's operations in order, each through the orchestrator's own checks, stopping at
    the first refusal (rollbacks, and intent files: orchestrator/intent_file.py)."""
    from orchestrator import fabric_ntp
    from orchestrator.routing import Routing
    from orchestrator.routing_intent import DeviceRouting, RoutingStore
    from orchestrator.segments import SegmentError, Segments
    from orchestrator.trunks import Trunks

    ops = planned["operations"]
    segments, routing, trunks, routing_store = Segments(nb), Routing(nb), Trunks(nb), RoutingStore(nb)

    async def run(o: dict) -> None:
        kind = o["kind"]
        if kind == "routing_set":
            await routing_store.set_device(fabric, o["target"], DeviceRouting.model_validate(o["routing"]))
        elif kind == "detach":
            await segments.detach(fabric, o["device"], o["interface"], o["vni"])
        elif kind == "set_vid":
            await segments.set_vid(fabric, o["device"], o["interface"], o["vni"], o["vid"])
        elif kind == "attach":
            await segments.attach(fabric, o["device"], o["interface"], o["vni"], o["vid"])
        elif kind == "route":
            await routing.set_routing(fabric, o["vni"], True, firewall=o["firewall"], peers=o["peers"], subnet=o["subnet"])
        elif kind == "unroute":
            await routing.set_routing(fabric, o["vni"], False)
        elif kind == "delete_service":
            await routing.delete_service(fabric, o["name"])
        elif kind == "service_asn":
            await routing.set_service_asn(fabric, o["name"], o["asn"])
        elif kind == "create_service":
            s = o["service"]
            await routing.create_service(fabric, s["name"], s["device"], s["port"], s["vlan"], s["address"],
                                         s["next_hop"], s.get("asn"), s.get("kind") or "firewall", s.get("addresses"),
                                         s.get("ipv6"), None if _vrf(s) == s["name"] else _vrf(s))
        elif kind == "service_ipv6":
            await routing.set_service_ipv6(fabric, o["name"], o["ipv6"])
        elif kind == "delete_segment":
            await segments.delete(fabric, o["vni"])
        elif kind == "rename":
            await segments.rename(fabric, o["vni"], o["name"])
        elif kind == "create_segment":
            await segments.create(fabric, o["name"], o["vni"], o["bd_id"])
        elif kind == "delete_trunk":
            await trunks.delete(o["device"], o["trunk"])
        elif kind == "create_trunk":
            await trunks.create(o["device"], o["number"], o["members"], o["mlag"])
        elif kind == "ntp":
            await fabric_ntp.set_ntp(nb, fabric, o["servers"], o["vpn_instance"])
        elif kind == "snmp":
            await fabric_ntp.set_snmp(nb, fabric, o["location"], o["contact"])
            from orchestrator import snmp_access   # SNMP (passwords are not intent: kept as they are)

            await snmp_access.restore(nb, fabric, o.get("v2c", True), o.get("readers") or {}, o.get("traps") or {})
        elif kind == "relay":
            from orchestrator import dhcp_relay

            await dhcp_relay.set_relay(nb, fabric, o["vni"], o["servers"])
        elif kind == "ipv6":
            from orchestrator import ipv6

            await ipv6.set_ipv6(nb, fabric, o["vni"], o["enabled"], o["hosts"], o["servers"])
        elif kind == "netstream":
            await fabric_ntp.set_netstream(nb, fabric, o["enabled"], o["host"], o["port"])
        elif kind == "syslog":
            await fabric_ntp.set_syslog(nb, fabric, o["enabled"], o["host"], o["port"])
        elif kind == "microseg":
            from orchestrator import microseg

            await microseg.restore(nb, fabric, o["document"])
        elif kind == "dci":
            from orchestrator import dci

            await dci.restore(nb, fabric, o["links"])
        elif kind == "qos":
            from orchestrator import qos

            await qos.restore(nb, fabric, o["document"])
        elif kind == "protection":
            from orchestrator import protection

            await protection.restore(nb, fabric, o["document"])
        elif kind == "port":
            from orchestrator import port_settings

            await port_settings.restore(nb, o["device"], o["port"], o["values"])
        elif kind == "port_profiles":
            from orchestrator import port_settings

            await port_settings.restore_enabled(nb, fabric, o["enabled"])
        elif kind == "overrides":
            from orchestrator import overrides

            await overrides.restore(nb, fabric, o["document"])
        elif kind == "gateway_mac":
            await fabric_ntp.set_gateway_mac(nb, fabric, o["mac"])
        else:
            raise SegmentError(f"unknown rollback operation {kind!r}")

    done = []
    for index, o in enumerate(ops):
        try:
            await run(o)
        except SegmentError as exc:
            return {**planned, "done": done, "failed": {"text": o["text"], "error": str(exc)},
                    "remaining": [r["text"] for r in ops[index + 1:]]}
        done.append(o["text"])
    return {**planned, "done": done, "failed": None, "remaining": []}
