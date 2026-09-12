"""Explicit SAM2 inference for generated mouth layers; runs in the Comfy environment."""

import argparse
import json
import math
from pathlib import Path


def valid_candidate(bounds, score, box):
    if not bounds or not math.isfinite(score) or score < 0.7:
        return False
    # A SAM prompt box guides segmentation; it is not an exact crop boundary.
    # Allow a small lip overhang, while still rejecting the surrounding face.
    dx, dy = (box[2] - box[0]) * 0.1, (box[3] - box[1]) * 0.1
    return (
        bounds[0] >= box[0] - dx
        and bounds[1] >= box[1] - dy
        and bounds[2] <= box[2] + dx
        and bounds[3] <= box[3] + dy
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()
    import numpy as np
    import torch
    from PIL import Image, ImageOps
    from scipy import ndimage
    from transformers import Sam2Model, Sam2Processor

    source = Image.open(args.input).convert("RGB")
    w, h = source.size
    # Generated mouths are requested at the centre. Exclude adjacent cheeks/nose/chin.
    points = [
        [
            [
                [w * 0.5, h * 0.5],
                [w * 0.1, h * 0.5],
                [w * 0.9, h * 0.5],
                [w * 0.5, h * 0.15],
                [w * 0.5, h * 0.85],
            ]
        ]
    ]
    box = [w * 0.25, h * 0.35, w * 0.75, h * 0.65]
    model = Sam2Model.from_pretrained(args.model, local_files_only=True).to("cuda").eval()
    processor = Sam2Processor.from_pretrained(args.model, local_files_only=True)
    inputs = processor(
        images=source,
        input_points=points,
        input_labels=[[[1, 0, 0, 0, 0]]],
        input_boxes=[[box]],
        return_tensors="pt",
    ).to("cuda")
    with torch.inference_mode():
        result = model(**inputs, multimask_output=True)
    masks = processor.post_process_masks(result.pred_masks.cpu(), inputs["original_sizes"].cpu())[0][0]
    records, candidates = [], []
    for index, (mask, score) in enumerate(zip(masks.numpy(), result.iou_scores[0, 0].tolist())):
        labels, _ = ndimage.label(mask)
        label = labels[h // 2, w // 2]
        component = ndimage.binary_fill_holes(labels == label) if label else np.zeros_like(mask)
        alpha = Image.fromarray(component.astype("uint8") * 255)
        alpha.save(args.output.with_name(args.output.stem + f"-mask-{index}.png"))
        bounds = alpha.getbbox()
        valid = valid_candidate(bounds, score, box)
        records.append({"index": index, "score": score, "bounds": bounds, "valid": valid})
        if valid:
            candidates.append((score, index, alpha))
    report = {"candidates": records, "prompt_box": box, "selected": None}
    report_path = args.output.with_suffix(".segmentation.json")
    if not candidates:
        report_path.write_text(json.dumps(report, indent=2))
        raise RuntimeError("No isolated central mouth mask; inspect raw generation and masks")
    _, index, alpha = max(candidates, key=lambda value: value[0])
    report["selected"] = index
    rgba = source.convert("RGBA")
    rgba.putalpha(alpha)
    cropped = rgba.crop(alpha.getbbox())
    fitted = ImageOps.contain(cropped, (args.width, args.height), Image.Resampling.LANCZOS)
    output = Image.new("RGBA", (args.width, args.height))
    output.alpha_composite(fitted, ((args.width - fitted.width) // 2, (args.height - fitted.height) // 2))
    output.save(args.output)
    report_path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
