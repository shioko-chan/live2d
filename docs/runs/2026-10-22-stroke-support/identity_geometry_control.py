"""Reproduce the completed fresh-binder assay and read actual geometry.

Two closed-pose renders only verify reopening reproduces the existing evidence.
The earlier temporary session did not save post-binding state. Replay its exact
blink and component binding steps in new copies; no historical facial repairs,
formal revision or acceptance. Preserve the first failed read-only audit.
"""

import asyncio
from base64 import b64decode
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image
from live2d_pipeline import assets
from live2d_pipeline.config import Config
from live2d_pipeline.refinement import bind
from live2d_pipeline.rig import open_rig, blink_operations
from live2d_pipeline.store import digest, lock, read_json, write_json

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "projects/mio-starlibrary-20261015"
SOURCE = PROJECT / "production/identity-prep-20261023/rig-control"
OUT = SOURCE / "geometry-audit-replayed"
POSES = {"neutral": {}, "closed": {"ParamEyeLOpen": 0, "ParamEyeROpen": 0},
         "turn-left": {"ParamAngleX": -14, "ParamAngleY": 8}}
MESHES = ["ArtMeshEyelashL", "ArtMeshEyelashR", "ArtMeshEyewhiteL", "ArtMeshEyewhiteR",
          "ArtMeshEyebrowL", "ArtMeshEyebrowR"]
WARPS = ["DeformEyes", "DeformEyeShapeL", "DeformEyeShapeR"]


async def inspect(config, mode, work):
    async with open_rig(config, work / "source.psd", work / "source.psd2live",
                        work / "control", OUT / (mode + ".log")) as (rig, _):
        layers = (await rig.call("project_list_layers"))["layers"]
        await rig.apply(blink_operations(layers, await rig.objects()))
        write_json(OUT / (mode + "-components.json"), await bind(rig, assets.load_parts(SOURCE / mode / "materials")))
        defaults = {p["id"]: p["default"] for p in (await rig.call("project_list_parameters"))["parameters"]}
        objects = {}
        for kind, names in [("mesh", MESHES), ("warp", WARPS)]:
            for name in names:
                objects[name] = await rig.call("object_get", {"target": {"kind": kind, "id": name}})
        write_json(OUT / (mode + "-objects.json"), objects)
        geometry = {}
        for pose_name, changes in POSES.items():
            pose = defaults | changes
            for name in MESHES:
                query = {"target": {"kind": "mesh", "id": name}, "coordinate": pose,
                         "space": "canvas", "detail": "points", "limit": 256}
                data = await rig.call("rig_inspect", query)
                while "nextOffset" in data:
                    page = await rig.call("rig_inspect", query | {"offset": data.pop("nextOffset")})
                    data["points"] += page["points"]
                    if "nextOffset" in page:
                        data["nextOffset"] = page["nextOffset"]
                geometry[pose_name + "/" + name] = data
            if pose_name == "closed":
                request = read_json(SOURCE / mode / "closed/actual.json")
                result = await rig.session.call_tool("view_render_model", request)
                assert not result.isError, str(result)
                output = OUT / (mode + "-closed.png")
                output.write_bytes(b64decode(next(i.data for i in result.content if i.type == "image")))
                assert Image.open(output).convert("RGBA").tobytes() == Image.open(SOURCE / mode / "closed/actual.png").convert("RGBA").tobytes(), "Reopening did not reproduce the evidence"
        write_json(OUT / (mode + "-geometry.json"), geometry)
        print(mode + " geometry captured; closed RGBA reproduced", flush=True)
        return geometry


def main():
    OUT.mkdir(exist_ok=False)
    write_json(OUT / "scope.json", {"poses": POSES, "meshes": MESHES, "warps": WARPS,
               "renders": 2, "model_edits": "Replay original blink/component defaults in disposable copies only",
               "purpose": "Separate actual non-iris geometry changes from texture-only rendering differences",
               "previous_attempt": "Read-only reopening failed before rendering: saved fresh workspaces omit later in-memory component parameters. No measurement from that incomplete state is accepted."})
    config = Config()
    hashes = {str(p): digest(p) for m in ["baseline", "uniform"] for p in
              [SOURCE / m / "source.psd", SOURCE / m / "source.psd2live", PROJECT / "project.json"]}
    results = {}
    with lock(config.runtime / "task.lock"), lock(PROJECT / ".lock"):
        for mode in ["baseline", "uniform"]:
            with tempfile.TemporaryDirectory(prefix="iris-geometry-") as temp:
                work = Path(temp)
                for filename in ["source.psd", "source.psd2live"]:
                    shutil.copyfile(SOURCE / mode / filename, work / filename)
                results[mode] = asyncio.run(inspect(config, mode, work))
        assert all(digest(Path(p)) == sha for p, sha in hashes.items())
    rows = []
    for key, base in results["baseline"].items():
        updated = results["uniform"][key]
        assert [p[0] for p in base["points"]] == [p[0] for p in updated["points"]]
        distances = [math.dist(a[1:], b[1:]) for a, b in zip(base["points"], updated["points"])]
        rows.append({"target": key, "vertices": len(distances), "max_canvas_distance": max(distances),
                     "changed_vertices": sum(d != 0 for d in distances)})
    write_json(OUT / "result.json", {"records": rows, "reopened_closed_rgba_identical": True,
               "sources_and_formal_state_unchanged": True, "renders": 2, "source_hashes": hashes})
    print(rows)


if __name__ == "__main__":
    main()
