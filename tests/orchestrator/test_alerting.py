"""Alert receiver from the GUI's settings: Alertmanager's configuration, never a broken one."""

import asyncio
import json

import pytest

from orchestrator import alerting, platform_settings, redact

EMAIL = {"ALERT_RECEIVER": "email", "ALERT_EMAIL_TO": "noc@example.net", "ALERT_EMAIL_FROM": "fabric@example.net",
         "ALERT_SMTP_HOST": "smtp.example.net:587"}


def test_no_receiver_by_default():
    config, problem = alerting.config({})
    assert config == {"route": {"receiver": "none", "group_by": ["alertname", "site"]}, "receivers": [{"name": "none"}]}
    assert problem is None


def test_email_with_and_without_login():
    config, problem = alerting.config(EMAIL)
    assert problem is None and config["route"]["receiver"] == "email"
    email = config["receivers"][1]["email_configs"][0]
    assert email["smarthost"] == "smtp.example.net:587" and "auth_password" not in email
    config, _ = alerting.config({**EMAIL, "ALERT_SMTP_USER": "fabric", "ALERT_SMTP_PASSWORD": "pw"})
    assert config["receivers"][1]["email_configs"][0]["auth_password"] == "pw"


def test_an_incomplete_receiver_holds_alerts_and_says_why():
    config, problem = alerting.config({**EMAIL, "ALERT_SMTP_HOST": ""})
    assert config["route"]["receiver"] == "none" and problem == "email needs SMTP server"
    config, problem = alerting.config({"ALERT_RECEIVER": "webhook"})
    assert config["route"]["receiver"] == "none" and problem == "webhook needs its URL"
    config, _ = alerting.config({"ALERT_RECEIVER": "webhook", "ALERT_WEBHOOK_URL": "https://hooks.example.net/t0ken"})
    assert config["receivers"][1] == {"name": "webhook", "webhook_configs": [{"url": "https://hooks.example.net/t0ken", "send_resolved": True}]}


def test_the_file_is_written_for_alertmanager_only(tmp_path):
    target = tmp_path / "alertmanager.yml"
    assert asyncio.run(alerting.apply({**EMAIL, "ALERTMANAGER_CONFIG": str(target)})) is None
    assert json.loads(target.read_text())["route"]["receiver"] == "email"
    assert target.stat().st_mode & 0o077 == 0


def test_settings_check_alert_values():
    by = platform_settings.BY_KEY
    for key, value in [("ALERT_RECEIVER", "pager"), ("ALERT_EMAIL_TO", "noc"), ("ALERT_SMTP_HOST", "smtp.example.net"),
                       ("ALERT_WEBHOOK_URL", "ftp://x")]:
        with pytest.raises(ValueError):
            platform_settings.check(by[key], value)
    assert platform_settings.check(by["ALERT_EMAIL_TO"], "a@example.net ,b@example.net") == "a@example.net, b@example.net"


def test_every_secret_setting_is_redacted():
    assert {s.key for s in platform_settings.CATALOGUE if s.secret} <= set(redact.SECRET_ENV)
