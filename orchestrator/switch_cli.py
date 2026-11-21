"""Commands sent to a switch outside a config patch: `save`, and the upgrade's download,
digest check, startup and reboot.

They go through gnetcli like every other switch session (the platform's only CLI transport):
the gnetcli server from the image (our known-hosts patch, so the pinned host key is enforced),
the platform login from GNETCLI_DEV_LOGIN / GNETCLI_DEV_PASSWORD. Each command carries the
questions it may ask, worded exactly as the lab switch asked them; any
other question is left unanswered, so gnetcli fails the command instead of guessing.
"""

import dataclasses
import re
from dataclasses import dataclass, replace


class SwitchCliError(Exception):
    pass


@dataclass(frozen=True)
class Step:
    command: str
    questions: tuple[tuple[str, str], ...] = ()   # (question as the switch prints it, answer)
    timeout: float = 60.0
    secret: bool = False   # the command holds a password: never logged or shown
    hide: tuple[str, ...] = ()   # its secret values: masked wherever the command or an error is shown

    def mask(self, text: str) -> str:
        for value in self.hide:
            text = text.replace(value, "<hidden>")
        return text
    expect: str | None = None   # a line the output must contain


# seen on V300R025.
SAVE = Step("save", (("Warning: The current configuration will be written to the device. Continue? [Y/N]:", "Y"),),
            expect="Info: Save the configuration successfully.")

# VRP marks a refused command with a line starting "Error:" (e.g. "Error: Wrong parameter found").
_ERROR = re.compile(r"^\s*Error:", re.M)


def _shown(step: Step) -> str:
    if step.secret and not step.hide:
        return "<command with a password>"
    return step.mask(step.command)


def check_output(step: Step, output: str) -> None:
    shown = _shown(step)
    if _ERROR.search(output):
        if step.secret and not step.hide:
            raise SwitchCliError(f"{shown}: the switch refused it")
        raise SwitchCliError(f"{shown}: the switch refused it: {step.mask(_ERROR.split(output, 1)[1].strip()[:300])}")
    if step.expect and step.expect not in output:
        raise SwitchCliError(f"{shown}: the switch did not answer {step.expect!r}")


async def run(device: str, ip: str, steps: list, abort: Step | None = None) -> list[str]:
    """Send the steps in one session and return each one's output. Stops at the first failure.

    A step may be an async check, which runs while the session stays open and returns what is
    wrong, or None: on an answer `abort` is sent in this session and the run fails with it."""
    from gnetcli_adapter.gnetcli_adapter import AppSettings
    from gnetclisdk.client import QA, Gnetcli, HostParams
    from gnetclisdk.starter import GnetcliStarter

    conf = AppSettings()   # the same GNETCLI_* environment Annet's gnetcli adapter reads
    params = HostParams(device="huawei", ip=ip, credentials=conf.make_dev_credentials())
    outputs = []
    # A free port of its own, as Annet's runs (deploy/annet/context.yml): runs may overlap.
    async with GnetcliStarter(conf.server_path, replace(conf.server_conf, port="127.0.0.1:0")) as url:
        api = Gnetcli(server=url, insecure_grpc=True, user_agent="orchestrator")
        async with api.cmd_session(hostname=device, host_params=params) as session:
            queue = list(steps)
            failed = None
            while queue:
                step = queue.pop(0)
                if callable(step):
                    if failed := await step():
                        queue = [abort]
                    continue
                try:
                    result = await session.cmd(
                        cmd=step.command, cmd_timeout=step.timeout, host_params=params,
                        qa=[QA(question=q, answer=a) for q, a in step.questions],
                    )
                except Exception as exc:
                    if not step.secret:
                        raise
                    raise SwitchCliError(f"{_shown(step)}: {step.mask(str(exc))[:500] if step.hide else 'gnetcli failed'}") from None
                output = result.out.decode(errors="replace")
                if result.status != 0:
                    error = result.error.decode(errors="replace").strip()
                    if step.secret and not step.hide:   # gnetcli's error may echo the command
                        raise SwitchCliError("<command with a password>: the switch session failed")
                    raise SwitchCliError(f"{_shown(step)}: {step.mask(error)[:500] or 'the switch session failed'}"
                                         + (f" (output: {step.mask(output)[-300:]!r})" if output.strip() else ""))
                check_output(step, output)
                outputs.append(output)
            if failed:
                raise SwitchCliError(failed)
    return outputs


