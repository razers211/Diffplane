"""One syslog datagram as the switches send it: RFC 3164 or 5424 framing, `<PRI>` first.

VRP's own message text starts with its module and severity (`%%01IFNET/4/LINK_STATE(l):...`);
that tag is kept apart for the Logs tab. The switch's own timestamp is not trusted (time zones,
clock drift): the receive time is stored.
"""

import re

PRI = re.compile(rb"^<(\d{1,3})>")
# RFC 5424: version, timestamp, host, app, procid, msgid, structured data (or "-"), message.
RFC5424 = re.compile(r"^1 \S+ (?P<host>\S+) \S+ \S+ \S+ (?:-|(?:\[.*?\])+) ?(?P<msg>.*)$", re.S)
# RFC 3164 and VRP: "Mmm dd [yyyy ]hh:mm:ss host message".
RFC3164 = re.compile(r"^[A-Z][a-z]{2} +\d{1,2} (?:\d{4} )?\d{2}:\d{2}:\d{2}(?:\.\d+)?(?: [+-]\d{2}:\d{2})? (?P<host>\S+) (?P<msg>.*)$", re.S)
VRP_TAG = re.compile(r"^%%\d*(?P<tag>[A-Z0-9_-]+/\d/[A-Z0-9_]+)(?:\([a-z]+\))?(?:\[\d+\])?:\s*(?P<msg>.*)$", re.S)
MAX_TEXT = 4096


def parse(datagram: bytes) -> dict | None:
    """{"severity", "facility", "host", "tag", "message"}, or None when it is not syslog."""
    m = PRI.match(datagram)
    if m is None or int(m.group(1)) > 191:
        return None
    pri = int(m.group(1))
    text = datagram[m.end():].decode("utf-8", "replace").strip()
    host, msg = "", text
    for form in (RFC5424, RFC3164):
        found = form.match(text)
        if found:
            host, msg = found.group("host"), found.group("msg")
            break
    tag = ""
    found = VRP_TAG.match(msg)
    if found:
        tag, msg = found.group("tag"), found.group("msg")
    return {"severity": pri & 7, "facility": pri >> 3, "host": host, "tag": tag, "message": msg.strip()[:MAX_TEXT]}
