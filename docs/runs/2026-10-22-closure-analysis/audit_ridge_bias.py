"""Deterministic centreline controls with known geometry and different profiles.

Does not change the frozen candidate gate or any production image. It tests the
measurement assumption that an argmax ridge represents a stroke's centre.
"""

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw
from live2d_pipeline.mouth_closure import Curve, dark_ridge

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "projects/mio-starlibrary-20261015/production/independent-closure-analysis-20261022/ridge-controls"


def frozen_dark_ridge(image, viewport, threshold=.2):
    """Preserved v1 instrument used by the rejected candidate's frozen gate."""
    left, top, width, height = viewport
    image = image.convert("RGBA")
    sx, sy = image.width / width, image.height / height
    columns = []
    for x in range(image.width):
        scores = []
        for y in range(image.height):
            r, g, b, a = image.getpixel((x, y))
            scores.append((a / 255) * (1 - (.2126 * r + .7152 * g + .0722 * b) / 255))
        columns.append((max(scores), scores))
    peak_all = max(peak for peak, _ in columns)
    if peak_all < .01:
        raise ValueError("No contour")
    points = []
    for x, (peak, scores) in enumerate(columns):
        if peak < threshold * peak_all:
            continue
        y = max(range(image.height), key=scores.__getitem__)
        rows = range(max(0, y - 1), min(image.height, y + 2))
        total = sum(scores[i] for i in rows)
        yy = sum((i + .5) * scores[i] for i in rows) / total
        points.append(((x + .5) / sx + left, yy / sy + top))
    return Curve(tuple(points))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    scale, centre = 4, 20
    records, images = [], []
    profiles = [("gaussian", .35)] + [("plateau", width) for width in [.75, 1, 1.5, 2, 3]]
    for profile, width in profiles:
        image = Image.new("RGBA", (320, 160))
        for x in range(80, 240):
            for y in range(image.height):
                distance = abs((y + .5) / scale - centre)
                opacity = math.exp(-.5 * (distance / width) ** 2) if profile == "gaussian" else float(distance <= width / 2)
                image.putpixel((x, y), (90, 30, 20, round(255 * opacity)))
        name = f"{profile}-{width:g}"
        image.save(OUT / f"{name}.png")
        measured = dark_ridge(image, (0, 0, 80, 40))
        frozen_y = frozen_dark_ridge(image, (0, 0, 80, 40)).sample(.5)[1]
        observed = measured.sample(.5)[1]
        records.append({"profile": profile, "profile_width_or_sigma_canvas_px": width,
                        "true_centre_y": centre, "measured_y": observed, "bias_canvas_px": observed - centre,
                        "frozen_v1_y": frozen_y, "frozen_v1_bias_canvas_px": frozen_y - centre,
                        "support_width": measured.width})
        images.append((name, image))
    sheet = Image.new("RGB", (640, 3 * 190), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (name, image) in enumerate(images):
        bg = Image.new("RGBA", image.size, "white")
        bg.alpha_composite(image)
        x, y = (index % 2) * 320, (index // 2) * 190
        sheet.paste(bg.convert("RGB"), (x, y + 25))
        draw.text((x + 5, y + 5), f"{name}: {records[index]['frozen_v1_bias_canvas_px']:.3f} -> {records[index]['bias_canvas_px']:.3f}px", fill="black")
        # Outside the measured stroke: mark the exact shared geometric centre.
        draw.line((x + 60, y + 105, x + 75, y + 105), fill="red")
        draw.line((x + 245, y + 105, x + 260, y + 105), fill="red")
    sheet.save(OUT / "controls.png")
    result = {"ground_truth": "All strokes have the same horizontal centreline y=20 and identical x support.",
              "method": "Frozen v1 argmax +/-1 compared with plateau-aware v2 on the same 4x controls.",
              "records": records,
              "scope": "Profile-dependent measurement bias; not a revised candidate gate or acceptance result."}
    (OUT / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
