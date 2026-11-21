"""Run Annet for the orchestrator, in a pool of worker processes (orchestrator/annet_worker.py).

A worker loads Annet, the generators and the rulebook once and runs one command at a time through
Annet's own command functions, handing results back as data: no text is parsed, and no start-up
cost is paid per run. Keeping Annet out of the API process keeps its process-wide state
(connectors, generator imports) out of it and lets a stuck run be killed: a worker that times out
is killed with its gnetcli server and replaced. A worker started before generators/ or rulebook/
changed is replaced before its next run, so a changed generator applies without a restart.
Workers run in the repository root (the container's /app, or ANNET_WORKDIR) so the `generators`
package named in the Annet context is importable.
"""

import asyncio
import json
import os
import re
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from orchestrator.timing import measure

# gnetcli writes one transcript per deploy here (every command and the device's reply);
# deploy/annet/context.yml sets the same path as the deployer's logs_dir.
DEPLOY_LOGS = Path("/tmp/annet-deploy-logs")
TRANSCRIPT_TAIL = 30

DEVICE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Commands that reach a switch through gnetcli. Each run starts its own gnetcli_server, by
# default on the fixed 127.0.0.1:50051, so two at once failed ("bind: address already in use").
# deploy/annet/context.yml gives each server a free port of its own instead (port 0),
# so runs may overlap; PARALLEL bounds how many at once.
CONNECTING = ("diff", "patch", "deploy", "show")
DIAL_RETRIES = 2          # a run whose only errors are a failed connection to the switch is run again
DIAL_RETRY_SECONDS = 15.0
PARALLEL = 8   # runs connecting to switches at once (each worker about 90 MB at its peak)
WORKERS = PARALLEL   # worker processes at most, connecting or not
IDLE_SECONDS = 600   # an idle worker is stopped after this
REPLY_LIMIT = 256 * 1024 * 1024   # one reply line (a running config is a few hundred KB)
CODE_DIRS = ("generators", "rulebook")   # a change here replaces the workers started before it


class AnnetError(Exception):
    pass


