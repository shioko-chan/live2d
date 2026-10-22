"""Measure source-to-prepared transforms; no production files are changed."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[3]
PRODUCTION = REPO / "projects/mio-starlibrary-20261015/production"
OUT = PRODUCTION / "independent-closure-analysis-20261022"


def main():
    OUT.mkdir(exist_ok=True)
    provenance = json.loads((PRODUCTION / "layout-provenance.json").read_text())
    results = []
    for row in provenance:
        original = Image.open(PRODUCTION / row["source"]).convert("RGBA")
        native = original.crop(row["crop"])
        target = tuple(row["output_size"])
        recreated = native if native.size == target else native.resize(target, Image.Resampling.LANCZOS)
        actual = Image.open(PRODUCTION / "prepared-v1" / (row["id"] + ".png")).convert("RGBA")
        width, height = native.size
        sx, sy = target[0] / width, target[1] / height
        # ±1 output pixel accounts for integer rounding. This is a mechanical
        # distortion flag, not an automatic verdict about character identity.
        width_locked_height = round(height * sx)
        height_locked_width = round(width * sy)
        results.append({
            "id": row["id"], "source": row["source"], "crop": row["crop"],
            "native_size": list(native.size), "recorded_native_matches": list(native.size) == row["native_size"],
            "output_size": list(target), "scale_x": sx, "scale_y": sy,
            "aspect_ratio_factor": sx / sy, "anisotropy": max(sx / sy, sy / sx),
            "width_locked_size": [target[0], width_locked_height],
            "height_locked_size": [height_locked_width, target[1]],
            "exceeds_one_pixel_rounding": abs(target[1] - width_locked_height) > 1 and abs(target[0] - height_locked_width) > 1,
            "reconstruction_pixel_identical": recreated.size == actual.size and recreated.tobytes() == actual.tobytes(),
        })
    report = {"scope": "Deterministic preprocessing attribution; no new generation, rig or material import.", "parts": results}
    (OUT / "preparation-analysis.json").write_text(json.dumps(report, indent=2) + "\n")
    iris = next(row for row in provenance if row["id"] == "iris_left")
    native = Image.open(PRODUCTION / iris["source"]).convert("RGBA").crop(iris["crop"])
    current = Image.open(PRODUCTION / "prepared-v1/iris_left.png").convert("RGBA")
    keep_width = native.resize((44, round(native.height * 44 / native.width)), Image.Resampling.LANCZOS)
    keep_height = native.resize((round(native.width * 43 / native.height), 43), Image.Resampling.LANCZOS)
    # These are visual controls for analysis only, never proposed replacement
    # materials or a claim that preserving aspect restores generated details.
    source = Image.open(PRODUCTION / "artwork/00-reference.png").convert("RGBA")
    panels = [
        ("Reference eye; literal crop", source.crop((390, 327, 487, 408))),
        ("Generated: uniform width 44", keep_width),
        ("Prepared: forced 44 x 43", current),
        ("Generated: uniform height 43", keep_height),
    ]
    sheet = Image.new("RGB", (4 * 330, 310), "#ddd")
    draw = ImageDraw.Draw(sheet)
    for index, (label, im) in enumerate(panels):
        bg = Image.new("RGBA", im.size, "#555555")
        bg.alpha_composite(im)
        shown = bg.convert("RGB").resize((im.width * 3, im.height * 3), Image.Resampling.NEAREST)
        sheet.paste(shown, (index * 330 + 5, 45))
        draw.text((index * 330 + 5, 5), label, fill="black")
        draw.text((index * 330 + 5, 22), "3x nearest-neighbour diagnostic", fill="black")
    sheet.save(OUT / "iris-source-preparation-controls.png")
    print(json.dumps({
        "parts": len(results), "reconstructed_exactly": sum(x["reconstruction_pixel_identical"] for x in results),
        "anisotropy_flags": sum(x["exceeds_one_pixel_rounding"] for x in results),
        "iris_left": next(x for x in results if x["id"] == "iris_left"),
    }, indent=2))


if __name__ == "__main__":
    main()
