"""Independent temporary-workspace renders; never changes a saved rig or head.

Requires the prepared Nix environment. The caller supplies the candidate revision.
Endpoint and eye preservation are pixel comparisons, not artistic acceptance.
"""

import argparse
import asyncio
from base64 import b64decode
from functools import reduce
from pathlib import Path
import shutil
import tempfile

from PIL import Image, ImageChops
from live2d_pipeline.config import Config
from live2d_pipeline.rig import open_rig
from live2d_pipeline.store import Store, digest, lock, write_json


def poses():
    result = {
        "neutral": {},
        "known-failure": {"ParamMouthOpenY": .05, "ParamMouthForm": -1},
        "form-negative-half": {"ParamMouthOpenY": .05, "ParamMouthForm": -.5},
        "form-positive-half": {"ParamMouthOpenY": .05, "ParamMouthForm": .5},
        "wink-left": {"ParamEyeLOpen": 0, "ParamEyeROpen": 1, "ParamAngleX": 14, "ParamAngleY": -8},
        "wink-right": {"ParamEyeLOpen": 1, "ParamEyeROpen": 0, "ParamAngleX": -14, "ParamAngleY": -8},
        "half-blink": {"ParamEyeLOpen": .5, "ParamEyeROpen": .5, "ParamAngleX": 14, "ParamAngleY": -8},
        "closed-blink": {"ParamEyeLOpen": 0, "ParamEyeROpen": 0},
    }
    for form in [-1, 0, 1]:
        result[f"full-open-form-{form}"] = {"ParamMouthOpenY": 1, "ParamMouthForm": form}
    for turn in [-14, 14]:
        result[f"failure-turn-{turn}"] = {
            "ParamMouthOpenY": .05, "ParamMouthForm": -1,
            "ParamAngleX": turn, "ParamAngleY": -8,
        }
    for x, y in [(-45, -30), (-45, 30), (45, -30), (45, 30)]:
        result[f"stress-x{x}-y{y}"] = {
            "ParamMouthOpenY": .05, "ParamMouthForm": -1,
            "ParamAngleX": x, "ParamAngleY": y,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("candidate")
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline", default="f83489945b5448c1")
    args = parser.parse_args()
    config, store = Config(), Store(args.project.resolve())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    cases = poses()

    async def render(revision, label, work):
        dest = output / label
        dest.mkdir()
        async with open_rig(
            config, work / "source.psd", work / "source.psd2live", work / "control", dest / "rig.log"
        ) as (rig, _):
            state = await rig.call("project_get_state")
            parameters = (await rig.call("project_list_parameters"))["parameters"]
            defaults = {p["id"]: p["default"] for p in parameters}
            write_json(dest / "parameters.json", parameters)
            frames = []
            for name, overrides in cases.items():
                assert set(overrides) <= set(defaults), "Unknown parameter in probe"
                request = {
                    "parameters": defaults | overrides,
                    "viewport": {"mode": "canvas_rect", "left": 0, "top": 0,
                                 "width": state["canvasWidth"], "height": state["canvasHeight"]},
                    "background": "transparent", "target_long_edge": 1280,
                }
                result = await rig.session.call_tool("view_render_model", request)
                assert not result.isError, str(result)
                (dest / f"{name}.png").write_bytes(b64decode(next(i.data for i in result.content if i.type == "image")))
                write_json(dest / f"{name}.json", request)
                frames.append({"time": len(frames) / 10, "parameters": request["parameters"]})
            write_json(dest / "trace.json", {"frames": frames, "scope": "Independent discrete pose probes, not an authored motion"})
            geometry = {}
            for pose_name in ["neutral", "known-failure"]:
                for mesh_id in ["ArtMeshMouthClose", "ArtMeshMouthOpen_lip_0", "ArtMeshMouthOpen_lip_1"]:
                    request = {"target": {"kind": "mesh", "id": mesh_id},
                               "coordinate": defaults | cases[pose_name], "space": "canvas",
                               "detail": "points", "limit": 256}
                    data = await rig.call("rig_inspect", request)
                    while "nextOffset" in data:
                        offset = data.pop("nextOffset")
                        page = await rig.call("rig_inspect", request | {"offset": offset})
                        data["points"] += page["points"]
                        if "nextOffset" in page:
                            data["nextOffset"] = page["nextOffset"]
                    geometry[f"{pose_name}/{mesh_id}"] = data
            write_json(dest / "geometry.json", geometry)
            print(f"Rendered {label}: {revision}, {len(cases)} poses", flush=True)

    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        head = store.get().id
        before = {}
        for label, revision in [("baseline", args.baseline), ("candidate", args.candidate)]:
            source = store.revision_dir(revision) / "rig"
            before[revision] = {name: digest(source / name) for name in ["source.psd", "source.psd2live"]}
            with tempfile.TemporaryDirectory(prefix="independent-closure-probe-") as tmp:
                work = Path(tmp)
                for name in before[revision]:
                    shutil.copyfile(source / name, work / name)
                asyncio.run(render(revision, label, work))
        unchanged = all(
            digest(store.revision_dir(revision) / "rig" / name) == value
            for revision, files in before.items() for name, value in files.items()
        )
        assert unchanged and store.get().id == head

    compared = []
    for name in cases:
        old = Image.open(output / "baseline" / f"{name}.png").convert("RGBA")
        new = Image.open(output / "candidate" / f"{name}.png").convert("RGBA")
        assert old.size == new.size
        difference = ImageChops.difference(old, new)
        bbox = reduce(ImageChops.lighter, difference.split()).getbbox()
        preserve = name in {"neutral", "wink-left", "wink-right", "half-blink", "closed-blink"} or name.startswith("full-open")
        compared.append({"pose": name, "pixel_identical": old.tobytes() == new.tobytes(),
                         "difference_bbox_all_channels": bbox, "must_preserve": preserve,
                         "stress_only": name.startswith("stress-")})
    report = {"candidate": args.candidate, "baseline": args.baseline,
              "head": head, "head_unchanged": True, "sources_unchanged": unchanged,
              "renders": len(cases) * 2, "comparisons": compared,
              "all_preservation_checks_pass": all(row["pixel_identical"] for row in compared if row["must_preserve"]),
              "scope": "Actual CPU pixels. Stress poses extend beyond the modest-turn brief. No target app or tracking test."}
    write_json(output / "result.json", report)
    print(report)


if __name__ == "__main__":
    main()
