"""Hardware state over NETCONF: optical modules, fans, power modules, CPU,
memory and temperatures.

Paths from a test switch's own replies (probe). Read-only: <get>, and one fixed
`display` command through huawei-cli (no temperature in any YANG module the switch answers).

Two groups, read in alternate state slots (each every 50 s): `environment`
(fans, power, CPU, memory, temperatures; ~1.8 s on a test switch) and `optics` (~1.7 s). A query the
switch refuses leaves out that part only.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

DEVM = "urn:huawei:yang:huawei-devm"
PIC = "urn:huawei:yang:huawei-pic"
DRIVER = "urn:huawei:yang:huawei-driver"
CPU_MEMORY = "urn:huawei:yang:huawei-cpu-memory"
CLI = "urn:huawei:yang:huawei-cli"
SYSTEM = "urn:huawei:yang:huawei-system"

TEMPERATURE_COMMAND = "display device temperature"   # with `all`: "Too many parameters" (V300R023)

QUERIES = {
    "optics": f'<devm xmlns="{DEVM}"><ports><port><position/><optical-module xmlns="{PIC}"/></port></ports></devm>',
    "fans-power": f'<devm xmlns="{DEVM}"><driver xmlns="{DRIVER}"><fans/><pms/></driver></devm>',
    "cpu-memory": f'<cpu-memory xmlns="{CPU_MEMORY}"><board-cpu-infos/><board-memory-infos/></cpu-memory>',
    "temperature": None,   # TEMPERATURE_COMMAND, not a subtree
    # what the switch is and runs (model, release, patch)
    "system": f'<system xmlns="{SYSTEM}"><system-info><hardware-model/><product-name/><product-version/><patch-version/>'
              f'</system-info></system>',
}
GROUPS = {"environment": ("system", "fans-power", "cpu-memory", "temperature"), "optics": ("optics",)}
TEMPERATURE_RPC = (f'<execute-command xmlns="{CLI}"><cli-cmds><cli-cmd><sequence>1</sequence>'
                   f'<command>{TEMPERATURE_COMMAND}</command></cli-cmd></cli-cmds></execute-command>')

# The module's own thresholds. devm gives warning and alarm levels for the
# powers, one pair for temperature, voltage and bias: read as alarm levels.
OPTIC_THRESHOLDS = {
    ("rx_power", "high_warn"): "rx-high-warn-power", ("rx_power", "low_warn"): "rx-low-warn-power",
    ("rx_power", "high_alarm"): "rx-high-alarm-power", ("rx_power", "low_alarm"): "rx-low-alarm-power",
    ("tx_power", "high_warn"): "tx-high-warn-power", ("tx_power", "low_warn"): "tx-low-warn-power",
    ("tx_power", "high_alarm"): "tx-high-alarm-power", ("tx_power", "low_alarm"): "tx-low-alarm-power",
    ("temperature", "high_alarm"): "temperature-high-threshold-float",
    ("temperature", "low_alarm"): "temperature-low-threshold-float",
    ("voltage", "high_alarm"): "voltage-high-threshold-float", ("voltage", "low_alarm"): "voltage-low-threshold-float",
    ("bias", "high_alarm"): "bias-high-threshold", ("bias", "low_alarm"): "bias-low-threshold",
}


@dataclass(frozen=True)
class Optic:
    vendor: str
    type: str
    part: str
    serial: str
    temperature: float | None
    voltage: float | None
    channels: tuple[tuple[str, float | None, float | None, float | None], ...]   # (number, tx dBm, rx dBm, bias mA)
    thresholds: dict = field(default_factory=dict, hash=False)   # (measure, level) -> value


@dataclass(frozen=True)
class Sensor:
    slot: str          # "1", "PWR1"
    name: str          # TEMP_A, CPU, NP0; "power" where the switch shows "--"
    status: str        # Normal, ...; "" where the switch shows "--"
    celsius: float
    thresholds: dict = field(default_factory=dict, hash=False)   # minor, major, fatal


@dataclass
class HardwareState:
    optics: dict[str, Optic] = field(default_factory=dict)                 # port -> module
    fans: dict[str, tuple[bool, int | None, int | None]] = field(default_factory=dict)   # FAN:1/1 -> (present, %, rpm)
    power: dict[str, tuple[str, float | None, float | None]] = field(default_factory=dict)  # PWR:1/1 -> (status, in W, out W)
    cpu: dict[str, tuple[float, float | None]] = field(default_factory=dict)      # slot -> (usage %, overload threshold %)
    memory: dict[str, tuple[float, float | None]] = field(default_factory=dict)   # slot -> (usage %, overload threshold %)
    sensors: list[Sensor] = field(default_factory=list)
    system: tuple[str, str, str] | None = None   # (model, release, patch)
    missing: set[str] = field(default_factory=set)   # queries the switch refused


def _t(element, ns: str, name: str) -> str:
    found = element.find(f"{{{ns}}}{name}")
    return (found.text or "").strip() if found is not None else ""


def _num(text: str) -> float | None:
    """A reading, or None for the switch's "--" (no input) and anything else not a number."""
    try:
        return float(text)
    except ValueError:
        return None