# -- switch login under a trial commit --------------------------------

TRIAL_SECONDS = 300
ABORT_TRIAL = Step("abort trial", (("Warning: The trial configuration will be rolled back. Continue? [Y/N]:", "Y"),),
                   timeout=180.0, expect="rollback succeeded")
CONFIRM_TRIAL = Step("commit", (("Warning: The trial configuration will be confirmed. Continue? [Y/N]:", "Y"),),
                     timeout=180.0)


# Local accounts' questions, as a test switch asked them (candidate only).
ACCOUNT_QUESTIONS = (
    ("Warning: This operation may affect online users, are you sure to change the user privilege level ?[Y/N]", "Y"),   # Annet's, for `undo local-user`
    ("Warning: This operation may affect online users and will change the user privilege level, Continue? [Y/N]:", "Y"),
    ("Warning: If the service type of local users is configured multiple times, the new configuration overwrites the "
     "previous one. The new configuration may cause local user authentication to fail. Continue? [Y/N]:", "Y"),
)


def trial_steps(patch: str, check, hide: tuple[str, ...]) -> list:
    """The patch, `commit trial`, `check` while it can still revert, then confirmed."""
    lines = [Step(line.strip(), ACCOUNT_QUESTIONS, timeout=60.0, hide=hide) for line in patch.splitlines() if line.strip()]
    return [SYSTEM_VIEW, *lines,
            Step(f"commit trial {TRIAL_SECONDS}", timeout=180.0, expect="The system enters the trial configuration mode"),
            check, CONFIRM_TRIAL, RETURN]


# -- the upgrade's commands --------------------------------

DISPLAY_VERSION = Step("display version")
# CE6866 on V300R025C10 refuses `snmp-agent sys-info version v2c` until this is installed
# (2026-11-19). Already installed it prints "The package that has been installed will be
# skipped: WEAKEA." and asks nothing; a real install is unseen, so any question it asks fails it.
WEAKEA = Step("install feature-software WEAKEA", timeout=600.0)
DISPLAY_STARTUP = Step("display startup")
DIR = Step("dir flash:/")
# `sftp client-transfile` exists in system view only (user view:
# "Unrecognized command"). Nothing is configured there, so `return` asks nothing.
SYSTEM_VIEW = Step("system-view")
RETURN = Step("return")
# Both questions, in the order asked; the save is right: the diff was empty just before.
REBOOT = Step("reboot", (
    ("Warning: The current configuration will be saved to the next startup saved-configuration file. Continue? [Y/N]:", "Y"),
    ("Warning: The system will reboot. Continue? [Y/N]:", "Y"),
), timeout=120.0)


def delete(name: str) -> Step:
    """Permanently (no recycle bin: the space is free at once)."""
    return Step(f"delete /unreserved flash:/{name}",
                ((f"Warning: The file flash:/{name} cannot be recycled. Continue? [Y/N]:", "Y"),), expect="Done.")


def download(host: str, port: int, login: str, password: str, name: str, size: int) -> Step:
    """From the platform's image server, verified against its pinned key (no question).
    The destination is never there (deleted or digest-checked first): overwriting would ask."""
    return Step(f"sftp client-transfile get host-ip {host} {port} -vpn-instance _management_vpn_ "
                f"username {login} password {password} sourcefile {name} destination flash:/{name}",
                timeout=600.0 + size / 100_000, secret=True, hide=(login, password),   # 1 MB took 2 s; allow 100 kB/s
                expect="Downloading file successfully ended.")


