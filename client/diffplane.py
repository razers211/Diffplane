"""diffplane: a command-line client for the Diffplane orchestrator API (Python standard library only).

    export DIFFPLANE_URL=https://diffplane.example:8000 DIFFPLANE_CA=fabric.crt DIFFPLANE_USER=ci
    diffplane drift --fabric dc-a --check      # exit 1 when a switch is out of sync
    diffplane diff --fabric dc-a               # check the switches against intent now
    diffplane deploy prepare --fabric dc-a     # patches for approval; nothing is sent to a switch
    diffplane deploy approve 42                # an approver's account (or an operator allowed to)
    diffplane intent export --fabric dc-a > dc-a.yaml   # the fabric's intent, to keep in git
    diffplane intent import --fabric dc-a dc-a.yaml     # what it would change; --apply to change it

The password comes from DIFFPLANE_PASSWORD or a prompt, never from an argument. The orchestrator
decides everything (roles, fabric limits, deploy rules): the client only asks. HTTPS is verified
against DIFFPLANE_CA (the orchestrator's own certificate) or the system's trust store.
"""

import argparse
import getpass
import http.cookiejar
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

POLL_SECONDS = 2.0
PREPARED = {"diffed", "unchanged", "failed", "cancelled"}       # a deploy job ready to read
FINISHED = {"verified", "failed", "cancelled", "stopped", "unchanged"}   # a deploy job that is over


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status = status


class Client:
    """The orchestrator's API with one signed-in session (its cookie kept in memory only)."""

    def __init__(self, url: str, ca: str | None = None, timeout: float = 60,
                 opener: Callable[..., Any] | None = None):
        self.url = url.rstrip("/")
        if not self.url.startswith("https://"):
            raise ValueError("the orchestrator serves HTTPS only: the URL starts with https://")
        self.timeout = timeout
        self.open = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())).open

    def call(self, method: str, path: str, query: dict | None = None, body: Any = None, raw: bool = False) -> Any:
        query = {k: v for k, v in (query or {}).items() if v is not None}
        target = self.url + urllib.parse.quote(path) + (f"?{urllib.parse.urlencode(query)}" if query else "")
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(target, data=data, method=method, headers={
            "Accept": "application/json", **({"Content-Type": "application/json"} if data else {})})
        try:
            with self.open(request, timeout=self.timeout) as answer:
                text = answer.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", exc.reason)
            except (ValueError, AttributeError):
                detail = exc.reason
            raise ApiError(exc.code, detail if isinstance(detail, str) else json.dumps(detail)) from None
        return text.decode() if raw else json.loads(text) if text else None

    def login(self, username: str, password: str) -> dict:
        return self.call("POST", "/auth/login", body={"username": username, "password": password})

    def wait(self, path: str, done: Callable[[dict], bool], say: Callable[[str], None] = lambda _: None,
             sleep: Callable[[float], None] = time.sleep) -> dict:
        """GET path until done(answer); say() each new status."""
        last = None
        while True:
            job = self.call("GET", path)
            if job["status"] != last:
                say(f"job {job['id']}: {job['status']}")
                last = job["status"]
            if done(job):
                return job
            sleep(POLL_SECONDS)


# -- commands ---------------------------------------------------------------------------------

def _table(rows: list[dict], columns: list[str]) -> str:
    cells = [[("" if r.get(c) is None else str(r.get(c))) for c in columns] for r in rows]
    widths = [max([len(c)] + [len(row[i]) for row in cells]) for i, c in enumerate(columns)]
    lines = [[c.upper() for c in columns]] + cells
    return "\n".join("  ".join(v.ljust(w) for v, w in zip(line, widths)).rstrip() for line in lines)


def _scope(args) -> dict:
    if bool(args.fabric) == bool(args.switch):
        raise SystemExit("give --fabric or --switch (one or more), not both")
    return {"site": args.fabric} if args.fabric else {"devices": args.switch}


def cmd_fabrics(api: Client, args, out) -> int:
    fabrics = api.call("GET", "/fabrics")
    out(fabrics, lambda: _table(fabrics, ["site", "name", "architecture"]))
    return 0


def cmd_switches(api: Client, args, out) -> int:
    switches = api.call("GET", "/devices", {"site": args.fabric})
    out(switches, lambda: _table(switches, ["name", "site", "role", "status", "primary_ip", "peer"]))
    return 0


def cmd_drift(api: Client, args, out) -> int:
    drift = api.call("GET", "/drift")
    rows = [d for d in drift["devices"] if not args.fabric or d["site"] == args.fabric]
    out({**drift, "devices": rows}, lambda: _table(rows, ["device", "site", "state", "checked_at"]))
    return 1 if args.check and any(d["state"] != "in_sync" for d in rows) else 0


