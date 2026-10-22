"""Causal control: add only an identity child warp on a disposable baseline copy.

No new revision, repair candidate or saved model change. Compare full-open actual
pixels/vertices with baseline and the already rendered structural candidate.
"""

import asyncio
from base64 import b64decode
import json
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image, ImageChops
from live2d_pipeline.config import Config
from live2d_pipeline.rig import open_rig
from live2d_pipeline.schema import Operation
from live2d_pipeline.store import Store, digest, lock, write_json

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "projects/mio-starlibrary-20261015"
OUT = PROJECT / "production/independent-closure-analysis-20261022/identity-warp-control"
PREVIOUS = OUT.parent / "candidate-probe"
MESHES = ["ArtMeshMouthOpen", "ArtMeshMouthOpen_lip_0", "ArtMeshMouthOpen_lip_1"]


async def run(config, work):
    async with open_rig(config, work / "source.psd", work / "source.psd2live", work / "control", OUT / "rig.log") as (rig, _):
        defaults = {p["id"]: p["default"] for p in (await rig.call("project_list_parameters"))["parameters"]}
        geometry = {}
        for mode in ["baseline", "identity-only"]:
            if mode == "identity-only":
                await rig.apply([Operation(kind="rig", tool="warp_create", arguments={
                    "id": "DeformCommonMouthClosure", "name": "Identity-only diagnostic",
                    "parent_id": "DeformMouthShapeBoth", "mesh_ids": MESHES, "rows": 3, "columns": 16,
                })])
                write_json(OUT / "identity-warp.json", await rig.call("object_get", {
                    "target": {"kind": "warp", "id": "DeformCommonMouthClosure"},
                }))
            for form in [-1, 0, 1]:
                pose = defaults | {"ParamMouthForm": form, "ParamMouthOpenY": 1}
                request = {"parameters": pose, "viewport": {"mode": "canvas_rect", "left": 0, "top": 0,
                           "width": 1024, "height": 1280}, "background": "transparent", "target_long_edge": 1280}
                rendered = await rig.session.call_tool("view_render_model", request)
                assert not rendered.isError, str(rendered)
                (OUT / f"{mode}-form-{form}.png").write_bytes(b64decode(next(i.data for i in rendered.content if i.type == "image")))
                write_json(OUT / f"{mode}-form-{form}.json", request)
                for mesh in MESHES:
                    query = {"target": {"kind": "mesh", "id": mesh}, "coordinate": pose,
                             "space": "canvas", "detail": "points", "limit": 256}
                    data = await rig.call("rig_inspect", query)
                    while "nextOffset" in data:
                        offset = data.pop("nextOffset")
                        page = await rig.call("rig_inspect", query | {"offset": offset})
                        data["points"] += page["points"]
                        if "nextOffset" in page:
                            data["nextOffset"] = page["nextOffset"]
                    geometry[f"{mode}/form-{form}/{mesh}"] = data
            print(f"Completed {mode}", flush=True)
        write_json(OUT / "geometry.json", geometry)
        return geometry


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    config, store = Config(), Store(PROJECT)
    baseline = "f83489945b5448c1"
    source = store.revision_dir(baseline) / "rig"
    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        head = store.get().id
        revisions = sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
        hashes = {name: digest(source / name) for name in ["source.psd", "source.psd2live"]}
        with tempfile.TemporaryDirectory(prefix="identity-warp-control-") as tmp:
            work = Path(tmp)
            for name in hashes:
                shutil.copyfile(source / name, work / name)
            geometry = asyncio.run(run(config, work))
        assert store.get().id == head
        assert all(digest(source / name) == sha for name, sha in hashes.items())
        assert revisions == sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
    rows = []
    for form in [-1, 0, 1]:
        before = Image.open(OUT / f"baseline-form-{form}.png").convert("RGBA")
        after = Image.open(OUT / f"identity-only-form-{form}.png").convert("RGBA")
        reference = Image.open(PREVIOUS / "candidate" / f"full-open-form-{form}.png").convert("RGBA")
        old = Image.open(PREVIOUS / "baseline" / f"full-open-form-{form}.png").convert("RGBA")
        difference = ImageChops.difference(before, after)
        mesh_differences = {}
        for mesh in MESHES:
            a = geometry[f"baseline/form-{form}/{mesh}"]["points"]
            b = geometry[f"identity-only/form-{form}/{mesh}"]["points"]
            assert [p[0] for p in a] == [p[0] for p in b]
            distances = [math.dist(x[1:], y[1:]) for x, y in zip(a, b)]
            mesh_differences[mesh] = {"max_canvas_distance": max(distances), "changed_vertices": sum(d > 0 for d in distances)}
        rows.append({"form": form, "baseline_reproduced": before.tobytes() == old.tobytes(),
                     "identity_only_equals_candidate": after.tobytes() == reference.tobytes(),
                     "changed_pixels": sum(p != (0, 0, 0, 0) for p in difference.get_flattened_data()),
                     "max_channel_change": max(pair[1] for pair in difference.getextrema()),
                     "meshes": mesh_differences})
    report = {"baseline": baseline, "candidate_reference": "03e76af8caa54714", "rows": rows,
              "source_unchanged": True, "head": head, "head_unchanged": True, "no_new_revision": True,
              "scope": "Three frontal full-open poses; actual temporary identity-warp control, not model acceptance."}
    write_json(OUT / "result.json", report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
