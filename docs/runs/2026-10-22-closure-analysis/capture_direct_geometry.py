"""Supplement the unchanged fade-boundary control with actual preview geometry.

No new correction or candidate. Replays saved operations in two temporary copies,
checks identical pixels, and measures baseline/direct ink and geometric support.
"""

import asyncio
from base64 import b64decode
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image
from live2d_pipeline.config import Config
from live2d_pipeline.mouth_closure import Frame, dark_ridge
from live2d_pipeline.rig import open_rig
from live2d_pipeline.schema import Operation
from live2d_pipeline.store import Store, digest, lock, read_json, write_json

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "projects/mio-starlibrary-20261015"
INPUT = PROJECT / "production/common-closure-20261023"
PREVIOUS = PROJECT / "production/independent-closure-analysis-20261022/direct-representation-control"
OUT = PREVIOUS / "actual-geometry"
IDS = {"closed": "ArtMeshMouthClose", "upper": "ArtMeshMouthOpen_lip_0",
       "lower": "ArtMeshMouthOpen_lip_1", "cavity": "ArtMeshMouthOpen"}


async def capture(config, work, mode):
    dest = OUT / mode
    dest.mkdir()
    async with open_rig(config, work / "source.psd", work / "source.psd2live", work / "capture", dest / "rig.log") as (rig, _):
        defaults = {p["id"]: p["default"] for p in (await rig.call("project_list_parameters"))["parameters"]}
        pose = defaults | {"ParamMouthForm": 0, "ParamMouthOpenY": .1}
        if mode == "direct":
            await rig.apply([Operation.model_validate(op) for op in read_json(PREVIOUS / "fade-boundary/operations.json")])
        geometry = {}
        for role, mesh in IDS.items():
            query = {"target": {"kind": "mesh", "id": mesh}, "coordinate": pose,
                     "space": "canvas", "detail": "points", "limit": 256}
            data = await rig.call("rig_inspect", query)
            while "nextOffset" in data:
                page = await rig.call("rig_inspect", query | {"offset": data.pop("nextOffset")})
                data["points"] += page["points"]
                if "nextOffset" in page:
                    data["nextOffset"] = page["nextOffset"]
            geometry[role] = data
        write_json(dest / "geometry.json", geometry)
        async def render(name, layer=None, shift=0):
            request = {"parameters": pose, "viewport": {"mode": "canvas_rect", "left": 464 + shift if layer else 0,
                       "top": 448 if layer else 0, "width": 120 if layer else 1024,
                       "height": 80 if layer else 1280}, "background": "transparent", "target_long_edge": 480 if layer else 1280}
            if layer:
                request["include_layer_ids"] = [layer]
            result = await rig.session.call_tool("view_render_model", request)
            assert not result.isError, str(result)
            (dest / (name + ".png")).write_bytes(b64decode(next(i.data for i in result.content if i.type == "image")))
            write_json(dest / (name + ".json"), request)
        await render("actual")
        assert Image.open(dest / "actual.png").convert("RGBA").tobytes() == Image.open(
            PREVIOUS / "fade-boundary" / (mode + ".png")).convert("RGBA").tobytes()
        opacity = [(IDS["closed"], .1), (IDS["closed"], 1), (IDS["upper"], 0), (IDS["lower"], 0)]
        await rig.apply([Operation(kind="rig", tool="keyform_set", arguments={
            "target": {"kind": "mesh", "id": mesh}, "coordinate": {"ParamMouthOpenY": value},
            "channels": {"opacity": 1},
        }) for mesh, value in opacity])
        layers = read_json(INPUT / "layer-mapping.json")
        for role in ["closed", "upper", "lower"]:
            await render(role, layers[role])
        if mode == "baseline":
            for shift in [-.25, -.125, .125, .25]:
                await render("phase-" + str(shift), layers["closed"], shift)
    print("Captured unchanged " + mode, flush=True)


