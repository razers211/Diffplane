"""Hardware state: parsed from leaf1's recorded replies (probe, temperature)."""

import asyncio
from pathlib import Path

import collector.__main__ as main
from collector import hardware
from collector.metrics import FabricCollector
from tests.collector.test_collector import exposition
from tests.collector.test_control import ControlSession, refused, value

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(name: str) -> str:
    return (FIXTURES / f"m32-{name}.xml").read_text()


EXAMPLE = {name: recorded(name) for name in ("optics", "fans-power", "cpu-memory", "temperature")}
EXAMPLE["system"] = (FIXTURES / "inventory-system-fibre.xml").read_text()   # model, release, patch


def test_leaf1_optics_with_the_modules_own_thresholds():
    state = hardware.parse({"optics": EXAMPLE["optics"]})
    assert len(state.optics) == 19 and "MEth0/0/0" not in state.optics   # ports without a module are left out
    optic = state.optics["10GE1/0/5"]
    assert (optic.vendor, optic.type, optic.part, optic.serial) == ("HUAWEI", "sfp-plus", "02313URC", "SN0000000001")
    assert optic.temperature == 21.82 and optic.voltage == 3.29
    assert optic.channels == (("0", -2.04, -40.0, 5.84),)                   # no fibre: -40 dBm received
    assert optic.thresholds[("rx_power", "low_alarm")] == -11.9 and optic.thresholds[("bias", "high_alarm")] == 20.0
    assert len(optic.thresholds) == len(hardware.OPTIC_THRESHOLDS)


def test_leaf1_fans_power_cpu_memory():
    state = hardware.parse({"fans-power": EXAMPLE["fans-power"], "cpu-memory": EXAMPLE["cpu-memory"]})
    assert state.fans["FAN:1/1"] == (True, 40, 9900) and len(state.fans) == 4
    assert state.power == {"PWR:1/1": ("abnormal", None, None), "PWR:1/2": ("normal", 157.0, 143.0)}   # 1/1 not fed
    assert state.cpu == {"1": (20.0, 90.0)} and state.memory == {"1": (44.0, 92.0)}


def test_leaf1_temperatures_with_the_switchs_thresholds():
    state = hardware.parse({"temperature": EXAMPLE["temperature"]})
    sensors = {(s.slot, s.name): s for s in state.sensors}
    assert set(sensors) == {("1", "TEMP_A"), ("1", "TEMP_B"), ("1", "CPU"), ("1", "NP0"), ("1", "NP1"),
                            ("PWR1", "power"), ("PWR2", "power")}
    assert sensors[("1", "NP1")].celsius == 46 and sensors[("1", "NP1")].thresholds == {"minor": 103, "major": 108, "fatal": 115}
    assert sensors[("1", "TEMP_A")].status == "Normal"
    assert sensors[("PWR2", "power")].celsius == 50 and sensors[("PWR2", "power")].status == ""
    assert sensors[("PWR2", "power")].thresholds == {}


def test_a_failed_temperature_command_is_left_out():
    failed = EXAMPLE["temperature"].replace("<execute-result>success", "<execute-result>failed")
    state = hardware.parse({"temperature": failed})
    assert state.sensors == [] and state.missing == {"temperature"}


def test_leaf1_hardware_as_metrics():
    metrics = FabricCollector()
    metrics.record_hardware("leaf1", "environment", hardware.parse(
        {"fans-power": EXAMPLE["fans-power"], "cpu-memory": EXAMPLE["cpu-memory"], "temperature": EXAMPLE["temperature"]}), 1.7)
    metrics.record_hardware("leaf1", "optics", hardware.parse({"optics": EXAMPLE["optics"]}), 1.6)
    text = exposition(metrics)
    port = dict(device="leaf1", interface="10GE1/0/5")
    assert value(text, "fabric_optic_rx_power_dbm", **port, channel="0") == -40
    assert value(text, "fabric_optic_threshold", **port, measure="rx_power", level="low_alarm") == -11.9
    assert value(text, "fabric_power_module_up", device="leaf1", module="PWR:1/1") == 0
    assert value(text, "fabric_power_module_input_watts", device="leaf1", module="PWR:1/2") == 157
    assert value(text, "fabric_power_module_input_watts", device="leaf1", module="PWR:1/1") is None
    assert value(text, "fabric_fan_rpm", device="leaf1", fan="FAN:1/3") == 9900
    assert value(text, "fabric_temperature_celsius", device="leaf1", slot="1", sensor="CPU") == 31
    assert value(text, "fabric_temperature_threshold_celsius", device="leaf1", slot="1", sensor="CPU", level="major") == 108
    assert value(text, "fabric_temperature_sensor_normal", device="leaf1", slot="PWR1", sensor="power") is None
    assert value(text, "fabric_cpu_usage_percent", device="leaf1", slot="1") == 20
    assert value(text, "fabric_collector_poll_duration_seconds", device="leaf1", poll="optics") == 1.6


class FakeConn:
    connected = True

    def __init__(self, refuse=()):
        self.refuse = set(refuse)

    def get(self, filter):
        name = next(n for n, q in hardware.QUERIES.items() if q == filter[1])
        if name in self.refuse:
            raise refused()
        return type("Reply", (), {"data_xml": EXAMPLE[name]})()

    def dispatch(self, rpc):
        if "temperature" in self.refuse:
            raise refused()
        return type("Reply", (), {"xml": EXAMPLE["temperature"]})()


def test_a_refused_query_leaves_out_that_part_only():
    session = main.Session(target=None, login="x", password="y")
    session.conn = FakeConn(refuse={"cpu-memory"})
    parts = session.get_hardware("environment")
    assert parts.missing == {"cpu-memory"} and parts.fans and parts.sensors and parts.cpu == {}
    assert parts.system == ("CE6881H-48S6CQ", "V300R025C00SPC500", "V300R025SPH120")
    session.conn = FakeConn()
    assert len(session.get_hardware("optics").optics) == 19


class HardwareSession(ControlSession):
    def __init__(self):
        super().__init__()
        self.groups = []

    def get_hardware(self, group):
        self.groups.append(group)
        return hardware.HardwareState()


def test_hardware_groups_take_turns_in_state_slots():
    """environment and optics alternate after state polls, each every 50 s."""
    collector = main.Collector(FabricCollector(), "x", "y", counter_interval=5, state_interval=25, hardware_interval=50)
    assert collector.hardware_every == 5
    metrics = FabricCollector()
    session = HardwareSession()
    asyncio.run(main.poll_device("leaf1", session, metrics, 0, 5, 0, attempts=16, control_every=5, hardware_every=5))
    assert session.groups == ["environment", "optics", "environment", "optics"]   # ticks 0, 5, 10, 15
    snap = metrics.snapshots["leaf1"]
    assert snap.polls["environment"] == 2 and snap.polls["optics"] == 2 and set(snap.hardware) == {"environment", "optics"}
    metrics.record_success("leaf1", snap.state)    # carried over by the other polls
    assert set(metrics.snapshots["leaf1"].hardware) == {"environment", "optics"}


def test_hardware_off_by_default():
    assert main.Collector(FabricCollector(), "x", "y", counter_interval=5, state_interval=25).hardware_every is None


def test_a_model_without_digits_is_reported_by_its_product_name():
    """The check port-layout compares this with NetBox, which switch_inventory fills the same way."""
    assert hardware.model("CE", "CE6800") == "CE6800"
    assert hardware.model("CE6881H-48S6CQ", "CE6881H") == "CE6881H-48S6CQ"
