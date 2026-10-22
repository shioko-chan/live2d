"""Read-only artifact audit; writes diagnostic copies under supervisor-owned output."""

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "projects/mio-starlibrary-20261015"
OUT = PROJECT / "production/independent-supervision-20261022"
BASE = PROJECT / "revisions/358614b7f88146f5"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n")


def on_background(im, color):
    bg = Image.new("RGBA", im.size, color)
    return Image.alpha_composite(bg, im).convert("RGB")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((BASE / "materials/parts.json").read_text())
    if not (OUT / "baseline-hashes.json").exists():
        write("baseline-hashes.json", {
            "head": json.loads((PROJECT / "project.json").read_text()),
            "files": {str(p.relative_to(ROOT)): sha(p) for p in BASE.rglob("*") if p.is_file()},
        })
    rows = []
    composite = Image.new("RGBA", (manifest["width"], manifest["height"]))
    for part in sorted(manifest["parts"], key=lambda p: p["order"]):
        path = BASE / "materials" / part["image"]
        with Image.open(path) as opened:
            im = opened.convert("RGBA")
        alpha = im.getchannel("A")
        histogram = alpha.histogram()
        x, y, width, height = part["bounds"]
        # Neutral composition selects closed mouth, following the documented
        # state choice; open-mouth material is still audited independently.
        if part["semantic"] != "mouth_open":
            composite.alpha_composite(im, (x, y))
        rows.append({
            "id": part["id"], "semantic": part["semantic"], "side": part["side"],
            "order": part["order"], "bounds": part["bounds"], "mode": opened.mode,
            "size": list(im.size), "size_matches_bounds": im.size == (width, height),
            "in_canvas": x >= 0 and y >= 0 and x + width <= manifest["width"] and y + height <= manifest["height"],
            "nonempty": alpha.getbbox() is not None,
            "transparent_pixels": histogram[0], "partial_alpha_pixels": sum(histogram[1:255]),
            "opaque_pixels": histogram[255], "sha256": sha(path),
        })
    saved = Image.open(BASE / "materials/composite.png").convert("RGBA")
    difference = ImageChops.difference(saved, composite)
    write("materials-audit.json", {
        "canvas": [manifest["width"], manifest["height"]], "parts": rows,
        "unique_ids": len({r["id"] for r in rows}) == len(rows),
        "unique_orders": len({r["order"] for r in rows}) == len(rows),
        "reassembled_rgba_identical": all(channel.getbbox() is None for channel in difference.split()),
        "note": "Alpha counts/structure are diagnostics, not visual acceptance.",
    })
    for suffix, color in (("light", "#eeeeee"), ("dark", "#202020")):
        on_background(saved, color).save(OUT / f"composite-{suffix}.png")
    hair = Image.open(BASE / "materials/hair_back.png").convert("RGBA")
    # Diagnostic crops preserve actual pixels. No production pixels are changed.
    crops = [(0, 500, 300, 850), (700, 500, 1004, 850)]
    sheet = Image.new("RGB", (620, 730), "#808080")
    draw = ImageDraw.Draw(sheet)
    for row, color in enumerate(("#eeeeee", "#202020")):
        for col, box in enumerate(crops):
            crop = on_background(hair.crop(box), color)
            sheet.paste(crop, (col * 310, row * 365 + 15))
            draw.text((col * 310, row * 365), f"hair_back {box} / {color}", fill="black")
    sheet.save(OUT / "hair-alpha-crops.png")
    face_sheet = Image.new("RGB", (1000, 520), "#dddddd")
    source = Image.open(PROJECT / "production/artwork/00-reference.png").convert("RGBA")
    for index, (label, im) in enumerate((("Original reference", source), ("Assembled materials", saved))):
        face_sheet.paste(on_background(im.crop((270, 100, 770, 600)), "#dddddd"), (index * 500, 20))
        ImageDraw.Draw(face_sheet).text((index * 500 + 5, 3), label, fill="black")
    face_sheet.save(OUT / "identity-face-comparison.png")
    # Underlayers for manual review, all at native resolution on two backgrounds.
    for name in ("face_base", "lashes_left", "mouth_open", "hair_side_left"):
        im = Image.open(BASE / f"materials/{name}.png").convert("RGBA")
        sheet = Image.new("RGB", (im.width * 2, im.height + 20), "#888888")
        for i, color in enumerate(("#eeeeee", "#202020")):
            sheet.paste(on_background(im, color), (i * im.width, 20))
        ImageDraw.Draw(sheet).text((3, 3), name, fill="black")
        sheet.save(OUT / f"layer-{name}.png")
    print(json.dumps({"parts": len(rows), "output": str(OUT), "reassembled_equal": all(c.getbbox() is None for c in difference.split())}))


if __name__ == "__main__":
    main()