def summarize():
    inputs, objects, neutral = [read_json(INPUT / name) for name in ["curve-inputs.json", "objects.json", "neutral-geometry.json"]]
    frame = Frame(**inputs["frame"])
    operations = read_json(PREVIOUS / "fade-boundary/operations.json")
    records = []
    for mode in ["baseline", "direct"]:
        geometry = read_json(OUT / mode / "geometry.json")
        for role in ["upper", "lower", "cavity"]:
            cells = objects[role]["geometry"]["cells"]
            def delta(opening):
                keys = objects[role]["geometry"]["axes"][1]["keys"]
                low, high = max(k for k in keys if k <= opening), min(k for k in keys if k >= opening)
                def at(value):
                    return next(c["positionDeltas"] for c in cells if c["coordinate"] == {
                        "ParamMouthForm": 0, "ParamMouthOpenY": value,
                    })
                ratio = 0 if low == high else (opening - low) / (high - low)
                return [x + (y - x) * ratio for x, y in zip(at(low), at(high))]
            d0 = delta(0)
            ds = delta(.1) if mode == "baseline" else next(op["arguments"]["geometry"]["position_deltas"] for op in operations
                if op["arguments"]["target"]["id"] == IDS[role])
            predicted = [frame.canvas((p[1] - d0[2 * i] + ds[2 * i], p[2] - d0[2 * i + 1] + ds[2 * i + 1]))
                         for i, p in enumerate(neutral[role + "_local"]["points"])]
            actual = [p[1:] for p in geometry[role]["points"]]
            assert len(actual) == len(predicted)
            record = {"mode": mode, "role": role, "max_actual_vs_predicted_canvas_error": max(math.dist(a, b) for a, b in zip(actual, predicted))}
            if role != "cavity":
                centers = [(actual[i][0] + actual[i + 1][0]) / 2 for i in range(0, inputs["paired_vertices"], 2)]
                support = [min(centers), max(centers)]
                outer = [min(p[0] for p in actual), max(p[0] for p in actual)]
                curve = dark_ridge(Image.open(OUT / mode / (role + ".png")), (464, 448, 120, 80))
                ink = [curve.sample(u)[0] for u in [0, 1]]
                record.update({"actual_geometric_x_support": support, "ink_x_support": ink,
                               "ink_minus_geometry_x": [x - y for x, y in zip(ink, support)],
                               "all_vertex_x_bounds": outer,
                               "outer_vertices_minus_ribbon_center_x": [x - y for x, y in zip(outer, support)],
                               "ink_minus_outer_vertices_x": [x - y for x, y in zip(ink, outer)]})
            records.append(record)
    phase = []
    static = dark_ridge(Image.open(OUT / "baseline/closed.png"), (464, 448, 120, 80))
    for shift in [-.25, -.125, .125, .25]:
        curve = dark_ridge(Image.open(OUT / "baseline" / ("phase-" + str(shift) + ".png")), (464 + shift, 448, 120, 80))
        phase.append({"viewport_phase_shift": shift,
                      "measured_world_endpoint_x_change": [curve.sample(u)[0] - static.sample(u)[0] for u in [0, 1]]})
    return {"records": records, "static_raster_phase_control": phase,
            "actual_pixels_reproduced": True, "scope": "Actual PSD2Live preview canvas vertices at F0/O.1, not official Core or full-grid acceptance. Viewport phase leaves world geometry unchanged; static phase sensitivity is not a universal error bound for other stroke profiles."}


def main():
    OUT.mkdir(exist_ok=False)
    config, store = Config(), Store(PROJECT)
    source = store.revision_dir("f83489945b5448c1") / "rig"
    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        head = store.get().id
        revisions = sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
        hashes = {name: digest(source / name) for name in ["source.psd", "source.psd2live"]}
        for mode in ["baseline", "direct"]:
            with tempfile.TemporaryDirectory(prefix="direct-geometry-evidence-") as tmp:
                work = Path(tmp)
                for name in hashes:
                    shutil.copyfile(source / name, work / name)
                asyncio.run(capture(config, work, mode))
        assert store.get().id == head and all(digest(source / name) == sha for name, sha in hashes.items())
        assert revisions == sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
    report = summarize() | {"source_unchanged": True, "head_unchanged": True, "no_new_revision": True}
    write_json(OUT / "result.json", report)
    print(report)


if __name__ == "__main__":
    main()
