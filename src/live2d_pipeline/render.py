"""Render a requested pose from a disposable copy of the editable workspace."""

import asyncio
from base64 import b64decode
import shutil
import tempfile
import uuid
from pathlib import Path

import jsonschema
from pydantic import Field, model_validator

from .review import built_directory
from .rig import open_rig
from .schema import Strict
from .store import digest, write_json


class RenderRequest(Strict):
    parameters: dict[str, float] = Field(default_factory=dict)
    # Full model canvas coordinates: left, top, width, height.
    region: tuple[float, float, float, float] | None = None
    long_edge: int = Field(default=768, ge=64, le=2048)

    @model_validator(mode="after")
    def valid_region(self):
        if self.region and (min(self.region[:2]) < 0 or min(self.region[2:]) <= 0):
            raise ValueError("Region requires nonnegative left/top and positive width/height")
        return self


def render_pose(config, store, request: RenderRequest, revision=None):
    rev, directory = built_directory(store, revision)
    output = store.root / "previews" / rev.id / "renders" / uuid.uuid4().hex
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="workspace-", dir=output) as temporary:
        work = Path(temporary)
        for name in ("source.psd", "source.psd2live"):
            shutil.copyfile(directory / "rig" / name, work / name)

        async def render():
            async with open_rig(
                config, work / "source.psd", work / "source.psd2live", work / "control", output / "render.log"
            ) as (rig, _):
                state = await rig.call("project_get_state")
                params = {p["id"]: p for p in (await rig.call("project_list_parameters"))["parameters"]}
                for key, value in request.parameters.items():
                    if key not in params:
                        raise ValueError(f"Unknown parameter: {key}")
                    if not params[key]["min"] <= value <= params[key]["max"]:
                        raise ValueError(f"Parameter out of range: {key}")
                x, y, width, height = request.region or (0, 0, state["canvasWidth"], state["canvasHeight"])
                if x + width > state["canvasWidth"] or y + height > state["canvasHeight"]:
                    raise ValueError("Render region exceeds model canvas")
                args = {
                    "parameters": {**{key: p["default"] for key, p in params.items()}, **request.parameters},
                    "viewport": {
                        "mode": "canvas_rect",
                        "left": x,
                        "top": y,
                        "width": width,
                        "height": height,
                    },
                    "background": "transparent",
                    "target_long_edge": request.long_edge,
                }
                jsonschema.validate(args, rig.tools["view_render_model"])
                result = await rig.session.call_tool("view_render_model", args)
                images = [item for item in result.content if item.type == "image"]
                if result.isError or not images:
                    raise RuntimeError("Pose renderer returned no usable image")
                (output / "pose.png").write_bytes(b64decode(images[0].data))
                return args

        arguments = asyncio.run(render())
    report = {
        "revision": rev.id,
        "image": str(output / "pose.png"),
        "sha256": digest(output / "pose.png"),
        "workspace_sha256": digest(directory / "rig/source.psd2live"),
        "request": request.model_dump(),
        "render_arguments": arguments,
        "runtime_verified": False,
        "physics_time_response": "not_evaluated",
    }
    write_json(output / "render.json", report)
    return report
