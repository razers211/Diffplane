"""The diffplane CLI against a fake orchestrator: its commands, exit codes, and that every call it
makes is a route the API's schema has (the contract of orchestrator/answers.py)."""

import io
import json
import re
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "client"))
import diffplane  # noqa: E402

CALLS: set[tuple[str, str]] = set()


class Fake:
    """Answers (method, path) from a dict; records every call for the schema check."""

    def __init__(self, routes: dict):
        self.routes, self.seen = routes, []

    def __call__(self, request, timeout):
        path = request.full_url.split("://", 1)[1].split("/", 1)[1].split("?")[0]
        key = (request.get_method(), "/" + path)
        body = json.loads(request.data) if request.data else None
        self.seen.append((*key, body))
        CALLS.add(key)
        answer = self.routes[key]
        answer = answer.pop(0) if isinstance(answer, list) else answer
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "refused", {},
                                         io.BytesIO(b'{"detail": "approving this deploy needs the approver role"}'))
        return io.BytesIO(answer.encode() if isinstance(answer, str) else json.dumps(answer).encode())


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(diffplane, "POLL_SECONDS", 0)


def run(routes: dict, *argv: str, capsys) -> tuple[int, str, Fake]:
    fake = Fake(routes)
    code = diffplane.main(["-q", *argv], client=diffplane.Client("https://diffplane.test:8000", opener=fake))
    return code, capsys.readouterr().out, fake


def snap(device, diff="", status="ok"):
    return {"device": device, "status": status, "has_changes": bool(diff), "diff": diff, "error": None,
            "created_at": "t", "finished_at": "t", "job_id": 7, "job_kind": "diff"}


def diff_job(status, snaps):
    return {"id": 7, "kind": "diff", "requested_by": "ci", "status": status, "site": "dc-a", "created_at": "t",
            "finished_at": None, "error": None, "devices": len(snaps), "changed": sum(bool(s["diff"]) for s in snaps),
            "errors": sum(s["status"] == "error" for s in snaps), "snapshots": snaps}


def test_diff_exits_1_when_a_switch_differs(capsys):
    routes = {("POST", "/diff-jobs"): {"id": 7},
              ("GET", "/diff-jobs/7"): [diff_job("running", []), diff_job("done", [snap("leaf1"), snap("leaf2", "+ ntp x")])]}
    code, out, fake = run(routes, "diff", "--fabric", "dc-a", capsys=capsys)
    assert code == 1
    assert fake.seen[0] == ("POST", "/diff-jobs", {"site": "dc-a"})
    assert "--- leaf1: ok\n(as intended)" in out and "--- leaf2: ok\n+ ntp x" in out


def test_diff_exits_0_in_sync_and_2_on_an_error(capsys):
    ok = {("POST", "/diff-jobs"): {"id": 7}, ("GET", "/diff-jobs/7"): diff_job("done", [snap("leaf1")])}
    assert run(ok, "diff", "--switch", "leaf1", capsys=capsys)[0] == 0
    bad = {("POST", "/diff-jobs"): {"id": 7},
           ("GET", "/diff-jobs/7"): diff_job("done", [snap("leaf1", status="error")])}
    assert run(bad, "diff", "--switch", "leaf1", capsys=capsys)[0] == 2


def test_a_scope_is_a_fabric_or_switches(capsys):
    with pytest.raises(SystemExit):
        run({}, "diff", capsys=capsys)


def deploy_job(status, **more):
    return {"id": 42, "kind": "deploy", "status": status, "site": "dc-a", "requested_by": "ci", "approved_by": None,
            "approved_at": None, "generators": None, "created_at": "t", "finished_at": None, "error": None,
            "window_start": None, "window_end": None, "checks": None, "params": None,
            "steps": [{"device": "leaf1", "status": "diffed", "patch": "ntp unicast-server 192.0.2.1\n",
                       "error": None, "started_at": None, "finished_at": None}], **more}


def test_prepare_waits_for_the_patches_and_sends_nothing_else(capsys):
    routes = {("POST", "/deploy-jobs"): {"id": 42},
              ("GET", "/deploy-jobs/42"): [deploy_job("queued"), deploy_job("preparing"), deploy_job("diffed")]}
    code, out, fake = run(routes, "deploy", "prepare", "--fabric", "dc-a", capsys=capsys)
    assert code == 0
    assert [c[:2] for c in fake.seen if c[0] != "GET"] == [("POST", "/deploy-jobs")]
    assert "ntp unicast-server 192.0.2.1" in out and "diffplane deploy approve 42" in out


def test_approve_in_a_window_and_a_refusal_is_the_servers(capsys):
    routes = {("POST", "/deploy-jobs/42/approve"): {"id": 42},
              ("GET", "/deploy-jobs/42"): deploy_job("scheduled", window_start="s", window_end="e")}
    code, out, fake = run(routes, "deploy", "approve", "42", "--window-start", "s", "--window-end", "e", capsys=capsys)
    assert code == 0 and fake.seen[0][2] == {"window_start": "s", "window_end": "e"} and "window: s to e" in out
    fake = Fake({("POST", "/deploy-jobs/42/approve"): 403})
    code = diffplane.main(["deploy", "approve", "42"], client=diffplane.Client("https://d.test", opener=fake))
    assert code == 2 and "403: approving this deploy needs the approver role" in capsys.readouterr().err


