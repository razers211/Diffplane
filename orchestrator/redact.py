"""Mask secrets in text the API returns (generated config, diffs, errors)."""

import os
import re

# Environment variables whose values generators put into config, and the account passwords the
# ZTP first-boot config holds.
SECRET_ENV = ("SNMP_COMMUNITY", "MLAG_DFS_PASSWORD", "GNETCLI_DEV_PASSWORD", "COLLECTOR_PASSWORD", "ZTP_ADMIN_PASSWORD",
              "ALERT_SMTP_PASSWORD", "ALERT_WEBHOOK_URL",   # the alert receiver's
              "CONFIG_BACKUP_KEY",                          # config backups' key
              "DIRECTORY_LDAP_BIND_PASSWORD", "DIRECTORY_RADIUS_SECRET")   # directory sign-in
MASK = "******"
EXTRA: set[str] = set()   # secrets kept in the platform database, not the environment (SNMPv3 passwords, OSPF keys)


# VRP's reversible cipher text, whatever the secret (a community the switch has, a key): between two
# identical delimiters %@%#, %^%#, %+%# or %$%$, on one line. Hand-made secrets on a switch have no
# value of ours to replace, so they are masked by their form.
VRP_CIPHER = re.compile(r"(%[@^+$]%[#$]).+?\1")   # the rule; _mask_cipher applies it in one pass


def _mask_cipher(text: str) -> str:
    """VRP_CIPHER.sub(MASK, text) in linear time: each delimiter is closed by the nearest same one
    at least one character later on its line (the regex backtracks quadratically on a hostile line)."""
    out = []
    for line in text.split("\n"):
        at = [i for i in range(len(line) - 3) if line[i] == "%" and line[i + 1] in "@^+$"
              and line[i + 2] == "%" and line[i + 3] in "#$"]
        by: dict[str, list[int]] = {}
        for i in at:
            by.setdefault(line[i:i + 4], []).append(i)
        nxt = dict.fromkeys(by, 0)
        parts, done = [], 0
        for i in at:
            if i < done:
                continue
            d, same = line[i:i + 4], by[line[i:i + 4]]
            while nxt[d] < len(same) and same[nxt[d]] < i + 5:
                nxt[d] += 1
            if nxt[d] < len(same):
                end = same[nxt[d]] + 4
                parts += [line[done:i], MASK]
                done = end
        out.append("".join(parts) + line[done:])
    return "\n".join(out)


def redact(text: str) -> str:
    text = _mask_cipher(text)
    for name in SECRET_ENV:
        if value := os.environ.get(name):
            text = text.replace(value, MASK)
    for value in EXTRA:
        text = text.replace(value, MASK)
    return text
