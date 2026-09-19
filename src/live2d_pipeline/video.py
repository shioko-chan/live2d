"""Render a deterministic motion preview without modifying the built revision."""

import asyncio
from base64 import b64decode
import io
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image
import jsonschema

from .process import run
from .review import built_directory
from .rig import open_rig

FPS = 24
DURATION = 8


def motion(params, t):
    values = {key: p["default"] for key, p in params.items()}
    phase = 2 * math.pi * t / DURATION
    for key, p in params.items():
        amount = None
        if key in {"ParamEyeLOpen", "ParamEyeROpen"}:
            closure = max(max(0, 1 - abs(t - center) / 0.16) for center in (1.5, 4, 6.5))
            values[key] = p["default"] + (p["min"] - p["default"]) * closure
        elif key == "ParamMouthOpenY":
            amount = 0.65 * max(0, math.sin(phase * 4)) ** 2
        elif key == "ParamBreath":
            amount = 0.5 * (1 - math.cos(phase * 2))
        elif key.startswith("ParamAngle"):
            amount = 0.7 * math.sin(phase * (2 if key.endswith("Y") else 1))
        elif key.startswith("ParamBodyAngle"):
            amount = 0.35 * math.sin(phase)
        elif key in {"ParamEyeBallX", "ParamEyeBallY"}:
            amount = 0.5 * math.sin(phase * 2)
        elif key in {"ParamHairFront", "ParamHairBack"} or key.startswith("ParamComponent_"):
            amount = 0.65 * math.sin(phase * 3)
        if amount is not None:
            extent = p["max"] - p["default"] if amount >= 0 else p["default"] - p["min"]
            values[key] = p["default"] + extent * amount
        values[key] = max(p["min"], min(p["max"], values[key]))
    return values


def encode(frames, output, log):
    run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frames / "%04d.png"),
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-vf",
            "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-movflags",
            "+faststart",
            str(output),
        ],
        log,
    )


def generate(config, store, revision=None, progress=lambda message: None):
    rev, directory = built_directory(store, revision)
    if not shutil.which("ffmpeg"):
        raise ValueError("找不到 FFmpeg，请在 nix develop 环境中启动网页。")
    target = store.root / "previews" / rev.id
    target.mkdir(parents=True, exist_ok=True)
    # A disposable copy protects the saved workspace and its artifact hashes.
    with tempfile.TemporaryDirectory(prefix="video-", dir=target) as temporary:
        work = Path(temporary)
        shutil.copyfile(directory / "rig/source.psd2live", work / "source.psd2live")
        shutil.copyfile(directory / "rig/source.psd", work / "source.psd")
        frames = work / "frames"
        frames.mkdir()

        async def render():
            async with open_rig(
                config, work / "source.psd", work / "source.psd2live", work / "control", target / "render.log"
            ) as (rig, _):
                state = await rig.call("project_get_state")
                params = {p["id"]: p for p in (await rig.call("project_list_parameters"))["parameters"]}
                for index in range(FPS * DURATION):
                    args = {
                        "parameters": motion(params, index / FPS),
                        "viewport": {
                            "mode": "canvas_rect",
                            "left": 0,
                            "top": 0,
                            "width": state["canvasWidth"],
                            "height": state["canvasHeight"],
                        },
                        "background": "transparent",
                        "target_long_edge": 512,
                    }
                    jsonschema.validate(args, rig.tools["view_render_model"])
                    result = await rig.session.call_tool("view_render_model", args)
                    images = [block for block in result.content if block.type == "image"]
                    if result.isError or not images:
                        raise RuntimeError(f"视频帧渲染失败：{index}")
                    with Image.open(io.BytesIO(b64decode(images[0].data))) as image:
                        image = image.convert("RGBA")
                        background = Image.new("RGBA", image.size, "#f4f5f7")
                        Image.alpha_composite(background, image).convert("RGB").save(
                            frames / f"{index:04d}.png"
                        )
                    if index % FPS == 0 or index == FPS * DURATION - 1:
                        progress(f"渲染预览视频 {index + 1}/{FPS * DURATION} 帧")

        progress("启动模型预览渲染器")
        asyncio.run(render())
        progress("编码 MP4 视频")
        encode(frames, work / "preview.mp4", target / "encode.log")
        (work / "preview.mp4").replace(target / "preview.mp4")
    return target / "preview.mp4"