def test_drift_check_and_json(capsys):
    drift = {"scheduler": None, "devices": [
        {"device": "leaf1", "site": "dc-a", "state": "in_sync", "checked_at": "t", "job_id": 1, "job_kind": "drift",
         "diff": "", "error": None},
        {"device": "leaf9", "site": "dc-b", "state": "out_of_sync", "checked_at": "t", "job_id": 1,
         "job_kind": "drift", "diff": "+x", "error": None}]}
    assert run({("GET", "/drift"): drift}, "drift", "--fabric", "dc-a", "--check", capsys=capsys)[0] == 0
    code, out, _ = run({("GET", "/drift"): drift}, "--json", "drift", "--check", capsys=capsys)
    assert code == 1 and [d["device"] for d in json.loads(out)["devices"]] == ["leaf1", "leaf9"]


def test_lists(capsys):
    _, out, _ = run({("GET", "/fabrics"): [[{"site": "dc-a", "name": "DC A", "architecture": "symmetric"}]]},
                    "fabrics", capsys=capsys)
    assert out.splitlines() == ["SITE  NAME  ARCHITECTURE", "dc-a  DC A  symmetric"]
    run({("GET", "/devices"): [[]]}, "switches", capsys=capsys)
    run({("GET", "/deploy-jobs"): [[deploy_job("verified")]]}, "deploy", "list", capsys=capsys)
    run({("POST", "/deploy-jobs/42/cancel"): {"id": 42}, ("GET", "/deploy-jobs/42"): deploy_job("cancelled")},
        "deploy", "cancel", "42", capsys=capsys)


def test_login_sends_the_account():
    fake = Fake({("POST", "/auth/login"): {"username": "ci", "role": "operator", "fabrics": None}})
    diffplane.Client("https://d.test", opener=fake).login("ci", "x")
    assert fake.seen == [("POST", "/auth/login", {"username": "ci", "password": "x"})]


def test_https_only():
    with pytest.raises(ValueError):
        diffplane.Client("http://diffplane.test:8000")


def test_intent_export_and_import(capsys, tmp_path):
    yaml_text = "kind: diffplane-intent\nversion: 1\nfabric: dc-a\n"
    routes = {("GET", "/fabrics/dc-a/intent"): yaml_text}
    fake = Fake(routes)
    out_file = tmp_path / "dc-a.yaml"
    code = diffplane.main(["-q", "intent", "export", "--fabric", "dc-a", "-o", str(out_file)],
                          client=diffplane.Client("https://d.test", opener=fake))
    assert code == 0 and out_file.read_text() == yaml_text

    plan = {"fabric": "dc-a", "operations": [{"kind": "create_segment", "text": "create segment db (VNI 10600)"}],
            "notes": [], "ignored": ["QoS"]}
    code, out, fake = run({("POST", "/fabrics/dc-a/intent/preview"): plan},
                          "intent", "import", "--fabric", "dc-a", str(out_file), capsys=capsys)
    assert code == 1 and fake.seen == [("POST", "/fabrics/dc-a/intent/preview", {"yaml": yaml_text})]
    assert "1. create segment db (VNI 10600)" in out and "left as it is (set from the GUI): QoS" in out

    done = {**plan, "done": ["create segment db (VNI 10600)"], "failed": None, "remaining": []}
    code, out, fake = run({("POST", "/fabrics/dc-a/intent/preview"): plan, ("POST", "/fabrics/dc-a/intent/import"): done},
                          "intent", "import", "--fabric", "dc-a", str(out_file), "--apply", capsys=capsys)
    assert code == 0 and fake.seen[1][2] == {"yaml": yaml_text, "expected": 1}
    refused = {**done, "done": [], "failed": {"text": "create segment db", "error": "VNI taken"}, "remaining": []}
    code, out, _ = run({("POST", "/fabrics/dc-a/intent/preview"): plan, ("POST", "/fabrics/dc-a/intent/import"): refused},
                       "intent", "import", "--fabric", "dc-a", str(out_file), "--apply", capsys=capsys)
    assert code == 2 and "refused: create segment db: VNI taken" in out


def test_every_call_is_a_route_of_the_schema():
    """Last in this file: CALLS holds what the tests above sent."""
    schema = json.loads((Path(__file__).parents[2] / "gui" / "openapi.json").read_text())
    routes = [(m.upper(), re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$"))
              for p, ops in schema["paths"].items() for m in ops]
    assert len(CALLS) >= 14   # every command and login
    for method, path in sorted(CALLS):
        assert any(m == method and r.match(path) for m, r in routes), f"{method} {path} is not in the API"
