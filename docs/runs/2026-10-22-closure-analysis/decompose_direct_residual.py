"""Read-only vector decomposition of the fixed Form=0 / Open=.1 control.

All curves use their own normalized horizontal support, matching the diagnostic.
The mesh curve is predicted from authored deltas, not a claim about Core pixels.
"""

import json
import math
from pathlib import Path

from PIL import Image
from live2d_pipeline.mouth_closure import Curve, Frame, dark_ridge

ROOT = Path(__file__).resolve().parents[3]
PRODUCTION = ROOT / "projects/mio-starlibrary-20261015/production"
SOURCE = PRODUCTION / "common-closure-20261023"
OUT = PRODUCTION / "independent-closure-analysis-20261022/direct-representation-control"


def read(path):
    return json.loads(path.read_text())


def main():
    inputs = read(SOURCE / "curve-inputs.json")
    objects = read(SOURCE / "objects.json")
    neutral = read(SOURCE / "neutral-geometry.json")
    ops = read(OUT / "fade-boundary/operations.json")
    frame = Frame(**inputs["frame"])
    painted = Curve(tuple(frame.canvas(p) for p in inputs["painted_C0"]))
    roles = ["upper", "lower"]
    predicted = {}
    for role in roles:
        obj = objects[role]
        d0 = next(c["positionDeltas"] for c in obj["geometry"]["cells"] if c["coordinate"] == {
            "ParamMouthForm": 0, "ParamMouthOpenY": 0,
        })
        ds = next(op["arguments"]["geometry"]["position_deltas"] for op in ops
                  if op["arguments"]["target"]["id"] == "ArtMeshMouthOpen_lip_" + str(roles.index(role)))
        predicted[role] = [frame.canvas((p[1] - d0[2 * i] + ds[2 * i],
                                        p[2] - d0[2 * i + 1] + ds[2 * i + 1]))
                           for i, p in enumerate(neutral[role + "_local"]["points"])]
    # At nonzero opening the tip overlap is not a single-valued y(x).
    # Do not average away an ambiguous curve: only use its measured x support.
    centers = {role: [tuple((predicted[role][i][axis] + predicted[role][i + 1][axis]) / 2
                            for axis in [0, 1]) for i in range(0, inputs["paired_vertices"], 2)]
               for role in roles}
    support = {role: (min(p[0] for p in points), max(p[0] for p in points))
               for role, points in centers.items()}
    mean_support = tuple(sum(support[role][i] for role in roles) / 2 for i in [0, 1])
    measured = {role: dark_ridge(Image.open(OUT / "fade-boundary" / (role + ".png")),
                                (464, 448, 120, 80)) for role in ["closed", *roles]}
    rows = []
    for i in range(21):
        u = i / 20
        s = measured["closed"].sample(u)
        a, b = (measured[role].sample(u) for role in roles)
        m = tuple((x + y) / 2 for x, y in zip(a, b))
        c = painted.sample(u)
        subtract = lambda x, y: tuple(a - b for a, b in zip(x, y))  # noqa: E731
        terms = {"measured_ink_minus_frozen_C0": subtract(m, c),
                 "frozen_C0_minus_v2_static_ink": subtract(c, s)}
        total = subtract(m, s)
        assert math.dist(tuple(sum(v[axis] for v in terms.values()) for axis in [0, 1]), total) < 1e-10
        rows.append({"u": u, "total_vector": total, "total_distance": math.hypot(*total), **terms})
    endpoint_x = []
    for side, u in enumerate([0, 1]):
        x_static = measured["closed"].sample(u)[0]
        x_ink = sum(measured[role].sample(u)[0] for role in roles) / 2
        x_c0 = painted.sample(u)[0]
        endpoint_x.append({"u": u, "total_ink_minus_static_x": x_ink - x_static,
                           "ink_support_minus_predicted_ribbon_support_x": x_ink - mean_support[side],
                           "predicted_ribbon_support_minus_frozen_C0_x": mean_support[side] - x_c0,
                           "frozen_C0_minus_static_x": x_c0 - x_static})
    report = {"case": "Form 0, Open .1, frontal", "instrument": "ridge-plateau-v2",
              "worst": max(rows, key=lambda row: row["total_distance"]), "rows": rows,
              "endpoint_horizontal_decomposition": endpoint_x,
              "predicted_geometry_width": mean_support[1] - mean_support[0], "frozen_C0_width": painted.width,
              "v2_static_ink_width": measured["closed"].width,
              "v2_dynamic_ink_width": sum(measured[r].width for r in roles) / 2,
              "scope": "Algebraic same-u ink decomposition and predicted horizontal support only. The nonzero-open tip overlap is not a single-valued y(x); no fitted geometric centre curve is substituted. Ink-support terms combine caps, rasterization and unmeasured evaluation error, not cap-only or Core geometry."}
    (OUT / "residual-decomposition.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
