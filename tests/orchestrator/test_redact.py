"""Masking secrets in what the API returns."""

from orchestrator.redact import MASK, redact


def test_vrp_cipher_text_is_masked_in_every_form():
    lines = [
        '- snmp-agent community read cipher %@%##!!!!"!!!!#AbCd:*Ef!!%@%# alias ro-0a1b2c3d',
        "  key-string cipher %^%#a1b2%^%#",
        "snmp-agent usm-user v3 nms authentication-mode sha2-256 cipher %+%#x y%+%#",
        "  authentication-mode hmac-sha256 password %$%$q%w$e%$%$",
    ]
    masked = redact("\n".join(lines)).splitlines()
    assert masked == [
        f"- snmp-agent community read cipher {MASK} alias ro-0a1b2c3d",
        f"  key-string cipher {MASK}",
        f"snmp-agent usm-user v3 nms authentication-mode sha2-256 cipher {MASK}",
        f"  authentication-mode hmac-sha256 password {MASK}",
    ]


def test_text_without_cipher_is_unchanged(monkeypatch):
    monkeypatch.setenv("SNMP_COMMUNITY", "s3cret")
    assert redact("ping 50%%#done and 100%") == "ping 50%%#done and 100%"
    assert redact("community s3cret") == f"community {MASK}"
