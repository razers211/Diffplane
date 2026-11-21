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
VRP_CIPHER = re.compile(r"(%[@^+$]%[#$]).+?\1")


def redact(text: str) -> str:
    text = VRP_CIPHER.sub(MASK, text)
    for name in SECRET_ENV:
        if value := os.environ.get(name):
            text = text.replace(value, MASK)
    for value in EXTRA:
        text = text.replace(value, MASK)
    return text
