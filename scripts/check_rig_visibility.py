"""Real CPU pixel regression: hide an object, then restore its exact rendered pixels.

Run inside the project's prepared environment. Writes evidence outside revisions.
Supports a visible mesh or part; does not change project head or saved workspace.
"""

import argparse
import asyncio
from base64 import b64decode
from pathlib import Path
import shutil
import tempfile

from PIL import Image, ImageChops
from live2d_pipeline.config import Config
from live2d_pipeline.rig import open_rig
from live2d_pipeline.schema import Operation
from live2d_pipeline.store import Store, digest, lock, read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("revision")
    parser.add_argument("object_id")
    parser.add_argument("output", type=Path)
    parser.add_argument("--kind", choices=["mesh", "part"], default="mesh")
    parser.add_argument("--parameters", type=Path)
    args = parser.parse_args()
    config, store = Config(), Store(args.project.resolve())
    source = store.revision_dir(args.revision) / "rig"
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    pose = read_json(args.parameters) if args.parameters else {}

    async def check(work):
        async with open_rig(
            config, work / "source.psd", work / "source.psd2live", work / "control", output / "rig.log"
        ) as (rig, _):
            obj = await rig.call("object_get", {"target": {"kind": args.kind, "id": args.object_id}})
            if obj.get("visible") is not True:
                raise ValueError("Probe requires an initially visible object")
            state = await rig.call("project_get_state")
            defaults = {
                p["id"]: p["default"] for p in (await rig.call("project_list_parameters"))["parameters"]
            }
            request = {
                "parameters": defaults | pose,
                "viewport": {
                    "mode": "canvas_rect",
                    "left": 0,
                    "top": 0,
                    "width": state["canvasWidth"],
                    "height": state["canvasHeight"],
                },
                "background": "transparent",
                "target_long_edge": 1280,
            }
            write_json(output / "request.json", request)
            for name, visible in [("before", True), ("hidden", False), ("restored", True)]:
                await rig.apply(
                    [
                        Operation(
                            kind="rig",
                            tool="object_edit",
                            arguments={
                                "edits": [
                                    {
                                        "action": "visibility",
                                        "kind": args.kind,
                                        "id": args.object_id,
                                        "visible": visible,
                                    }
                                ]
                            },
                        )
                    ]
                )
                observed = await rig.call("object_get", {"target": {"kind": args.kind, "id": args.object_id}})
                assert observed["visible"] == visible
                result = await rig.session.call_tool("view_render_model", request)
                assert not result.isError, str(result)
                (output / f"{name}.png").write_bytes(
                    b64decode(next(i.data for i in result.content if i.type == "image"))
                )
            before = Image.open(output / "before.png").convert("RGBA")
            hidden = Image.open(output / "hidden.png").convert("RGBA")
            restored = Image.open(output / "restored.png").convert("RGBA")

            def bounds(image):
                return ImageChops.difference(before, image).convert("RGB").getbbox()

            hidden_bounds = bounds(hidden)
            assert hidden_bounds is not None, "visibility did not change actual pixels"
            assert before.tobytes() == restored.tobytes(), "restoring visibility changed pixels"
            return {
                "object": args.object_id,
                "kind": args.kind,
                "hidden_changed_bbox": hidden_bounds,
                "restored_pixel_identical": True,
                "passed": True,
            }

    with (
        lock(config.runtime / "task.lock"),
        lock(store.root / ".lock"),
        tempfile.TemporaryDirectory(prefix="visibility-regression-") as temporary,
    ):
        head = store.get().id
        hashes = {n: digest(source / n) for n in ["source.psd", "source.psd2live"]}
        work = Path(temporary)
        for n in hashes:
            shutil.copyfile(source / n, work / n)
        result = asyncio.run(check(work))
        assert store.get().id == head and all(digest(source / n) == v for n, v in hashes.items())
        result.update({"head_unchanged": True, "source_unchanged": True, "revision": args.revision})
        write_json(output / "result.json", result)
        print(result)


if __name__ == "__main__":
    main()
