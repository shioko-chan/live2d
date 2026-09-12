"""Explicit source/environment/model preparation. Never runs on import."""

import shutil
import subprocess
from pathlib import Path
from .process import run
from .store import read_json, digest


def prepare_sources(config):
    sources = config.runtime / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    for name, info in read_json(config.root / "upstreams.lock.json").items():
        dest = sources / name
        if not dest.exists():
            subprocess.run(["git", "init", str(dest)], check=True)
            subprocess.run(["git", "-C", str(dest), "remote", "add", "origin", info["url"]], check=True)
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", info["revision"]], check=True
            )
            subprocess.run(["git", "-C", str(dest), "checkout", "--detach", info["revision"]], check=True)
        actual = subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip()
        if actual != info["revision"]:
            raise ValueError(f"{name} is not at pinned revision; use a new runtime directory")
    for engine in ("comfy", "see-through"):
        shutil.copyfile(
            config.root / "containers" / f"{engine}-requirements.lock.txt",
            sources / engine / "requirements.lock.txt",
        )
    (sources / ".dockerignore").write_text(
        "**/.git\n**/__pycache__\ncomfy/input\ncomfy/output\ncomfy/user\npsd2live/.gradle\npsd2live/build\n"
    )
    shutil.copyfile(
        config.root / "integrations/see-through/requirements.txt",
        sources / "see-through/requirements-inference.txt",
    )
    (sources / "psd2live/gradlew").chmod(0o755)
    # Known, local adapter additions; never modify unrelated checkouts.
    rig = sources / "psd2live"
    shutil.copyfile(
        config.root / "integrations/psd2live/PipelineServer.kt",
        rig / "src/main/kotlin/io/github/psd2live/PipelineServer.kt",
    )
    build = rig / "build.gradle.kts"
    baseline = subprocess.check_output(["git", "-C", str(rig), "show", "HEAD:build.gradle.kts"], text=True)
    build.write_text(
        baseline + "\n" + (config.root / "integrations/psd2live/build-addon.gradle.kts").read_text()
    )
    # Only six documented inference casts change. No dtype monkey-patching of torch.
    fp16 = sources / "see-through/common/utils/inference_utils.py"
    baseline = subprocess.check_output(
        ["git", "-C", str(sources / "see-through"), "show", "HEAD:common/utils/inference_utils.py"], text=True
    )
    if baseline.count("torch.bfloat16") != 6:
        raise ValueError("See-through precision adapter drift")
    if baseline.count("scheduler=None") != 1:
        raise ValueError("See-through scheduler adapter drift")
    baseline = "from diffusers import DPMSolverMultistepScheduler\n" + baseline
    baseline = baseline.replace(
        "scheduler=None",
        "scheduler=DPMSolverMultistepScheduler.from_pretrained(pretrained, "
        'subfolder="scheduler", local_files_only=True, algorithm_type="sde-dpmsolver++", '
        'final_sigmas_type="zero")',
    )
    fp16.write_text(baseline.replace("torch.bfloat16", "torch.float16"))
    lama_relative = "annotators/lama_inpainter/__init__.py"
    baseline = subprocess.check_output(
        ["git", "-C", str(sources / "see-through"), "show", f"HEAD:{lama_relative}"], text=True
    )
    remote_load = """torch.hub.load_state_dict_from_url("https://huggingface.co/dreMaz/AnimeMangaInpainting/resolve/main/lama_large_512px.ckpt", map_location = 'cpu')"""
    if baseline.count(remote_load) != 1:
        raise ValueError("See-through LaMa adapter drift")
    (sources / "see-through" / lama_relative).write_text(
        "import os\n"
        + baseline.replace(
            remote_load, 'torch.load(os.environ["L2D_LAMA_MODEL"], map_location="cpu", weights_only=True)'
        )
    )
    node = sources / "comfy/custom_nodes/ComfyUI-GGUF"
    if not node.exists():
        node.symlink_to(sources / "gguf", target_is_directory=True)
    config.runtime.mkdir(parents=True, exist_ok=True)
    (config.runtime / "extra_model_paths.yaml").write_text(
        "live2d:\n  base_path: " + str(config.models) + "\n  checkpoints: checkpoints\n"
        "  unet: unet\n  text_encoders: text_encoders\n  vae: vae\n"
    )


