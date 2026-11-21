"""The drawn front panel: every front-panel port of a switch and what it carries. Read only."""

import asyncio

from orchestrator.faceplate import faceplate
from orchestrator.routing import Routing
from orchestrator.segments import Segments
from tests.collector.test_collector import example_netbox


def run(coro):
    return asyncio.run(coro)


def test_every_front_panel_port_says_what_it_carries():
    nb = example_netbox()
    segments = Segments(nb)
    run(segments.create("example", "web", 10500, 500))
    run(segments.attach("example", "leaf1", "10GE1/0/3", 10500, None))
    run(Routing(nb).create_service("example", "FW", "leaf1", "10GE1/0/4", 3000, "10.255.0.1/30", "10.255.0.2"))

    plate = run(faceplate(nb, "leaf1"))
    ports = {p["name"]: p for p in plate["ports"]}
    assert plate["model"] == "CE6881H-48S6CQ"
    assert len(ports) == 54 and "MEth0/0/0" not in ports and "Eth-Trunk1" not in ports   # 48 x 10GE + 6 x 100GE
    assert ports["100GE1/0/1"]["kind"] == "fabric" and ports["100GE1/0/1"]["detail"] == "to spine1 100GE1/0/1"
    assert ports["100GE1/0/5"]["kind"] == "peer" and ports["100GE1/0/5"]["trunk"] == "Eth-Trunk0"
    assert ports["10GE1/0/1"]["kind"] == "trunk" and ports["10GE1/0/1"]["detail"] == "Eth-Trunk1 (M-LAG)"
    assert ports["10GE1/0/3"]["kind"] == "segment" and ports["10GE1/0/3"]["segments"] == [{"vni": 10500, "vid": None}]
    assert ports["10GE1/0/4"]["kind"] == "service" and ports["10GE1/0/4"]["detail"] == "service FW"
    assert ports["10GE1/0/12"]["kind"] == "free"
    # an optic's level is a problem only on a port in use.
    assert [n for n in ("100GE1/0/1", "100GE1/0/5", "10GE1/0/1", "10GE1/0/3", "10GE1/0/4") if not ports[n]["in_use"]] == []
    assert ports["10GE1/0/12"]["in_use"] is False and ports["100GE1/0/1"]["cabled"] is True
    assert run(faceplate(nb, "spine1"))["ports"]   # spines have a panel too (trunks on spines)
