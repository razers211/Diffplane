"""One Annet worker process (orchestrator/annet.py keeps a pool of them).

It loads Annet, the generators and the rulebook once, then runs one command at a time for the
orchestrator: Annet's own command functions (`annet.cli`), called in this process with options
parsed by Annet's own parser. Their results are not printed: an output driver hands each one back
as it is, (label, text, failed), so nothing is parsed from text. Errors Annet logs are sent back
with them, since Annet reports some failures only in its log ("No devices found").

Protocol, one JSON object per line: the request on stdin
    {"argv": [...annet arguments...], "env": {...}}
and the reply on the original stdout
    {"code": int, "items": [[label, text, failed], ...], "errors": ["LEVEL message", ...]}
Everything else Annet prints goes to stderr. `env` replaces the environment for that run, so a
setting changed in the platform (a login, a community) applies to the next run.
"""

import io
import json
import logging
import os
import sys


class _Collected(logging.Handler):
    """Log records at ERROR or above during one run."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(f"{record.levelname} {record.getMessage()}")


def _driver(items: list):
    from annet.annlib.output import OutputWriter
    from annet.output import OutputDriverBasic

    class Collecting(OutputDriverBasic):
        """Results handed back instead of written (labels as Annet's own output gives them)."""

        def write_output(self, arg_out, results, query_result_count=1):
            for label, output, failed in results:
                text = io.StringIO()
                (output if isinstance(output, OutputWriter) else OutputWriter(output)).write(text)
                items.append([os.path.normpath(label), text.getvalue(), bool(failed)])

    return Collecting


def main() -> int:
    reply = os.fdopen(os.dup(1), "w")   # the protocol; Annet's prints go to stderr
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    import annet
    from annet import argparse, cli, diff, generators, hardware, output, rulebook

    items: list = []
    parser = argparse.ArgParser()
    cli.fill_base_args(parser, annet.__name__, "configs/logging.yaml")
    rulebook.rulebook_provider_connector.set(rulebook.DefaultRulebookProvider)
    hardware.hardware_connector.set(hardware.AnnetHardwareProvider)
    diff.file_differ_connector.set(diff.UnifiedFileDiffer)
    output.output_driver_connector.set(_driver(items))
    parser.add_commands([*parser.find_subcommands(cli.list_subcommands()), *parser.find_entry_point_commands()])
    started = False
    collected = _Collected()

    for line in sys.stdin:
        request = json.loads(line)
        os.environ.clear()
        os.environ.update(request["env"])
        items.clear()
        collected.lines.clear()
        code = 0
        try:
            ns = parser.parse_args(request["argv"])
            if not started:   # logging and colours, as `annet` does before its first command
                annet.init(ns)
                logging.getLogger().addHandler(collected)
                started = True
            values = [getattr(ns, arg.dest) if isinstance(arg, argparse.Arg) else arg.construct_from(ns)
                      for arg in parser.func_opts(ns.func)]
            code = ns.func(*values) or 0
        except (generators.GeneratorError, annet.ExecError) as exc:   # as `annet` reports them
            collected.lines.append(f"ERROR {exc}")
            code = 1
        except SystemExit as exc:   # argparse refusing the arguments
            collected.lines.append(f"ERROR invalid annet arguments (exit {exc.code})")
            code = 2
        except BaseException as exc:   # noqa: BLE001  the worker survives; the run fails with the reason
            collected.lines.append(f"ERROR {getattr(exc, 'formatted_output', None) or repr(exc)}")
            code = 1
        reply.write(json.dumps({"code": code, "items": items, "errors": collected.lines}) + "\n")
        reply.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
