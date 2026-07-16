"""Command dispatcher for the standalone claims disposition SQL pipeline."""

from __future__ import annotations

from collections.abc import Sequence


COMMANDS = ("fixture", "run", "verify", "e2e")


def _run_command(command: str, arguments: list[str]) -> int:
    if command == "fixture":
        from . import fixture_loader

        return fixture_loader.main(arguments)
    if command == "run":
        from . import pipeline

        return pipeline.main(arguments)
    if command == "verify":
        from . import verify_outputs

        return verify_outputs.main(arguments)
    raise ValueError(f"unsupported command: {command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one command or the fixed fixture/run/verify end-to-end flow."""

    args = list(argv or [])
    if not args or args[0] not in COMMANDS:
        raise SystemExit("usage: run_demo.py {fixture|run|verify|e2e}")

    command, arguments = args[0], args[1:]
    if command != "e2e":
        return _run_command(command, arguments)
    if arguments:
        raise SystemExit("usage: run_demo.py e2e")

    for step in ("fixture", "run", "verify"):
        result = _run_command(step, [])
        if result:
            return result
    return 0
