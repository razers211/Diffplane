"""The syslog receiver: parsing what switches send, keeping only known switches."""

from datetime import datetime, timezone

import pytest

from syslogs.__main__ import row
from syslogs.parse import parse

VRP = b"<188>Oct 22 2026 10:00:01 leaf1 %%01IFNET/4/LINK_STATE(l)[12]:The line protocol IP on the interface 10GE1/0/10 has entered the DOWN state."


def test_a_vrp_message_keeps_its_severity_module_tag_and_text():
    assert parse(VRP) == {"severity": 4, "facility": 23, "host": "leaf1", "tag": "IFNET/4/LINK_STATE",
                          "message": "The line protocol IP on the interface 10GE1/0/10 has entered the DOWN state."}


@pytest.mark.parametrize("datagram, expected", [
    (b"<13>Oct  2 08:01:02 host app: hello", {"severity": 5, "facility": 1, "host": "host", "tag": "", "message": "app: hello"}),
    (b"<165>1 2026-10-22T10:00:00.003Z sw1 app 12 ID47 - text here",
     {"severity": 5, "facility": 20, "host": "sw1", "tag": "", "message": "text here"}),
    (b"<165>1 2026-10-22T10:00:00Z sw1 app - - [x@1 a=\"b\"] with data", {"severity": 5, "facility": 20, "host": "sw1",
                                                                        "tag": "", "message": "with data"}),
    (b"<12>no header at all", {"severity": 4, "facility": 1, "host": "", "tag": "", "message": "no header at all"}),
])
def test_rfc3164_and_rfc5424_forms(datagram, expected):
    assert parse(datagram) == expected


@pytest.mark.parametrize("datagram", [b"hello", b"<999>too big", b""])
def test_what_is_not_syslog_is_refused(datagram):
    assert parse(datagram) is None


def test_only_known_switches_are_kept_with_the_receive_time():
    switches = {"172.24.0.11": ("leaf1", "example")}
    now = datetime(2026, 10, 22, 10, 0, 1, 250000, tzinfo=timezone.utc)
    assert row(VRP, "172.24.0.11", switches, now) == {
        "time": "2026-10-22 10:00:01.250", "site": "example", "device": "leaf1", "address": "172.24.0.11",
        "severity": 4, "facility": 23, "tag": "IFNET/4/LINK_STATE",
        "message": "The line protocol IP on the interface 10GE1/0/10 has entered the DOWN state."}
    assert row(VRP, "172.24.0.99", switches, now) is None


class FakeClickHouse:
    cluster = None

    def __init__(self):
        self.asked = []

    async def rows(self, sql, **params):
        self.asked.append((sql, params))
        return [{"time": "2026-10-22 10:00:01.250", "severity": 4, "facility": 23, "tag": "IFNET/4/LINK_STATE", "message": "down"}]


def test_a_fabrics_logs_are_asked_by_site_with_each_switch_named():
    import asyncio

    from syslogs import store

    ch = FakeClickHouse()
    asyncio.run(store.messages(ch, None, 4, "", 24, 100, site="site-a"))
    sql, params = ch.asked[0]
    assert params == {"site": "site-a", "severity": 4, "hours": 24, "text": ""}
    assert "WHERE site = {site:String}" in sql and "SELECT time, device," in sql and "LIMIT 100" in sql


def test_a_switchs_logs_are_asked_by_name_severity_and_text():
    import asyncio

    from syslogs import store

    ch = FakeClickHouse()
    rows = asyncio.run(store.messages(ch, "leaf1", 4, "10GE", 24))
    assert rows[0]["severity_name"] == "warning"
    sql, params = ch.asked[0]
    assert params == {"device": "leaf1", "severity": 4, "hours": 24, "text": "10GE"} and "ORDER BY time DESC" in sql
    assert " AS time" not in sql   # an alias named like the column replaces it in WHERE (ClickHouse NO_COMMON_TYPE)
    with pytest.raises(ValueError, match="severity"):
        asyncio.run(store.messages(ch, "leaf1", 9))
