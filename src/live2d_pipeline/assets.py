import hashlib
import colorsys
import re
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageStat
from psd_tools import PSDImage
from psd_tools.constants import Tag
from .schema import Part, Parts
from .store import read_json, write_json

SEMANTICS = {
    "back hair",
    "front hair",
    "headwear",
    "face",
    "facedetail",
    "irides",
    "eyebrow",
    "eyewhite",
    "eyelash",
    "eye_close",
    "eyewear",
    "ears",
    "earwear",
    "nose",
    "mouth",
    "mouth_open",
    "mouth_close",
    "tooth-t",
    "tooth-b",
    "tongue",
    "neck",
    "neckwear",
    "topwear",
    "handwear",
    "bottomwear",
    "legwear",
    "footwear",
    "tail",
    "wings",
    "objects",
}
ALIASES = {
    "前发": "front hair",
    "刘海": "front hair",
    "后发": "back hair",
    "脸": "face",
    "脸部": "face",
    "瞳孔": "irides",
    "iris": "irides",
    "eyeball": "irides",
    "眼白": "eyewhite",
    "睫毛": "eyelash",
    "眉毛": "eyebrow",
    "嘴": "mouth",
    "mouth open": "mouth_open",
    "mouth close": "mouth_close",
    "eye close": "eye_close",
    "身体": "topwear",
    "衣服": "topwear",
    "脖子": "neck",
    "body": "topwear",
    "face detail": "facedetail",
    "front_hair": "front hair",
    "back_hair": "back hair",
}


def semantic(name):
    name = name.lower().strip()
    for key in sorted(SEMANTICS | ALIASES.keys(), key=len, reverse=True):
        if key in name:
            return ALIASES.get(key, key)
    return "unknown"


def load_parts(directory):
    return Parts.model_validate(read_json(Path(directory) / "parts.json"))


def save_parts(directory, manifest):
    write_json(Path(directory) / "parts.json", manifest.model_dump())


def normalize(psd_path, directory):
    directory.mkdir(parents=True, exist_ok=True)
    psd = PSDImage.open(psd_path)
    if psd.depth != 8:
        raise ValueError("Only 8-bit layered PSDs supported")
    manifest = Parts(width=psd.width, height=psd.height, parts=[])
    counts = {}
    for layer in psd.descendants():
        if layer.is_group() or not layer.is_visible():
            continue
        im = layer.composite()
        if im is None:
            continue
        im = im.convert("RGBA")
        if im.getchannel("A").getbbox() is None:
            continue
        tag = semantic(layer.name)
        side = (
            "left"
            if re.search(r"\bleft\b|(?:^|[-_ ])l(?:$|[-_ ])|左", layer.name.lower())
            else (
                "right" if re.search(r"\bright\b|(?:^|[-_ ])r(?:$|[-_ ])|右", layer.name.lower()) else "none"
            )
        )
        key = tag.replace(" ", "_") + "_" + side
        counts[key] = counts.get(key, 0) + 1
        pid = f"{key}_{counts[key]}"
        im.save(directory / f"{pid}.png")
        manifest.parts.append(
            Part(
                id=pid,
                name=layer.name,
                semantic=tag,
                side=side,
                order=len(manifest.parts),
                bounds=(layer.left, layer.top, *im.size),
                image=f"{pid}.png",
            )
        )
    if len(manifest.parts) < 2:
        raise ValueError("Need at least two nonempty layers, not a flattened image")
    save_parts(directory, manifest)
    assemble(directory, directory / "composite.png")
    return manifest


def assemble(directory, output):
    manifest = load_parts(directory)
    canvas = Image.new("RGBA", (manifest.width, manifest.height))
    tags = {p.semantic for p in manifest.parts}
    for part in sorted(manifest.parts, key=lambda p: p.order):
        if part.semantic == "mouth_open" and "mouth_close" in tags:
            continue
        with Image.open(directory / part.image) as im:
            canvas.alpha_composite(im.convert("RGBA"), part.bounds[:2])
    canvas.save(output)


def write_psd(directory, output):
    manifest = load_parts(directory)
    psd = PSDImage.new("RGBA", (manifest.width, manifest.height))
    used_ids = set()
    for p in sorted(manifest.parts, key=lambda p: p.order):
        layer_id = int.from_bytes(hashlib.sha256(p.id.encode()).digest()[:4], "big") & 0x7FFFFFFF
        if layer_id in used_ids:
            raise ValueError("PSD stable layer ID collision")
        used_ids.add(layer_id)
        with Image.open(directory / p.image) as im:
            layer = psd.create_pixel_layer(
                im.convert("RGBA"),
                name=f"{p.semantic} {p.id}" + ({"left": "-l", "right": "-r"}.get(p.side, "")),
                left=p.bounds[0],
                top=p.bounds[1],
            )
            layer.tagged_blocks.set_data(Tag.LAYER_ID, layer_id)
    psd.save(output)


def preserve_outside(original, edited, mask):
    original = original.convert("RGBA")
    edited = edited.convert("RGBA").resize(original.size, Image.Resampling.LANCZOS)
    mask = mask.convert("L")
    if mask.size != original.size:
        raise ValueError("Mask must match original dimensions")
    result = Image.composite(edited, original, mask)
    result.putalpha(original.getchannel("A"))
    return result


