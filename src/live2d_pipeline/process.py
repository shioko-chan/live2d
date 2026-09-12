"""Own only processes started by this invocation; never touch unrelated GPU jobs."""

import contextlib
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
import httpx


def free_port(port):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def stop(proc):
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def terminate_invocation(signum, frame):
    # Python's default SIGTERM bypasses finally blocks, leaving owned services alive.
    raise SystemExit(128 + signum)


@contextlib.contextmanager
def owned_process(command, log: Path, cwd=None, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as stream:
        previous_handler = signal.signal(signal.SIGTERM, terminate_invocation)
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                env={**os.environ, **(env or {})},
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                yield proc
            finally:
                stop(proc)
                # Killing a docker client alone may leave its container running. Remove only our UUID name.
                if command[:2] == ["docker", "run"] and "--name" in command:
                    name = command[command.index("--name") + 1]
                    if name.startswith("l2d-"):
                        subprocess.run(
                            ["docker", "rm", "-f", name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=30,
                            check=False,
                        )
        finally:
            signal.signal(signal.SIGTERM, previous_handler)


def run(command, log, timeout=3600, cwd=None, env=None):
    with owned_process(command, log, cwd, env) as proc:
        try:
            result = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Timed out; see {log}") from None
        if result:
            raise RuntimeError(f"Process exited {result}; see {log}")


def wait_http(proc, url, timeout):
    end = time.monotonic() + timeout
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < end:
            if proc.poll() is not None:
                raise RuntimeError("Service exited before ready; inspect its log")
            try:
                if client.get(url).is_success:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise TimeoutError(f"Service startup timed out: {url}")
