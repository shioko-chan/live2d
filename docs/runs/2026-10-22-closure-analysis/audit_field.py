"""Compare actual canvas vertices with the prescribed common-curve correction."""

import json
import math
from pathlib import Path

from live2d_pipeline.mouth_closure import Curve, Frame, dynamic_displacement

ROOT = Path(__file__).resolve().parents[3]
PRODUCTION = ROOT / "projects/mio-starlibrary-20261015/production"
OUT = PRODUCTION / "independent-closure-analysis-20261022"


def main():
    inputs = json.loads((PRODUCTION / "common-closure-20261023/curve-inputs.json").read_text())
    frame = Frame(**inputs["frame"])
    c0 = Curve(tuple(map(tuple, inputs["painted_C0"])))
    g0 = Curve(tuple(map(tuple, inputs["generated_G0"])))
    base = json.loads((OUT / "candidate-probe/baseline/geometry.json").read_text())
    candidate = json.loads((OUT / "candidate-probe/candidate/geometry.json").read_text())
    paired = inputs["paired_vertices"]
    records = []
    for pose, opening in [("neutral", 0), ("known-failure", .05)]:
        for mesh in ["ArtMeshMouthOpen_lip_0", "ArtMeshMouthOpen_lip_1"]:
            key = pose + "/" + mesh
            old = [tuple(point[1:]) for point in base[key]["points"]]
            actual = [tuple(point[1:]) for point in candidate[key]["points"]]
            local = [frame.local(point) for point in old]
            # At a nonzero opening the crossed tip paths need not form a single
            # closed curve. Only horizontal bounds are needed by this field.
            xs = [(local[i][0] + local[i + 1][0]) / 2 for i in range(0, paired, 2)]
            bounds = min(xs), max(xs)
            predicted = []
            for point in local:
                delta = dynamic_displacement(c0, g0, bounds, point, opening)
                predicted.append(frame.canvas(tuple(a + b for a, b in zip(point, delta))))
            assert len(predicted) == len(actual)
            errors = [math.dist(a, b) for a, b in zip(predicted, actual)]
            ordered = sorted(errors)
            worst = max(range(len(errors)), key=errors.__getitem__)
            records.append({
                "pose": pose, "mesh": mesh, "count": len(errors),
                "max_canvas_error_actual_vs_exact_shared_field": max(errors),
                "p95_canvas_error": ordered[int(.95 * (len(ordered) - 1))],
                "rms_canvas_error": math.sqrt(sum(e * e for e in errors) / len(errors)),
                "worst_vertex_index": worst, "worst_exact": predicted[worst], "worst_actual": actual[worst],
                "max_paired_vertices_error": max(errors[:paired]),
                "scope": "Neutral isolates spatial representation; nonzero opening also includes parameter interpolation.",
            })
    result = {
        "revision": "03e76af8caa54714",
        "method": "Evaluate the prescribed analytic field on actual baseline vertices in the measured parent frame, then compare with actual candidate canvas vertices. No model edits.",
        "records": records,
    }
    (OUT / "field-representation-error.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
