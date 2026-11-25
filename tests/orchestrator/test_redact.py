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


def test_cipher_masking_matches_the_rule_in_linear_time():
    import random
    import time

    from orchestrator.redact import MASK, VRP_CIPHER, _mask_cipher

    rng = random.Random(7)
    for _ in range(3000):
        text = "".join(rng.choice(["%@%#", "%^%#", "%+%#", "%$%$", "%", "@", "#", "a", " ", "\n"])
                       for _ in range(rng.randint(0, 40)))
        assert _mask_cipher(text) == VRP_CIPHER.sub(MASK, text), repr(text)
    hostile = "".join(f"%{a}%{b}" for a in "@^+$" for b in "#$") * 2000   # no delimiter closes twice... and many open
    start = time.perf_counter()
    _mask_cipher(hostile + "x" * 50000)
    assert time.perf_counter() - start < 1
