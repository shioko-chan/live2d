"""Fixed per-pose temporary representation ablation; no new model revision.

Use the frozen field and source curve from the shared-warp experiment. Change
only its representation, at four declared poses, then retain all measurements.
This is not a complete keyform-grid implementation or an acceptance certificate.
"""

import asyncio
from base64 import b64decode
import math
from pathlib import Path
import shutil
import tempfile

from PIL import Image
from live2d_pipeline.config import Config
from live2d_pipeline.mouth_closure import Curve, dark_ridge, dynamic_displacement, ribbon_curve, static_displacement
from live2d_pipeline.rig import open_rig
from live2d_pipeline.schema import Operation
from live2d_pipeline.store import Store, digest, lock, read_json, write_json

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "projects/mio-starlibrary-20261015"
INPUT = PROJECT / "production/common-closure-20261023"
OUT = PROJECT / "production/independent-closure-analysis-20261022/direct-representation-control"
IDS = {"closed": "ArtMeshMouthClose", "upper": "ArtMeshMouthOpen_lip_0",
       "lower": "ArtMeshMouthOpen_lip_1", "cavity": "ArtMeshMouthOpen"}
CASES = [("original-failure", -1, .05), ("fade-boundary", 0, .1),
         ("middle-form-holdout", .5, .05), ("full-open-control", 0, 1)]


def prepare():
    objects, geometry = read_json(INPUT / "objects.json"), read_json(INPUT / "neutral-geometry.json")
    inputs = read_json(INPUT / "curve-inputs.json")
    assert all(digest(INPUT / path) == sha for path, sha in inputs["source_hashes"].items())
    assert all(objects[role]["parentId"] == "DeformMouthShapeBoth" for role in IDS)
    rests = {}

    def delta(role, form, opening):
        cells = objects[role]["geometry"]["cells"]
        keys = objects[role]["geometry"]["axes"][1]["keys"]
        low, high = max(k for k in keys if k <= opening), min(k for k in keys if k >= opening)
        def at(value):
            return next(c["positionDeltas"] for c in cells if c["coordinate"] == {
                "ParamMouthForm": form, "ParamMouthOpenY": value,
            })
        a, b = at(low), at(high)
        ratio = 0 if low == high else (opening - low) / (high - low)
        return [x + (y - x) * ratio for x, y in zip(a, b)]

    for role in ["upper", "lower", "cavity"]:
        d0 = delta(role, 0, 0)
        rests[role] = [(p[1] - d0[2 * i], p[2] - d0[2 * i + 1])
                       for i, p in enumerate(geometry[role + "_local"]["points"])]
    def points(role, form, opening):
        ds = delta(role, form, opening)
        return [(p[0] + ds[2 * i], p[1] + ds[2 * i + 1]) for i, p in enumerate(rests[role])]
    painted = Curve(tuple(map(tuple, inputs["painted_C0"])))
    reference = Curve(tuple(map(tuple, inputs["generated_G0"])))
    paired = inputs["paired_vertices"]
    plans = {}
    for name, form, opening in CASES:
        ops = []
        if form != 0:
            form_curve = ribbon_curve(points("upper", form, 0), paired)
            ds = []
            for point in geometry["closed_local"]["points"]:
                ds.extend(static_displacement(reference, form_curve, painted.coordinate(point[1])))
            ops.append(Operation(kind="rig", tool="keyform_set", arguments={
                "target": {"kind": "mesh", "id": IDS["closed"]}, "coordinate": {"ParamMouthForm": form},
                "geometry": {"position_deltas": ds},
            }))
        # Full opening is a literal no-edit control, not an identity transform.
        if opening < 1:
            top = points("upper", form, opening)
            xs = [(top[i][0] + top[i + 1][0]) / 2 for i in range(0, paired, 2)]
            bounds = min(xs), max(xs)
            for role in ["upper", "lower", "cavity"]:
                original = delta(role, form, opening)
                ds = []
                for i, point in enumerate(points(role, form, opening)):
                    correction = dynamic_displacement(painted, reference, bounds, point, opening)
                    ds.extend([original[2 * i] + correction[0], original[2 * i + 1] + correction[1]])
                ops.append(Operation(kind="rig", tool="keyform_set", arguments={
                    "target": {"kind": "mesh", "id": IDS[role]},
                    "coordinate": {"ParamMouthForm": form, "ParamMouthOpenY": opening},
                    "geometry": {"position_deltas": ds},
                }))
        plans[name] = ops
    return plans


