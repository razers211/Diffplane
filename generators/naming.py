"""Interface names per switch vendor.

NetBox keeps each switch's own names, so code that creates a name asks for the vendor's form,
and code that reads one recognises every vendor's (the forms never collide). Vendors are
Annet's names (`device.hw.vendor`): "huawei" or "arista".

Huawei VRP: verified on the lab switch. Arista EOS: from Arista's documentation, unverified
until the EVE-NG lab (ROADMAP.md, Second vendor).
"""

import re

HUAWEI, ARISTA = "huawei", "arista"
VENDORS = (HUAWEI, ARISTA)

_LOOPBACK = {HUAWEI: "LoopBack", ARISTA: "Loopback"}
_TRUNK = {HUAWEI: "Eth-Trunk", ARISTA: "Port-Channel"}
_TRUNK_RE = re.compile(r"(?:Eth-Trunk|Port-Channel)(\d+)")
_MANAGEMENT_RE = re.compile(r"MEth\d+/\d+/\d+|Management\d+(?:/\d+)?")
# VRP 10GE1/0/4, 100GE1/0/1; EOS Ethernet1, Ethernet49/1. Never a sub-interface (.N).
_FRONT_PANEL_RE = re.compile(r"\d{0,3}GE\d+/\d+/\d+|Ethernet\d+(?:/\d+){0,2}")


def vendor_of(device) -> str:
    """The switch's vendor, as Annet names it ("huawei" when it cannot tell)."""
    hw = getattr(device, "hw", None)
    vendor = getattr(hw, "vendor", None) if hw is not None else None
    return vendor if vendor in VENDORS else HUAWEI


def _check(vendor: str) -> str:
    if vendor not in VENDORS:
        raise ValueError(f"unknown switch vendor {vendor!r}")
    return vendor


def loopback(vendor: str, number: int) -> str:
    return f"{_LOOPBACK[_check(vendor)]}{number}"


def trunk(vendor: str, number: int) -> str:
    return f"{_TRUNK[_check(vendor)]}{number}"


def trunk_number(name: str) -> int | None:
    """Eth-Trunk7 / Port-Channel7 -> 7; anything else None."""
    match = _TRUNK_RE.fullmatch(name)
    return int(match.group(1)) if match else None


def is_trunk(name: str) -> bool:
    return trunk_number(name) is not None


def is_management(name: str) -> bool:
    return _MANAGEMENT_RE.fullmatch(name) is not None


def is_front_panel(name: str) -> bool:
    return _FRONT_PANEL_RE.fullmatch(name) is not None
