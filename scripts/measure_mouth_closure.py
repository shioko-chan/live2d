"""Measure separately rendered mouth components with fixed shared-u correspondence.

Input directory contains closed.png, upper.png, lower.png, rendered at identical
viewport/scale and forced opacity 1 on disposable workspaces. Never use composite
peak counts as component correspondence. Thresholds are explicit, frozen inputs.
"""

import argparse
import json
from pathlib import Path
import math
from PIL import Image
from live2d_pipeline.mouth_closure import dark_ridge


def measure(directory, viewport, threshold=0.2):
    curves = {
        name: dark_ridge(Image.open(directory / (name + ".png")), viewport, threshold)
        for name in ["closed", "upper", "lower"]
    }
    closed, upper, lower = (curves[name] for name in ["closed", "upper", "lower"])
    samples = []
    for i in range(21):
        u = i / 20
        c, a, b = closed.sample(u), upper.sample(u), lower.sample(u)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        samples.append({"u": u, "closed": c, "upper": a, "lower": b, "midline_distance": math.dist(c, mid)})
    return {
        "measurement_version": "ridge-plateau-v2",
        "threshold": threshold,
        "viewport": viewport,
        "samples": samples,
        "component_widths": {name: curve.width for name, curve in curves.items()},
        "max_distance": max(sample["midline_distance"] for sample in samples),
        "width_ratio": (upper.width + lower.width) / 2 / closed.width,
        "scope": "Isolated visible stroke support, not anatomy or natural motion acceptance",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--viewport", type=float, nargs=4, required=True)
    parser.add_argument("--threshold", type=float, default=0.2)
    parser.add_argument("--max-distance", type=float, required=True)
    parser.add_argument("--max-width-error", type=float, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if any(not math.isfinite(v) or v <= 0 for v in [args.max_distance, args.max_width_error]):
        parser.error("Limits must be finite and positive")
    try:
        result = measure(args.directory, args.viewport, args.threshold)
    except ValueError as error:
        result = {
            "measurement_version": "ridge-plateau-v2", "measurable": False,
            "status": "unmeasurable", "passed": False, "error": str(error),
            "threshold": args.threshold, "viewport": args.viewport,
            "limits": {"max_distance": args.max_distance, "max_width_error": args.max_width_error},
        }
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        return 2
    result["measurable"] = True
    result["limits"] = {"max_distance": args.max_distance, "max_width_error": args.max_width_error}
    result["passed"] = (
        result["max_distance"] <= args.max_distance and abs(result["width_ratio"] - 1) <= args.max_width_error
    )
    result["status"] = "passed" if result["passed"] else "failed"
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
