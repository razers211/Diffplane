"""Where alerts go: Alertmanager's receiver, set in the GUI's settings.

The orchestrator writes Alertmanager's configuration into a volume they share
(`ALERTMANAGER_CONFIG`) at start and whenever an ALERT_* setting changes, then asks Alertmanager
to reload it. Receivers: none (alerts are held and listed with amtool), email (SMTP) or a
webhook. An incomplete receiver is not written: alerts stay held and the page says what is
missing. JSON is written: it is valid YAML, Alertmanager's format.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

GROUP_BY = ["alertname", "site"]


def config(env=os.environ) -> tuple[dict, str | None]:
    """Alertmanager's configuration from the settings, and what is missing (None when complete)."""
    kind = env.get("ALERT_RECEIVER") or "none"
    receivers = [{"name": "none"}]
    problem = None
    if kind == "email":
        to, sender, host = env.get("ALERT_EMAIL_TO"), env.get("ALERT_EMAIL_FROM"), env.get("ALERT_SMTP_HOST")
        missing = [label for label, value in (("recipients", to), ("sender", sender), ("SMTP server", host)) if not value]
        if missing:
            problem = f"email needs {', '.join(missing)}"
        else:
            email = {"to": to, "from": sender, "smarthost": host, "send_resolved": True, "require_tls": True}
            if env.get("ALERT_SMTP_USER"):
                email.update(auth_username=env["ALERT_SMTP_USER"], auth_password=env.get("ALERT_SMTP_PASSWORD", ""))
            receivers.append({"name": "email", "email_configs": [email]})
    elif kind == "webhook":
        if not env.get("ALERT_WEBHOOK_URL"):
            problem = "webhook needs its URL"
        else:
            receivers.append({"name": "webhook", "webhook_configs": [{"url": env["ALERT_WEBHOOK_URL"], "send_resolved": True}]})
    receiver = kind if problem is None and kind in ("email", "webhook") else "none"
    return {"route": {"receiver": receiver, "group_by": GROUP_BY}, "receivers": receivers}, problem


async def apply(env=os.environ) -> str | None:
    """Write the configuration and have Alertmanager reload it; what is wrong, or None."""
    path = env.get("ALERTMANAGER_CONFIG")
    data, problem = config(env)
    if path:
        target = Path(path)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.chmod(0o600)   # it may hold the SMTP password or a webhook token
        tmp.replace(target)
    url = env.get("ALERTMANAGER_URL")
    if url:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(f"{url.rstrip('/')}/-/reload")
            if response.status_code >= 400:
                return f"Alertmanager refused the configuration: {response.text.strip()[:300]}"
        except httpx.HTTPError as exc:
            return f"Alertmanager could not be reached to reload ({type(exc).__name__})"
    return problem


async def send_test(user: str, env=os.environ) -> None:
    """A test alert, delivered like any other (after Alertmanager's grouping wait)."""
    await send("FabricTestAlert", "info", f"Test alert sent from the platform by {user}", env=env)


async def send(name: str, severity: str, summary: str, labels: dict | None = None, minutes: int = 5,
               env=os.environ) -> None:
    """An alert from the platform itself (not a Prometheus rule), through the alert receiver."""
    url = env.get("ALERTMANAGER_URL")
    if not url:
        raise RuntimeError("ALERTMANAGER_URL is not set")
    ends = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    alert = [{"labels": {"alertname": name, "severity": severity, **(labels or {})},
              "annotations": {"summary": summary}, "endsAt": ends}]
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(f"{url.rstrip('/')}/api/v2/alerts", json=alert)
        response.raise_for_status()