def recolor(image, color):
    target = tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, target_value = colorsys.rgb_to_hsv(*target)
    result = image.convert("RGBA").copy()
    pixels = list(result.get_flattened_data())
    visible_values = sorted(max(r, g, b) / 255 for r, g, b, a in pixels if a > 127)
    reference = visible_values[len(visible_values) // 2] if visible_values else 1.0
    result.putdata(
        [
            (
                *[
                    round(c * 255)
                    for c in colorsys.hsv_to_rgb(
                        h,
                        s,
                        min(1.0, target_value * (max(r, g, b) / 255 / reference))
                        if reference > 0
                        else target_value,
                    )
                ],
                a,
            )
            for r, g, b, a in pixels
        ]
    )
    return result


def prepare_generated_face(directory, prompt):
    """Apply known generation intent and anatomical stacking to machine-generated layers only."""
    manifest = load_parts(directory)
    state = (
        "mouth_close"
        if "closed mouth" in prompt.lower()
        else ("mouth_open" if "open mouth" in prompt.lower() else None)
    )
    if state:
        for part in manifest.parts:
            if part.semantic == "mouth":
                part.semantic = state
        manifest.required_semantics = ["mouth_close", "mouth_open"]
    ranks = {
        "ears": 0,
        "face": 1,
        "eyewhite": 2,
        "irides": 3,
        "eyelash": 4,
        "eyebrow": 5,
        "nose": 5,
        "mouth": 6,
        "mouth_open": 6,
        "mouth_close": 6,
        "front hair": 7,
    }
    facial = [p for p in manifest.parts if p.semantic in ranks]
    slots = sorted(p.order for p in facial)
    for order, part in zip(slots, sorted(facial, key=lambda p: (ranks[p.semantic], p.order))):
        part.order = order
    save_parts(directory, manifest)
    assemble(directory, Path(directory) / "composite.png")


def filter_unsupported_parts(directory, original_source, aligned_source):
    """Reject mostly invented silhouettes on the supported white-background input path.

    Use See-through's saved aligned input, not a guessed resize. Interior white clothing
    stays foreground because only near-white regions connected to the corners are removed.
    Raw PSDs remain intact; authored PSD inputs never pass through this filter.
    """
    report = {"status": "not_applicable", "support": {}, "removed": []}
    with Image.open(original_source) as source:
        rgb = Image.alpha_composite(Image.new("RGBA", source.size, "white"), source.convert("RGBA"))
        corners = [
            (0, 0),
            (0, source.height - 1),
            (source.width - 1, 0),
            (source.width - 1, source.height - 1),
        ]
        white_background = all(min(rgb.getpixel(point)[:3]) >= 243 for point in corners)
    if white_background:
        manifest = load_parts(directory)
        with Image.open(aligned_source) as source:
            if source.size != (manifest.width, manifest.height):
                raise ValueError("Aligned source and layers have different canvas dimensions")
            rgb = Image.alpha_composite(
                Image.new("RGBA", source.size, "white"), source.convert("RGBA")
            ).convert("RGB")
        channels = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white")).split()
        mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        mask = mask.point(lambda value: 0 if value <= 12 else 255)
        for point in [(0, 0), (0, mask.height - 1), (mask.width - 1, 0), (mask.width - 1, mask.height - 1)]:
            if mask.getpixel(point) == 0:
                ImageDraw.floodfill(mask, point, 128)
        mask = mask.point(lambda value: 0 if value == 128 else 255)
        coverage = ImageStat.Stat(mask).mean[0] / 255
        if 0.01 < coverage < 0.95:
            report["status"] = "checked_white_background"
            for part in list(manifest.parts):
                x, y, w, h = part.bounds
                with Image.open(directory / part.image) as image:
                    alpha = image.convert("RGBA").getchannel("A")
                overlap = ImageChops.multiply(alpha, mask.crop((x, y, x + w, y + h)))
                support = ImageStat.Stat(overlap).sum[0] / max(1, ImageStat.Stat(alpha).sum[0])
                report["support"][part.id] = support
                if support < 0.2:
                    report["removed"].append(part.id)
                    manifest.parts.remove(part)
                    (directory / part.image).unlink()
            save_parts(directory, manifest)
            assemble(directory, directory / "composite.png")
    write_json(directory / "source-support.json", report)
    return report


def quality(directory):
    manifest = load_parts(directory)
    tags = {p.semantic for p in manifest.parts}
    missing = ({"face", "irides", "eyewhite", "eyelash", "eyebrow"} | set(manifest.required_semantics)) - tags
    if not tags & {"mouth", "mouth_open"}:
        missing.add("mouth_open")
    errors = []
    for p in manifest.parts:
        with Image.open(directory / p.image) as im:
            if im.size != tuple(p.bounds[2:]):
                errors.append(f"{p.id}: image/placement dimensions differ")
            if im.convert("RGBA").getchannel("A").getbbox() is None:
                errors.append(f"{p.id}: empty alpha")
    return {
        "missing_parts": sorted(missing),
        "errors": errors,
        "unknown_parts": [p.id for p in manifest.parts if p.semantic == "unknown"],
        "visual_quality": "not_evaluated",
    }


def unchanged_pixels(original, changed, mask):
    diff = ImageChops.difference(original.convert("RGBA"), changed.convert("RGBA"))
    outside = ImageChops.invert(mask.convert("L"))
    return all(ImageChops.multiply(channel, outside).getbbox() is None for channel in diff.split())