def digest(name: str, sha256: str) -> Step:
    return Step(f"check file-digest digest-algorithm sha256 {sha256} flash:/{name}", timeout=600.0,
                expect="is the same as the input digest value.")


def has_digest(release: str) -> bool:
    # shortcut: only R023 is known to lack `check file-digest` (an imported switch; R024 and
    # R025 have it), add any other release found without it here
    return not release.startswith("V300R023")


def verify(name: str, sha256: str, kind: str, release: str) -> Step:
    """The downloaded or present image checked on the switch: its SHA-256 where the release can, else
    (R023) the package's own integrity check after the exact size was compared.
    `check software` asked one question and said "The integrity check for the system software of the
    <release> version succeeds." in 7 s on R023; `check patch` on another file
    asked "Warning: Package verification consumes system CPU resources. Continue? [Y/N]:" ; its answer is unseen: only its refusal fails it, and an unexpected question fails it too."""
    if has_digest(release):
        return digest(name, sha256)
    if kind == "system":
        cpu = (("Warning: Software file verification consumes CPU resources. Continue? [Y/N]:", "Y"),)
        return Step(f"check software file-name flash:/{name}", cpu, timeout=900.0,
                    expect="The integrity check for the system software of the ")
    cpu = (("Warning: Package verification consumes system CPU resources. Continue? [Y/N]:", "Y"),)
    return Step(f"check patch flash:/{name}", cpu, timeout=900.0)


def size_of(name: str) -> Step:
    return Step(f"dir flash:/{name}")


def startup(kind: str, name: str) -> Step:
    # `startup patch <file>` alone is "Incomplete command": it needs a slot or `all` (test switch);
    # `startup system-software <file>` took a new file as is. `display startup` confirms both afterwards.
    if kind == "system":
        return Step(f"startup system-software flash:/{name}", timeout=900.0)
    return Step(f"startup patch flash:/{name} all", timeout=900.0)


_RELEASE = re.compile(r"\((?:\S+ )?(V\d{3}R\d{3}C\d{2}SPC\d{3})\)")
_PATCH = re.compile(r"^Patch Version:\s*(\S+)", re.M)
_STARTUP = re.compile(r"^\s*(Startup|Next startup) (system software|patch package):\s*(\S+)", re.M)
_FILE = re.compile(r"^\s*\d+\s+(\S+)\s+([\d,]+|-)\s+\w{3}\s+\d+\s+\d{4}\s+[\d:]+\s+(\S+)\s*$", re.M)
_FREE = re.compile(r"([\d,]+) KB total \(([\d,]+) KB free\)")


def version(output: str) -> tuple[str | None, str | None]:
    """(release, patch) from `display version`: `... (CE6881H V300R025C00SPC500)`, `Patch Version: V300R025SPH120`."""
    release, patch = _RELEASE.search(output), _PATCH.search(output)
    return (release.group(1) if release else None, patch.group(1) if patch else None)


def startup_files(output: str) -> dict[str, str | None]:
    """`display startup` as {"system", "patch", "next system", "next patch"}: flash file names, None for NULL."""
    found = {}
    for when, what, value in _STARTUP.findall(output):
        key = ("next " if when == "Next startup" else "") + ("system" if what == "system software" else "patch")
        found[key] = None if value in ("NULL", "default") else value.removeprefix("flash:/")
    return found


def flash(output: str) -> tuple[dict[str, int], int]:
    """`dir flash:/` as ({file: bytes} without directories, free bytes)."""
    files = {name: int(size.replace(",", "")) for attr, size, name in _FILE.findall(output)
             if not attr.startswith("d") and size != "-"}
    free = _FREE.search(output)
    if free is None:
        raise SwitchCliError("dir flash:/ did not show the free space")
    return files, int(free.group(2).replace(",", "")) * 1024
