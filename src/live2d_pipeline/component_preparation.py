"""Explicit aspect-preserving component placement, without inference or recropping.

This removes preparation distortion; it cannot restore generated identity details.
Canvas anchors use image-edge coordinates. Integer placement error is reported.
"""

import math

from PIL import Image


def prepare_uniform(image, crop, canvas_anchor, *, width=None, height=None, source_anchor=(0.5, 0.5)):
    if (width is None) == (height is None):
        raise ValueError("Specify exactly one target dimension; stretching is not implicit")
    dimension = width if width is not None else height
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1:
        raise ValueError("Target dimension must be a positive integer")
    if len(crop) != 4 or any(not isinstance(v, int) or isinstance(v, bool) for v in crop):
        raise ValueError("Crop requires four integer image-edge coordinates")
    x0, y0, x1, y1 = crop
    if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
        raise ValueError("Crop must be nonempty and contained in the source")
    if len(canvas_anchor) != 2 or any(not math.isfinite(v) for v in canvas_anchor):
        raise ValueError("Canvas anchor must contain two finite coordinates")
    if len(source_anchor) != 2 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in source_anchor):
        raise ValueError("Source anchor must be a finite normalized crop coordinate")
    native = image.convert("RGBA").crop(crop)
    if native.getchannel("A").getbbox() is None:
        raise ValueError("Crop has no visible alpha")
    scale = dimension / (native.width if width is not None else native.height)
    size = (dimension, max(1, round(native.height * scale))) if width is not None else (
        max(1, round(native.width * scale)), dimension,
    )
    if size[0] * size[1] > 24_000_000:
        raise ValueError("Prepared component exceeds 24 million pixels")
    # Premultiply before filtering so transparent RGB cannot bleed into edges.
    output = native.copy() if size == native.size else native.convert("RGBa").resize(
        size, Image.Resampling.LANCZOS,
    ).convert("RGBA")
    if output.getchannel("A").getbbox() is None:
        raise ValueError("Resampling removed all visible alpha; inspect the requested scale")
    origin = tuple(round(canvas_anchor[i] - source_anchor[i] * size[i]) for i in range(2))
    actual_anchor = tuple(origin[i] + source_anchor[i] * size[i] for i in range(2))
    scales = (size[0] / native.width, size[1] / native.height)
    receipt = {
        "crop": list(crop), "native_size": list(native.size), "output_size": list(size),
        "requested_uniform_scale": scale, "actual_scale_x": scales[0], "actual_scale_y": scales[1],
        "aspect_ratio_factor_after_integer_rounding": scales[0] / scales[1],
        "source_anchor_normalized": list(source_anchor), "requested_canvas_anchor": list(canvas_anchor),
        "actual_canvas_anchor": list(actual_anchor),
        "anchor_rounding_error": [a - b for a, b in zip(actual_anchor, canvas_anchor)],
        "placement": [*origin, *size],
        "resampling": "none" if size == native.size else "LANCZOS in premultiplied alpha, once",
        "source_alpha_bbox": native.getchannel("A").getbbox(),
        "output_alpha_bbox": output.getchannel("A").getbbox(),
        "identity_acceptance": "not_evaluated",
    }
    return output, receipt