def _int(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def parse_optics(xml: str, state: HardwareState) -> None:
    for port in ET.fromstring(xml).iter(f"{{{DEVM}}}port"):
        module = port.find(f"{{{PIC}}}optical-module")
        if module is None:
            continue
        channels = tuple((_t(c, PIC, "number"), _num(_t(c, PIC, "tx-power")), _num(_t(c, PIC, "rx-power")),
                          _num(_t(c, PIC, "bias"))) for c in module.iter(f"{{{PIC}}}channel"))
        thresholds = {key: value for key, tag in OPTIC_THRESHOLDS.items()
                      if (value := _num(_t(module, PIC, tag))) is not None}
        state.optics[_t(port, DEVM, "position")] = Optic(
            vendor=_t(module, PIC, "vendor-name"), type=_t(module, PIC, "type"), part=_t(module, PIC, "vendor-pn"),
            serial=_t(module, PIC, "serial-number"), temperature=_num(_t(module, PIC, "temperature-float")),
            voltage=_num(_t(module, PIC, "voltage-float")), channels=channels, thresholds=thresholds)


def parse_fans_power(xml: str, state: HardwareState) -> None:
    root = ET.fromstring(xml)
    for fan in root.iter(f"{{{DRIVER}}}fan"):
        state.fans[_t(fan, DRIVER, "position")] = (_t(fan, DRIVER, "present") == "yes", _int(_t(fan, DRIVER, "speed")),
                                                   _int(_t(fan, DRIVER, "fan-rate")))
    for pm in root.iter(f"{{{DRIVER}}}pm"):
        state.power[_t(pm, DRIVER, "position")] = (_t(pm, DRIVER, "pm-status"), _num(_t(pm, DRIVER, "input-power-value")),
                                                   _num(_t(pm, DRIVER, "output-power-value")))


def parse_cpu_memory(xml: str, state: HardwareState) -> None:
    root = ET.fromstring(xml)
    for target, tag, usage in ((state.cpu, "board-cpu-info", "system-cpu-usage"),
                               (state.memory, "board-memory-info", "os-memory-usage")):
        for board in root.iter(f"{{{CPU_MEMORY}}}{tag}"):
            value = _num(_t(board, CPU_MEMORY, usage))
            if value is not None:
                target[_t(board, CPU_MEMORY, "slot-id")] = (value, _num(_t(board, CPU_MEMORY, "overload-threshold")))


SLOT = re.compile(r"Unit: celsius, Slot ([^,\s]+)")
ROW = re.compile(r"^(\S+)\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(-?\d+(?:\.\d+)?)\s*$")


def parse_temperature(reply_xml: str, state: HardwareState) -> None:
    """`display device temperature` rows, per `Slot` section (seen on a test switch: board sensors with
    Minor/Major/Fatal thresholds, power supplies with a temperature only)."""
    root = ET.fromstring(reply_xml)
    if any(_t(r, CLI, "execute-result") != "success" for r in root.iter(f"{{{CLI}}}cli-result")):
        state.missing.add("temperature")
        return
    slot = ""
    for line in "".join(e.text or "" for e in root.iter(f"{{{CLI}}}cmd-output-info")).splitlines():
        if (heading := SLOT.search(line)) is not None:
            slot = heading.group(1)
        elif (row := ROW.match(line)) is not None and slot:
            _, name, status, minor, major, fatal, celsius = row.groups()
            thresholds = {level: v for level, text in (("minor", minor), ("major", major), ("fatal", fatal))
                          if (v := _num(text)) is not None}
            state.sensors.append(Sensor(slot, "power" if name == "--" else name, "" if status == "--" else status,
                                        float(celsius), thresholds))


def model(hardware_model: str, product_name: str) -> str:
    """The switch's model: an eNSP CE reports hardware-model "CE", product-name "CE6800"."""
    return hardware_model if re.search(r"\d", hardware_model) else (product_name or hardware_model)


def parse_system(xml: str, state: HardwareState) -> None:
    info = ET.fromstring(xml).find(f".//{{{SYSTEM}}}system-info")
    if info is not None and _t(info, SYSTEM, "hardware-model"):
        state.system = (model(_t(info, SYSTEM, "hardware-model"), _t(info, SYSTEM, "product-name")),
                        _t(info, SYSTEM, "product-version"),
                        _t(info, SYSTEM, "patch-version"))


PARSERS = {"system": parse_system, "optics": parse_optics, "fans-power": parse_fans_power, "cpu-memory": parse_cpu_memory,
           "temperature": parse_temperature}


def parse(replies: dict[str, str | None]) -> HardwareState:
    """`replies`: query name -> its reply, or None when the switch refused that query."""
    state = HardwareState()
    for name, xml in replies.items():
        if xml is None:
            state.missing.add(name)
        else:
            PARSERS[name](xml, state)
    return state