def cmd_diff(api: Client, args, out) -> int:
    """Exit 0: every switch as intended; 1: a switch differs; 2: a switch could not be checked."""
    started = api.call("POST", "/diff-jobs", body=_scope(args))
    job = api.wait(f"/diff-jobs/{started['id']}", lambda j: j["status"] != "running", args.say)

    def text() -> str:
        parts = []
        for s in job.get("snapshots") or []:
            body = s["error"] if s["status"] == "error" else (s["diff"] or "(as intended)")
            parts.append(f"--- {s['device']}: {s['status']}\n{body}")
        return "\n".join(parts + [f"job {job['id']} {job['status']}: {job['devices']} switches, "
                                  f"{job['changed']} differ, {job['errors']} errors" + (f" ({job['error']})" if job["error"] else "")])
    out(job, text)
    return 2 if job["status"] == "failed" or job["errors"] else 1 if job["changed"] else 0


def _deploy_text(job: dict) -> str:
    lines = [f"deploy job {job['id']} on {job['site'] or 'listed switches'}: {job['status']}"
             + (f" ({job['error']})" if job.get("error") else "")]
    if job.get("window_start"):
        lines.append(f"window: {job['window_start']} to {job['window_end']}")
    for step in job["steps"]:
        lines.append(f"--- {step['device']}: {step['status']}" + (f" ({step['error']})" if step["error"] else ""))
        if step["patch"]:
            lines.append(step["patch"].rstrip("\n"))
    for finding in ((job.get("checks") or {}).get("fabrics") or []):
        for f in finding.get("findings") or []:
            lines.append(f"check {f['severity']}: {f['text']}")
    return "\n".join(lines)


def cmd_deploy_prepare(api: Client, args, out) -> int:
    started = api.call("POST", "/deploy-jobs", body=_scope(args))
    job = api.wait(f"/deploy-jobs/{started['id']}", lambda j: j["status"] in PREPARED, args.say)
    out(job, lambda: _deploy_text(job) + ("\nnothing is sent until it is approved: diffplane deploy approve "
                                          f"{job['id']}" if job["status"] == "diffed" else ""))
    return 1 if job["status"] == "failed" else 0


def cmd_deploy_approve(api: Client, args, out) -> int:
    if bool(args.window_start) != bool(args.window_end):
        raise SystemExit("a window needs --window-start and --window-end")
    window = {"window_start": args.window_start, "window_end": args.window_end} if args.window_start else {}
    api.call("POST", f"/deploy-jobs/{args.id}/approve", body=window)
    if window or not args.wait:
        job = api.call("GET", f"/deploy-jobs/{args.id}")
    else:
        job = api.wait(f"/deploy-jobs/{args.id}", lambda j: j["status"] in FINISHED, args.say)
    out(job, lambda: _deploy_text(job))
    return 1 if job["status"] == "failed" else 0


def cmd_deploy_show(api: Client, args, out) -> int:
    job = api.call("GET", f"/deploy-jobs/{args.id}")
    out(job, lambda: _deploy_text(job))
    return 0


def cmd_deploy_list(api: Client, args, out) -> int:
    jobs = api.call("GET", "/deploy-jobs", {"limit": args.limit})
    out(jobs, lambda: _table(jobs, ["id", "site", "status", "requested_by", "approved_by", "created_at"]))
    return 0


def cmd_deploy_cancel(api: Client, args, out) -> int:
    api.call("POST", f"/deploy-jobs/{args.id}/cancel")
    return cmd_deploy_show(api, args, out)