async def render_case(config, work, name, form, opening, operations):
    dest = OUT / name
    dest.mkdir()
    write_json(dest / "operations.json", [op.model_dump() for op in operations])
    async with open_rig(config, work / "source.psd", work / "source.psd2live", work / "control", dest / "rig.log") as (rig, _):
        defaults = {p["id"]: p["default"] for p in (await rig.call("project_list_parameters"))["parameters"]}
        pose = defaults | {"ParamMouthForm": form, "ParamMouthOpenY": opening}
        async def render(mode, layer=None):
            roi = layer is not None
            request = {"parameters": pose, "viewport": {"mode": "canvas_rect", "left": 464 if roi else 0,
                       "top": 448 if roi else 0, "width": 120 if roi else 1024, "height": 80 if roi else 1280},
                       "background": "transparent", "target_long_edge": 480 if roi else 1280}
            if layer:
                request["include_layer_ids"] = [layer]
            result = await rig.session.call_tool("view_render_model", request)
            assert not result.isError, str(result)
            (dest / (mode + ".png")).write_bytes(b64decode(next(i.data for i in result.content if i.type == "image")))
            write_json(dest / (mode + ".json"), request)
        await render("baseline")
        if operations:
            await rig.apply(operations)
        await render("direct")
        if opening < 1:
            opacity = [(IDS["closed"], .1), (IDS["closed"], 1), (IDS["upper"], 0), (IDS["lower"], 0)]
            await rig.apply([Operation(kind="rig", tool="keyform_set", arguments={
                "target": {"kind": "mesh", "id": mesh}, "coordinate": {"ParamMouthOpenY": value},
                "channels": {"opacity": 1},
            }) for mesh, value in opacity])
            layers = read_json(INPUT / "layer-mapping.json")
            for role in ["closed", "upper", "lower"]:
                await render(role, layers[role])
    print(f"Completed fixed control {name}", flush=True)


def main():
    OUT.mkdir(parents=True, exist_ok=False)
    config, store = Config(), Store(PROJECT)
    plans = prepare()
    source = store.revision_dir("f83489945b5448c1") / "rig"
    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        head = store.get().id
        revisions = sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
        hashes = {name: digest(source / name) for name in ["source.psd", "source.psd2live"]}
        for name, form, opening in CASES:
            with tempfile.TemporaryDirectory(prefix="direct-field-control-") as tmp:
                work = Path(tmp)
                for filename in hashes:
                    shutil.copyfile(source / filename, work / filename)
                asyncio.run(render_case(config, work, name, form, opening, plans[name]))
        assert store.get().id == head and all(digest(source / name) == sha for name, sha in hashes.items())
        assert revisions == sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
    records = []
    for name, form, opening in CASES:
        record = {"case": name, "form": form, "opening": opening}
        if opening == 1:
            a = Image.open(OUT / name / "baseline.png").convert("RGBA")
            b = Image.open(OUT / name / "direct.png").convert("RGBA")
            record["zero_edit_pixel_identical"] = a.tobytes() == b.tobytes()
        else:
            try:
                curves = [dark_ridge(Image.open(OUT / name / (role + ".png")), (464, 448, 120, 80))
                          for role in ["closed", "upper", "lower"]]
                closed, upper, lower = curves
                distances = []
                for i in range(21):
                    u = i / 20
                    a, b, c = (curve.sample(u) for curve in curves)
                    distances.append(math.dist(a, ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)))
                ratio = (upper.width + lower.width) / 2 / closed.width
                record.update({"measurable": True, "max_distance": max(distances), "width_ratio": ratio,
                               "within_diagnostic_limits": max(distances) <= 1 and abs(ratio - 1) <= .1})
            except ValueError as error:
                record.update({"measurable": False, "error": str(error)})
        records.append(record)
    report = {"records": records, "instrument": "ridge-plateau-v2", "head": head,
              "head_unchanged": True, "source_unchanged": True, "no_new_revision": True,
              "scope": "Four predeclared independent temporary pose controls; no full-grid/transition or production acceptance."}
    write_json(OUT / "result.json", report)
    print(report)


if __name__ == "__main__":
    main()