class AnnetRunner:
    def __init__(
        self, command: list[str] | None = None, cwd: Path | None = None, timeout: float = 120.0,
        deploy_timeout: float = 600.0, logs_dir: Path | None = None,
        before_connect: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.command = command or [sys.executable, "-m", "orchestrator.annet_worker"]
        self.cwd = cwd or Path(os.environ.get("ANNET_WORKDIR", ".")).resolve()
        self.timeout = timeout
        self.deploy_timeout = deploy_timeout
        self.logs_dir = logs_dir or DEPLOY_LOGS
        # Runs before every command that connects to the device (diff, patch, deploy): the
        # orchestrator checks its SSH host key is pinned (orchestrator/hostkeys.py).
        self.before_connect = before_connect
        self._gnetcli = asyncio.Semaphore(PARALLEL)   # switch-connecting runs at once (CONNECTING)
        self._workers = asyncio.Semaphore(WORKERS)    # taken after _gnetcli, never before it
        self._idle: list[_Worker] = []

    async def _connecting(self, device: str) -> None:
        self._check(device)
        if self.before_connect is not None:
            await self.before_connect(device)

    async def gen(self, device: str) -> str:
        """Return the generated config for one device, as `annet gen` prints it."""
        self._check(device)
        output = await self._labelled("gen", device, ".cfg")
        return output.rstrip("\n") + "\n" if output.strip() else ""

    async def diff(self, device: str) -> str:
        """`annet diff` for one device: fetches the running config, compares with generated.
        Empty when the device matches NetBox."""
        await self._connecting(device)
        output = await self._labelled("diff", device, ".cfg", "--no-progress")
        return output.strip("\n") + "\n" if output.strip() else ""

    async def current(self, device: str) -> str:
        """`annet show current`: the device's running config, fetched over SSH (pinned host key).
        It holds secrets: callers parse it in memory and never store or return it (import)."""
        await self._connecting(device)
        reply = await self._run("show", "current", device)
        _raise_on_annet_errors("show current", device, reply)
        for label, _body, failed in reply["items"]:
            if failed or label != f"{device}.cfg":
                raise AnnetError(f"annet show current failed for {label}")   # body may hold secrets: not shown
        return "".join(body for _, body, _ in reply["items"])

    async def patch(self, device: str, generators: list[str] | None = None) -> str:
        """`annet patch`: the commands a deploy would send. Empty when nothing to do."""
        await self._connecting(device)
        output = await self._labelled("patch", device, ".patch", "--no-progress", *_gens(generators))
        return output.strip("\n") + "\n" if output.strip() else ""

    async def deploy(self, device: str, generators: list[str] | None = None, excluded: str | None = None) -> None:
        """`annet deploy` without its interactive confirmation: the approval happened in the GUI.
        Annet checks the diff is empty afterwards; anything left is a failure."""
        await self._connecting(device)
        started = time.time()
        try:
            reply = await self._run(
                "deploy", "--no-ask-deploy", "--no-progress", "--no-label", "--no-color", *_gens(generators),
                *(["-G", excluded] if excluded else []), device, timeout=self.deploy_timeout,
            )
            _raise_on_annet_errors("deploy", device, reply)
        except AnnetError as exc:
            # The device's error alone does not say which command it rejected.
            if transcript := deploy_transcript(self.logs_dir, device, started):
                raise AnnetError(f"{exc}\n\nlast lines of the deploy transcript:\n{transcript}") from exc
            raise

    async def commands(self, device: str, ip: str, steps: list) -> list[str]:
        """Send switch commands outside a config patch (orchestrator/switch_cli.py) through a gnetcli
        server of their own, so under the same bound and host-key check as Annet's runs."""
        from orchestrator import switch_cli

        await self._connecting(device)
        async with self._gnetcli:
            with measure("switch-cli"):
                return await switch_cli.run(device, ip, steps)

    async def trial(self, device: str, ip: str, patch: str, check, hide: tuple[str, ...]) -> None:
        """The patch under `commit trial`: reverts by itself unless `check` finds both logins work.
        The whole session holds a connection slot, so its `check` connects with switch_cli directly."""
        from orchestrator import switch_cli

        await self._connecting(device)
        async with self._gnetcli:
            with measure("switch-cli"):
                await switch_cli.run(device, ip, switch_cli.trial_steps(patch, check, hide), abort=switch_cli.ABORT_TRIAL)

    def _check(self, device: str) -> None:
        if not DEVICE_NAME.fullmatch(device):
            raise AnnetError(f"invalid device name: {device!r}")

    async def _labelled(self, command: str, device: str, suffix: str, *options: str) -> str:
        """Run a command that gives labelled results and return the device's.

        Annet succeeds even when a device fails: the failure is a result labelled with the bare
        device name, and "No devices found" only goes to the log. Both must be errors here;
        read as empty output they would mean "no changes".
        """
        reply = await self._run(command, "--no-color", *options, device)
        _raise_on_annet_errors(command, device, reply)
        for label, body, failed in reply["items"]:
            if failed or label != f"{device}{suffix}":
                raise AnnetError(f"annet {command} failed for {label}:\n{body.strip()[-2000:]}")
        return "".join(body for _, body, _ in reply["items"])

    async def _run(self, *args: str, timeout: float | None = None) -> dict:
        if args[0] not in CONNECTING:
            return await self._call(list(args), timeout)
        for attempt in range(DIAL_RETRIES + 1):
            async with self._gnetcli:
                reply = await self._call(list(args), timeout)
            if attempt == DIAL_RETRIES or not _dial_failed(reply):
                return reply
            # A CE6866 refused new SSH connections for some seconds right after the commit that completed
            # an M-LAG trunk on both switches; its open NETCONF sessions kept working. Retrying is safe:
            # a read reads again, and a deploy recomputes its patch (empty if the first one committed;
            # intent is frozen during a rollout).
            await asyncio.sleep(DIAL_RETRY_SECONDS)
        return reply

    async def _call(self, argv: list[str], timeout: float | None) -> dict:
        """One command on a free worker: {"code", "items": [[label, text, failed]], "errors"}."""
        async with self._workers:
            worker = await self._take()
            # Exact host filter: "leaf1" must not also match "leaf10". The environment goes with
            # each run, so a setting changed in the platform applies to the next one.
            request = {"argv": argv, "env": {**os.environ, "NETBOX_EXACT_HOST_FILTER": "true"}}
            try:
                with measure(f"annet-{argv[0]}"):
                    worker.process.stdin.write(json.dumps(request).encode() + b"\n")
                    await worker.process.stdin.drain()
                    line = await asyncio.wait_for(worker.process.stdout.readline(), timeout or self.timeout)
                if not line:
                    raise AnnetError(f"annet {argv[0]} failed: the worker stopped "
                                     f"(exit code {await worker.process.wait()})")
                reply = json.loads(line)
            except asyncio.TimeoutError:
                # Kill it with its gnetcli server: a stuck run blocked later ones.
                await worker.kill()
                raise AnnetError(f"annet {argv[0]} timed out after {timeout or self.timeout:.0f}s") from None
            except BaseException:
                await worker.kill()
                raise
            worker.used = time.monotonic()
            self._idle.append(worker)
        if reply["code"] != 0:
            raise AnnetError(f"annet {argv[0]} failed: " + "\n".join(reply["errors"])[-2000:])
        return reply

    async def _take(self) -> "_Worker":
        """An idle worker on the current code, else a new one; stale and long-idle ones are stopped."""
        code, now = _code_version(self.cwd), time.monotonic()
        keep = []
        for worker in self._idle:
            if worker.code == code and now - worker.used < IDLE_SECONDS and worker.process.returncode is None:
                keep.append(worker)
            else:
                await worker.kill()
        self._idle = keep
        if self._idle:
            return self._idle.pop()
        process = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,   # Annet's own log can quote configs: errors come in the reply
            start_new_session=True, limit=REPLY_LIMIT,
        )
        return _Worker(process, code)

    async def close(self) -> None:
        for worker in self._idle:
            await worker.kill()
        self._idle = []