def cmd_intent_export(api: Client, args, out) -> int:
    text = api.call("GET", f"/fabrics/{args.fabric}/intent", raw=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0


def _plan_text(plan: dict) -> str:
    lines = [f"{len(plan['operations'])} change(s) to {plan['fabric']}'s intent" + (":" if plan["operations"] else "")]
    lines += [f"  {i}. {o['text']}" for i, o in enumerate(plan["operations"], 1)]
    lines += [f"note: {n}" for n in plan["notes"]]
    lines += [f"left as it is (set from the GUI): {', '.join(plan['ignored'])}"] if plan["ignored"] else []
    return "\n".join(lines)


def cmd_intent_import(api: Client, args, out) -> int:
    """Preview: exit 0 if the fabric's intent is already the file's, 1 if it is not. With --apply:
    exit 0 once every operation is done, 2 if the orchestrator refused one."""
    with open(args.file, encoding="utf-8") as f:
        text = f.read()
    plan = api.call("POST", f"/fabrics/{args.fabric}/intent/preview", body={"yaml": text})
    if not args.apply or not plan["operations"]:
        out(plan, lambda: _plan_text(plan) + ("\nnothing written; --apply makes these changes" if plan["operations"] else ""))
        return 1 if plan["operations"] else 0
    done = api.call("POST", f"/fabrics/{args.fabric}/intent/import",
                    body={"yaml": text, "expected": len(plan["operations"])})

    def summary() -> str:
        lines = [f"done: {t}" for t in done["done"]]
        if done["failed"]:
            lines += [f"refused: {done['failed']['text']}: {done['failed']['error']}"]
            lines += [f"not done: {t}" for t in done["remaining"]]
        return "\n".join(lines + ["intent changed: review and deploy it (diffplane deploy prepare)" if done["done"] else ""])
    out(done, summary)
    return 2 if done["failed"] else 0


def parser() -> argparse.ArgumentParser:
    env = os.environ.get
    p = argparse.ArgumentParser(prog="diffplane", description=__doc__.split("\n\n")[0])
    p.add_argument("--url", default=env("DIFFPLANE_URL"), help="the orchestrator, https://host:port (DIFFPLANE_URL)")
    p.add_argument("--ca", default=env("DIFFPLANE_CA"),
                   help="its certificate, fabric.crt (DIFFPLANE_CA); else the system's trust store")
    p.add_argument("--user", default=env("DIFFPLANE_USER"), help="account (DIFFPLANE_USER); password: DIFFPLANE_PASSWORD or a prompt")
    p.add_argument("--json", action="store_true", help="print the API's answers as JSON")
    p.add_argument("--quiet", "-q", action="store_true", help="no progress lines on stderr")
    sub = p.add_subparsers(dest="command", required=True)

    def scoped(q):
        q.add_argument("--fabric", help="every active switch of this fabric")
        q.add_argument("--switch", action="append", help="this switch (repeat for more)")

    sub.add_parser("fabrics", help="list fabrics").set_defaults(run=cmd_fabrics)
    q = sub.add_parser("switches", help="list switches")
    q.add_argument("--fabric")
    q.set_defaults(run=cmd_switches)
    q = sub.add_parser("drift", help="each switch's latest check against intent")
    q.add_argument("--fabric")
    q.add_argument("--check", action="store_true", help="exit 1 unless every switch is in sync")
    q.set_defaults(run=cmd_drift)
    q = sub.add_parser("diff", help="check switches against intent now (exit 1: one differs, 2: an error)")
    scoped(q)
    q.set_defaults(run=cmd_diff)

    deploy = sub.add_parser("deploy", help="deploy jobs").add_subparsers(dest="action", required=True)
    q = deploy.add_parser("prepare", help="compute the patches for approval (nothing is sent)")
    scoped(q)
    q.set_defaults(run=cmd_deploy_prepare)
    q = deploy.add_parser("approve", help="approve a prepared job: now, or in a change window")
    q.add_argument("id", type=int)
    q.add_argument("--window-start", help="ISO 8601 with a zone, e.g. 2026-12-01T02:00:00+01:00")
    q.add_argument("--window-end")
    q.add_argument("--no-wait", dest="wait", action="store_false", help="return once approved")
    q.set_defaults(run=cmd_deploy_approve)
    for name, run, text in (("show", cmd_deploy_show, "a job, its patches and log"),
                            ("cancel", cmd_deploy_cancel, "cancel a scheduled job, or stop one after its wave")):
        q = deploy.add_parser(name, help=text)
        q.add_argument("id", type=int)
        q.set_defaults(run=run)
    q = deploy.add_parser("list", help="recent deploy jobs")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(run=cmd_deploy_list)

    intent = sub.add_parser("intent", help="a fabric's intent as a YAML file").add_subparsers(dest="action", required=True)
    q = intent.add_parser("export", help="write the fabric's intent as YAML")
    q.add_argument("--fabric", required=True)
    q.add_argument("--output", "-o", help="this file (default: stdout)")
    q.set_defaults(run=cmd_intent_export)
    q = intent.add_parser("import", help="what the file would change in intent; --apply to change it")
    q.add_argument("--fabric", required=True)
    q.add_argument("file")
    q.add_argument("--apply", action="store_true", help="make the changes (switches change only after a deploy)")
    q.set_defaults(run=cmd_intent_import)
    return p


def main(argv: list[str] | None = None, client: Client | None = None) -> int:
    args = parser().parse_args(argv)
    args.say = (lambda _: None) if args.quiet or args.json else (lambda line: print(line, file=sys.stderr))

    def out(data: Any, text: Callable[[], str]) -> None:
        print(json.dumps(data, indent=2) if args.json else text())

    try:
        if client is None:
            if not args.url or not args.user:
                raise SystemExit("set --url and --user (or DIFFPLANE_URL and DIFFPLANE_USER)")
            client = Client(args.url, args.ca)
            password = os.environ.get("DIFFPLANE_PASSWORD") or getpass.getpass(f"password for {args.user}: ")
            client.login(args.user, password)
        return args.run(client, args, out)
    except (ApiError, ValueError, OSError) as exc:
        print(f"diffplane: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
