import functools
import json

import typer


def show(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def json_errors(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (ValueError, RuntimeError, OSError) as exc:
            show({"error": type(exc).__name__, "message": str(exc)})
            raise typer.Exit(1) from exc

    return wrapped
