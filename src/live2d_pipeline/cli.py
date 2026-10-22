from enum import Enum
from typing import Annotated
from pathlib import Path
import typer
from .config import Config
from .pipeline import Pipeline
from .services import plan_revision, export_revision
from .store import Store, lock, read_json
from .validation import validate_bundle
from . import setup
from .cli_io import json_errors, show
from .tool_cli import register

app = typer.Typer(
    no_args_is_help=True,
    help="Agent-driven Live2D tools. Local models require execution.mode=local for full-local jobs.",
)


def cfg(path):
    return Config(path)


@app.command()
def init(project: Path):
    """Create an empty project; no service startup."""
    Store(project).init()
    typer.echo(str(project.resolve()))


@app.command()
@json_errors
def plan(
    project: Path,
    request: str,
    source: Path | None = None,
    reference: Path | None = None,
    plan_file: Path | None = None,
    config: Path | None = None,
):
    """Import a JSON plan; local text planning requires explicit full-local mode."""
    c, store = cfg(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        rev = plan_revision(c, store, request, source, reference, plan_file)
        show({"revision": rev.id, "plan": rev.plan.model_dump(), "executed": False})


class StopAfter(str, Enum):
    image = "image"
    materials = "materials"
    rig = "rig"


@app.command()
@json_errors
def run(
    project: Path, revision: str | None = None, config: Path | None = None, until: StopAfter = StopAfter.rig
):
    """Explicitly execute a plan. This DOES load models/use GPU where needed."""
    c, store = cfg(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        rev = store.get(revision)
        result = Pipeline(c, store).run(rev, until=until.value)
        show({"output": result, "revision": rev.id, "status": rev.status, "runtime_verified": False})


@app.command()
def revise(
    project: Path,
    request: str,
    execute: bool = False,
    plan_file: Path | None = None,
    config: Path | None = None,
):
    """Create a revision using natural language. --execute also runs image/rig stages."""
    plan(project, request, plan_file=plan_file, config=config)
    if execute:
        run(project, config=config)


@app.command()
@json_errors
def review(
    project: Path,
    request: Annotated[str, typer.Argument()] = "检查模型的细节和动作",
    revision: str | None = None,
    config: Path | None = None,
):
    """Run local VLM review in full-local mode. For agent inspection use inspect."""
    from .review import inspect

    c, store = cfg(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        show(inspect(c, store, request, revision))


@app.command()
@json_errors
def refine(
    project: Path,
    request: Annotated[str, typer.Argument()] = "完善头发、服装和饰品的独立部件与小幅摆动",
    revision: str | None = None,
    config: Path | None = None,
):
    """Create/inspect drafts with the local VLM in full-local mode; does not apply."""
    from .review import refine as refine_model

    c, store = cfg(config), Store(project)
    with lock(c.runtime / "task.lock"), lock(store.root / ".lock"):
        report = refine_model(c, store, request, revision)
        show({"review": report, "accepted": False})


@app.command()
def resume(project: Path, revision: str | None = None, config: Path | None = None):
    """Explicit execution: reuse verified completed stages and retry unfinished stages."""
    run(project, revision, config)


@app.command()
def status(project: Path, revision: str | None = None):
    show(Store(project).get(revision).model_dump())


@app.command()
def history(project: Path):
    store = Store(project)
    show(
        {
            "project": read_json(store.root / "project.json"),
            "revisions": [
                {"id": r["id"], "parent": r["parent"], "status": r["status"], "request": r["plan"]["request"]}
                for r in store.history()
            ],
        }
    )


@app.command()
def checkout(project: Path, revision: str):
    """Select the starting point for subsequent work."""
    store = Store(project)
    with lock(store.root / ".lock"):
        store.checkout(revision)


@app.command()
def accept(project: Path, revision: str):
    """Mark a built revision as manually accepted without switching the working head."""
    store = Store(project)
    with lock(store.root / ".lock"):
        store.accept(revision)


@app.command()
def export(project: Path, destination: Path, revision: str | None = None):
    """Package a completed model and sources, with no inference."""
    store = Store(project)
    with lock(store.root / ".lock"):
        typer.echo(str(export_revision(store, destination, revision)))


@app.command()
def web(port: int = typer.Option(7860, min=1024, max=65535), config: Path | None = None):
    """Open the local web workspace. Startup does not load models."""
    import uvicorn
    from .web.app import create_app

    typer.echo(f"Live2D 工作台：http://127.0.0.1:{port}（打开网页不会加载模型）")
    uvicorn.run(create_app(cfg(config)), host="127.0.0.1", port=port, log_level="info", access_log=False)


@app.command()
def doctor(config: Path | None = None):
    """Offline filesystem check. Does not query GPU or start any process."""
    show(setup.doctor(cfg(config)))


@app.command("prepare-sources")
def prepare_sources(config: Path | None = None):
    """Explicitly download pinned source code only, and apply local adapters."""
    c = cfg(config)
    with lock(c.runtime / "task.lock"):
        setup.prepare_sources(c)


@app.command("install-environments")
def install_environments(config: Path | None = None):
    """Download/build software dependencies. No weight downloads or model inference."""
    c = cfg(config)
    with lock(c.runtime / "task.lock"):
        setup.install_environments(c)


@app.command("download-models")
def download_models(config: Path | None = None):
    """Explicitly download pinned model files; never called automatically by run."""
    c = cfg(config)
    with lock(c.runtime / "task.lock"):
        setup.download_models(c)


@app.command("verify-runtime")
def verify_runtime(bundle: Path, core: Path = typer.Option(..., "--core"), config: Path | None = None):
    """Explicit optional native Core check, using the user's local library; CPU only."""
    from .process import run as run_process
    import sys

    c = cfg(config)
    with lock(c.runtime / "task.lock"):
        validate_bundle(bundle)
        run_process(
            [
                sys.executable,
                str(c.root / "scripts/check_core.py"),
                str(bundle.resolve()),
                str(core.resolve()),
            ],
            bundle.parent / "core-verification.log",
            timeout=60,
            env={"CUDA_VISIBLE_DEVICES": ""},
        )
    typer.echo("Core loaded and updated the model. Visual/physics compatibility still needs inspection.")


register(app)


if __name__ == "__main__":
    app()
