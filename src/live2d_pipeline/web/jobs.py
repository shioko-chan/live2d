"""One owned worker at a time, with durable job records and cooperative cancellation."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Literal
import uuid

from pydantic import Field

from ..schema import Strict
from ..store import read_json, write_json

ACTIVE = {"queued", "running", "cancelling"}


class JobRequest(Strict):
    action: Literal["plan", "run", "export", "checkout", "accept"]
    expected_head: str | None
    revision: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9]+$")
    request: str = Field(default="", max_length=6000)
    until: Literal["plan", "image", "materials", "rig"] = "rig"
    source: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}\.(png|jpg|jpeg|psd)$")
    reference: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}\.(png|jpg|jpeg)$")


class Jobs:
    def __init__(self, config, projects):
        self.config, self.projects = config, projects
        self.root = config.runtime / "web/jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.mutex = threading.Lock()
        self.process = None
        self.current = None

    def recover(self):
        # Called only with the exclusive web-server lock held.
        for path in self.root.glob("*/job.json"):
            data = read_json(path)
            if data["status"] in ACTIVE:
                data.update(
                    status="interrupted", error="上次网页服务中断；可以检查产物后继续。", ended=time.time()
                )
                write_json(path, data)

    def _reap(self):
        if self.process is None or self.process.poll() is None:
            return
        path = self.root / self.current / "job.json"
        data = read_json(path)
        if data["status"] in ACTIVE:
            cancelled = (path.parent / "cancel.request").exists()
            data.update(
                status="cancelled" if cancelled else "failed",
                error="任务已停止。"
                if cancelled
                else f"工作进程退出（{self.process.returncode}），请查看日志。",
                ended=time.time(),
            )
            write_json(path, data)
        self.process, self.current = None, None

    def list(self):
        with self.mutex:
            self._reap()
            records = [read_json(p) for p in self.root.glob("*/job.json")]
            for item in records:
                if item["id"] == self.current and item["status"] not in ACTIVE:
                    item.update(status="running", phase="正在完成清理")
                if item["status"] in ACTIVE and (self.root / item["id"] / "cancel.request").exists():
                    item["status"] = "cancelling"
            return sorted(records, key=lambda r: r["created"], reverse=True)[:100]

    def submit(self, project, spec):
        with self.mutex:
            self._reap()
            if self.process:
                raise RuntimeError("已有任务正在运行，请等待完成或先停止它。")
            job_id = uuid.uuid4().hex
            directory = self.root / job_id
            directory.mkdir()
            record = {
                "id": job_id,
                "project": project,
                "spec": spec.model_dump(),
                "status": "queued",
                "phase": "等待执行",
                "created": time.time(),
                "revision": spec.revision,
                "error": None,
            }
            write_json(directory / "job.json", record)
            command = [
                sys.executable,
                "-u",
                "-m",
                "live2d_pipeline.web.worker",
                str(self.config.path),
                str(self.projects),
                str(directory),
                str(os.getpid()),
            ]
            try:
                with (directory / "output.log").open("ab") as output:
                    self.process = subprocess.Popen(
                        command,
                        cwd=self.config.root,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            except OSError as exc:
                record.update(status="failed", error=str(exc), ended=time.time())
                write_json(directory / "job.json", record)
                raise
            self.current = job_id
            return record

    def cancel(self, job_id):
        with self.mutex:
            self._reap()
            if job_id != self.current or self.process is None:
                raise ValueError("这个任务已经结束。")
            (self.root / job_id / "cancel.request").touch()
            try:
                # Signal only the worker. Its finally blocks stop its own model processes/containers.
                self.process.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                self._reap()

    def close(self):
        if self.current:
            self.cancel(self.current)
        if self.process:
            self.process.wait()
        with self.mutex:
            self._reap()


def tail(path: Path, size=16000):
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - size))
        return stream.read(size).decode("utf-8", errors="replace").replace("\r", "\n")