class _Worker:
    def __init__(self, process: asyncio.subprocess.Process, code: tuple):
        self.process, self.code, self.used = process, code, time.monotonic()

    async def kill(self) -> None:
        """The worker and everything it started (its gnetcli server), in its own process group."""
        if self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        await self.process.wait()


def _code_version(root: Path) -> tuple:
    """Changes when a file of the generators or the rulebook changes, is added or removed."""
    files = [p for d in CODE_DIRS for p in (root / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return len(files), max((p.stat().st_mtime_ns for p in files), default=0)


def deploy_transcript(logs_dir: Path, device: str, since: float) -> str:
    """The end of gnetcli's transcript of this device's latest deploy (files are
    <logs_dir>/<date>/<device>_<unix time>). Empty if there is none. Contains
    secrets: callers redact it before showing it."""
    found = [p for p in logs_dir.glob(f"*/{device}_*") if p.stat().st_mtime >= since - 1]
    if not found:
        return ""
    latest = max(found, key=lambda p: p.stat().st_mtime)
    lines = latest.read_text(errors="replace").splitlines()
    return "\n".join(lines[-TRANSCRIPT_TAIL:])


def _dial_failed(reply: dict) -> bool:
    """Every error is gnetcli failing to connect (nothing reached the switch in that connection)."""
    errors = [line for line in reply["errors"] if line.strip()]
    return bool(errors) and all("failed to dial" in line for line in errors)


def _raise_on_annet_errors(command: str, device: str, reply: dict) -> None:
    errors = reply["errors"]
    if any("No devices found" in line for line in errors):
        raise AnnetError(f"annet {command}: {device} was not found in NetBox")
    if errors:
        raise AnnetError(f"annet {command} reported errors for {device}:\n" + "\n".join(errors[-20:]))


GENERATOR_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def _gens(generators: list[str] | None) -> list[str]:
    if not generators:
        return []
    bad = [g for g in generators if not GENERATOR_NAME.fullmatch(g)]
    if bad:
        raise AnnetError(f"invalid generator names: {bad}")
    return ["-g", ",".join(generators)]
