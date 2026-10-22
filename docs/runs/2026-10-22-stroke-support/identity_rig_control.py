"""Independent fresh-binder clipping assay for the two uniform iris materials.

This builds two disposable material copies. Historical facial repair keyforms are
not replayed into changed material coordinates. Results therefore describe fresh
binder clipping, not preservation of the authored production rig or acceptance.
"""

import asyncio
from base64 import b64decode
from pathlib import Path
import shutil

from PIL import Image, ImageChops
from live2d_pipeline import assets
from live2d_pipeline.config import Config
from live2d_pipeline.refinement import bind
from live2d_pipeline.rig import blink_operations, open_rig
from live2d_pipeline.store import Store, digest, lock, read_json, write_json

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "projects/mio-starlibrary-20261015"
PREP = PROJECT / "production/identity-prep-20261023"
OUT = PREP / "rig-control"
POSES = {
    "neutral": {}, "half": {"ParamEyeLOpen": .5, "ParamEyeROpen": .5},
    "closed": {"ParamEyeLOpen": 0, "ParamEyeROpen": 0},
    "wink-left": {"ParamEyeLOpen": 0}, "wink-right": {"ParamEyeROpen": 0},
    "gaze-left": {"ParamEyeBallX": -1}, "gaze-right": {"ParamEyeBallX": 1},
    "gaze-up": {"ParamEyeBallY": 1}, "gaze-down": {"ParamEyeBallY": -1},
    "turn-left": {"ParamAngleX": -14, "ParamAngleY": 8},
    "turn-right": {"ParamAngleX": 14, "ParamAngleY": -8},
    "turn-half-left": {"ParamAngleX": 14, "ParamAngleY": -8, "ParamEyeLOpen": .5},
    "turn-gaze-left": {"ParamAngleX": 14, "ParamAngleY": -8, "ParamEyeBallX": -1},
    "half-gaze-down": {"ParamEyeLOpen": .5, "ParamEyeROpen": .5, "ParamEyeBallY": -1},
}
EYE_ROI = (330, 280, 710, 455)


async def run(config, mode):
    work = OUT / mode
    assets.write_psd(work / "materials", work / "source.psd")
    async with open_rig(config, work / "source.psd", work / "source.psd2live", work / "control", work / "rig.log") as (rig, _):
        layers = (await rig.call("project_list_layers"))["layers"]
        await rig.apply(blink_operations(layers, await rig.objects()))
        write_json(work / "components.json", await bind(rig, assets.load_parts(work / "materials")))
        context = await rig.context()
        write_json(work / "context.json", context)
        defaults = {p["id"]: p["default"] for p in context["parameters"]["parameters"]}
        groups = {"irises": [p["id"] for p in layers if p.get("semanticTag") == "irides"],
                  "whites": [p["id"] for p in layers if p.get("semanticTag") == "eyewhite"]}
        assert all(len(group) == 2 for group in groups.values()), groups
        for name, changes in POSES.items():
            assert set(changes) <= set(defaults), changes
            pose = defaults | changes
            for view in ["actual", "irises", "whites"]:
                args = {"parameters": pose, "viewport": {"mode": "canvas_rect", "left": 0, "top": 0,
                        "width": 1024, "height": 1280}, "background": "transparent", "target_long_edge": 1280}
                if view != "actual":
                    args["include_layer_ids"] = groups[view]
                result = await rig.session.call_tool("view_render_model", args)
                assert not result.isError, str(result)
                dest = work / name
                dest.mkdir(exist_ok=True)
                (dest / (view + ".png")).write_bytes(b64decode(next(i.data for i in result.content if i.type == "image")))
                write_json(dest / (view + ".json"), args)
            print(mode + "/" + name, flush=True)


def compare():
    rows = []
    for name in POSES:
        row = {"pose": name}
        actual = {}
        for mode in ["baseline", "uniform"]:
            folder = OUT / mode / name
            iris = Image.open(folder / "irises.png").convert("RGBA").getchannel("A")
            whites = Image.open(folder / "whites.png").convert("RGBA").getchannel("A")
            row[mode + "_iris_pixels_outside_white_alpha_support"] = sum(a > 0 and b == 0 for a, b in zip(iris.get_flattened_data(), whites.get_flattened_data()))
            row[mode + "_iris_alpha_bbox"] = iris.getbbox()
            actual[mode] = Image.open(folder / "actual.png").convert("RGBA")
        diff = ImageChops.difference(actual["baseline"], actual["uniform"])
        row["changed_bbox"] = diff.getbbox(alpha_only=False)
        diff.paste((0, 0, 0, 0), EYE_ROI)
        row["changed_outside_eye_roi"] = sum(p != (0, 0, 0, 0) for p in diff.get_flattened_data())
        rows.append(row)
    return {"rows": rows, "eye_roi": EYE_ROI, "actual_renders": 84,
            "scope": "Fresh binder baseline versus uniform iris materials. No historical keyform replay, no formal model promotion, no Core/export/target-app claim."}


def main():
    config, store = Config(), Store(PROJECT)
    draft = read_json(PREP / "draft-id.json")
    batch = PROJECT / "material-batches" / draft["id"]
    info = read_json(batch / "draft.json")
    sources = {"baseline": batch / "base", "uniform": batch / info["result"]["preview"]["path"] / "materials"}
    # Declare fixed poses/count before acquiring the runtime, not after results.
    OUT.mkdir(exist_ok=False)
    write_json(OUT / "declared-scope.json", {"poses": POSES, "eye_roi": EYE_ROI, "render_count": 84,
               "baseline": "Fresh binder using draft base materials; authored rig is NOT the comparator"})
    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        state = read_json(PROJECT / "project.json")
        revisions = sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
        source_hashes = {str(p): digest(p) for folder in sources.values() for p in folder.rglob("*") if p.is_file()}
        for mode, material in sources.items():
            shutil.copytree(material, OUT / mode / "materials")
            asyncio.run(run(config, mode))
        assert read_json(PROJECT / "project.json") == state
        assert revisions == sorted(p.name for p in (PROJECT / "revisions").iterdir() if p.is_dir())
        assert all(digest(Path(p)) == sha for p, sha in source_hashes.items())
    report = compare() | {"formal_state_unchanged": True, "source_hashes_unchanged": True, "no_new_revision": True}
    write_json(OUT / "result.json", report)
    print(report)


if __name__ == "__main__":
    main()
