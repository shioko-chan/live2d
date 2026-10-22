from pathlib import Path

import typer

from .cli_io import show
from .runtime_check import verify_playback as verify


def verify_playback(
    bundle: Path,
    trace_file: Path,
    core: Path = typer.Option(
        ..., envvar="CUBISM_CORE_LIBRARY",
        help="Official Cubism Core library; defaults to CUBISM_CORE_LIBRARY.",
    ),
    timeout: float = typer.Option(120, min=1),
):
    """Check a trace in official Core; this does not validate physics, pixels or a target app."""
    report = verify(bundle, trace_file, core, timeout)
    show(report)
    if report["status"] != "passed":
        raise typer.Exit(1)


def register(app):
    app.command()(verify_playback)
