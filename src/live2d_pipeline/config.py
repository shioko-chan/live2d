import tomllib
from pathlib import Path

from huggingface_hub.constants import HF_HUB_CACHE

ROOT = Path(__file__).resolve().parents[2]


class Config:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / "configs/local.toml").resolve()
        self.data = tomllib.loads(self.path.read_text())
        self.mode = self.data.get("execution", {}).get("mode", "agent")
        if self.mode not in {"agent", "local"}:
            raise ValueError("execution.mode must be agent or local")
        backend = self.data.get("execution", {}).get("inference", "container")
        if backend not in {"native", "container"}:
            raise ValueError("execution.inference must be native or container")
        if any(self.data["limits"][key] < 64 or self.data["limits"][key] % 16 for key in ("width", "height")):
            raise ValueError("Image dimensions must be at least 64 and multiples of 16")
        self.root = ROOT
        self.runtime = self.resolve(self.data["paths"]["runtime"])
        self.models = self.resolve(self.data["paths"]["models"])
        self.hf_cache = Path(HF_HUB_CACHE).expanduser().resolve()

    def resolve(self, path):
        p = Path(path).expanduser()
        return p if p.is_absolute() else self.root / p

    def __getitem__(self, key):
        return self.data[key]

    def require_local_models(self, operation):
        if self.mode != "local":
            raise ValueError(
                f"{operation} 需要本地模型；当前为 agent 模式。"
                "默认由 Codex 规划并使用 agent 自带的生图/修图功能，再导入计划或素材；"
                "其他图像工具仅在用户明确指定时使用。"
                "只有明确要求全本地运行时，才使用 execution.mode = \"local\" 的任务配置。"
            )

    def inference_command(self, engine, args, mounts=()):
        self.require_local_models(engine)
        if self.data.get("execution", {}).get("inference", "container") == "native":
            return [str(self.runtime / "envs" / engine / "bin/python"), *map(str, args)]
        roots = {self.root, self.runtime, self.models, *[Path(p).resolve() for p in mounts]}
        # Avoid duplicate/nested binds; retain paths so CLI and service outputs agree.
        roots = {p for p in roots if not any(p != q and p.is_relative_to(q) for q in roots)}
        import os
        import uuid

        command = [
            "docker",
            "run",
            "--rm",
            "--name",
            "l2d-" + uuid.uuid4().hex,
            "--init",
            "--network",
            "host",
            "--device",
            "nvidia.com/gpu=0",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--shm-size",
            "1g",
            "-e",
            "HF_HUB_OFFLINE=1",
            "-e",
            "TRANSFORMERS_OFFLINE=1",
            "-e",
            f"HF_HUB_CACHE={self.hf_cache}",
            "-e",
            "HOME=/tmp",
            "-e",
            f"L2D_LAMA_MODEL={self.models}/see-through/lama/lama_large_512px.ckpt",
            "--workdir",
            str(self.runtime / "sources" / engine),
        ]
        for path in sorted(roots):
            command += ["--volume", f"{path}:{path}"]
        command += ["--volume", f"{self.hf_cache}:{self.hf_cache}:ro"]
        return [*command, f"live2d-{engine}:pinned", *map(str, args)]