def install_environments(config):
    """Downloads software dependencies only; user calls explicitly after source preparation."""
    sources = config.runtime / "sources"
    log = config.runtime / "install.log"
    if config.data.get("execution", {}).get("inference", "container") == "container":
        for name in ("comfy", "see-through"):
            run(
                [
                    "docker",
                    "build",
                    "--build-arg",
                    f"ENGINE={name}",
                    "-t",
                    f"live2d-{name}:pinned",
                    "-f",
                    str(config.root / "containers/inference.Dockerfile"),
                    str(sources),
                ],
                log,
            )
    else:
        for name in ("comfy", "see-through"):
            env = config.runtime / "envs" / name
            run(["uv", "venv", "--python", "3.12", str(env)], log)
            python = str(env / "bin/python")
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    python,
                    "torch==2.8.0",
                    "torchvision==0.23.0",
                    "torchaudio==2.8.0",
                    "--index-url",
                    "https://download.pytorch.org/whl/cu128",
                ],
                log,
            )
            run(
                [
                    "uv",
                    "pip",
                    "install",
                    "--python",
                    python,
                    "-c",
                    str(sources / name / "requirements.lock.txt"),
                    "-r",
                    str(
                        sources
                        / name
                        / ("requirements-inference.txt" if name == "see-through" else "requirements.txt")
                    ),
                ],
                log,
                cwd=sources / name,
            )
            if name == "comfy":
                run(
                    [
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        python,
                        "-c",
                        str(sources / name / "requirements.lock.txt"),
                        "-r",
                        str(sources / "gguf/requirements.txt"),
                    ],
                    log,
                )
    # Resolve compiler dependencies once, so subsequent runs can be offline.
    run(
        [str(sources / "psd2live/gradlew"), "--no-daemon", "classes", "test"],
        log,
        cwd=sources / "psd2live",
        env={"GRADLE_USER_HOME": str(config.runtime / "gradle-home")},
    )


def download_models(config):
    from huggingface_hub import hf_hub_download, try_to_load_from_cache

    files = read_json(config.root / "models.lock.json")["files"]
    config.models.mkdir(parents=True, exist_ok=True)
    config.hf_cache.mkdir(parents=True, exist_ok=True)
    missing = [
        f
        for f in files
        if not isinstance(
            try_to_load_from_cache(f["repo"], f["file"], revision=f["revision"], cache_dir=config.hf_cache),
            str,
        )
    ]
    required = sum(f["bytes"] for f in missing)
    if required and shutil.disk_usage(config.hf_cache).free < required + 5 * 1024**3:
        raise RuntimeError(f"Need {required / 1024**3:.1f} GiB plus 5 GiB reserve in {config.hf_cache}")
    for index, f in enumerate(files, 1):
        print(f"[{index}/{len(files)}] {f['destination']} ({f['bytes'] / 1024**3:.2f} GiB)", flush=True)
        dest = config.models / f["destination"]
        cached = Path(
            hf_hub_download(
                repo_id=f["repo"], filename=f["file"], revision=f["revision"], cache_dir=config.hf_cache
            )
        )
        if cached.stat().st_size != f["bytes"] or (f["sha256"] and digest(cached) != f["sha256"]):
            raise RuntimeError(f"Weight verification failed: {f['file']}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            if dest.resolve() == cached.resolve():
                continue
            dest.unlink()
        elif dest.exists():
            raise ValueError(f"Existing file differs; refusing to overwrite {dest}")
        dest.symlink_to(cached.resolve())


def doctor(config):
    """Filesystem only; no network, torch import, CUDA query, or process launch."""
    report = {
        "gpu_accessed": False,
        "models_loaded": False,
        "free_disk_gib": round(shutil.disk_usage(config.root).free / 1024**3, 1),
        "planner_model": config.resolve(config["planner"]["model"]).is_file(),
        "llama_server": shutil.which(config["planner"]["executable"]),
        "sources": {},
        "models": {},
        "environments": {},
    }
    for name in read_json(config.root / "upstreams.lock.json"):
        report["sources"][name] = (config.runtime / "sources" / name).is_dir()
    for f in read_json(config.root / "models.lock.json")["files"]:
        p = config.models / f["destination"]
        report["models"][f["destination"]] = p.is_file() and p.stat().st_size == f["bytes"]
    for name in ("comfy", "see-through"):
        if config.data.get("execution", {}).get("inference", "container") == "container":
            report["environments"][name] = {
                "backend": "container",
                "image": f"live2d-{name}:pinned",
                "availability": "not_checked: filesystem-only doctor does not query Docker",
            }
        else:
            report["environments"][name] = {
                "backend": "native",
                "available": (config.runtime / "envs" / name / "bin/python").is_file(),
            }
    return report
