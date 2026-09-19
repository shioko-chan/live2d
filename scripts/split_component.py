"""Isolated SAM2/LaMa workers; selections use source-image pixel coordinates."""

import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter


def segment(source, work, model_dir):
    import torch
    from transformers import Sam2Model, Sam2Processor

    spec = json.loads((work / "selection.json").read_text())
    image = Image.open(source).convert("RGBA")
    rgb = Image.new("RGBA", image.size, "white")
    rgb.alpha_composite(image)
    points = spec["points"]
    if not points or not any(p[2] == 1 for p in points):
        raise ValueError("请至少标记一个保留点")
    model = Sam2Model.from_pretrained(model_dir, local_files_only=True).to("cuda").eval()
    processor = Sam2Processor.from_pretrained(model_dir, local_files_only=True)
    prompts = {
        "input_points": [[[[x, y] for x, y, _ in points]]],
        "input_labels": [[[label for _, _, label in points]]],
    }
    if spec.get("box"):
        x, y, w, h = spec["box"]
        prompts["input_boxes"] = [[[x, y, x + w, y + h]]]
    inputs = processor(images=rgb.convert("RGB"), **prompts, return_tensors="pt").to("cuda")
    identity = hashlib.sha256(source.read_bytes())
    for path in sorted(Path(model_dir).glob("*")):
        if path.is_file():
            st = path.stat()
            identity.update(f"{path.name}:{st.st_size}:{st.st_mtime_ns}".encode())
    cache = work.parent.parent / "embeddings"
    cache.mkdir(exist_ok=True)
    cached = cache / (identity.hexdigest() + ".pt")
    with torch.inference_mode():
        if cached.exists():
            embeddings = torch.load(cached, weights_only=True, map_location="cuda")
        else:
            embeddings = model.get_image_embeddings(inputs["pixel_values"])
            torch.save([v.cpu() for v in embeddings], cached.with_suffix(".tmp"))
            cached.with_suffix(".tmp").replace(cached)
        output = model(
            **{k: v for k, v in inputs.items() if k != "pixel_values"},
            image_embeddings=embeddings,
            multimask_output=True,
        )
    masks = processor.post_process_masks(output.pred_masks.cpu(), inputs["original_sizes"].cpu())[0][0]
    alpha = np.array(image.getchannel("A")) > 0
    report = []
    for index, (mask, score) in enumerate(zip(masks.numpy(), output.iou_scores[0, 0].tolist())):
        mask = mask & alpha
        name = f"mask-{index}.png"
        Image.fromarray(mask.astype("uint8") * 255).save(work / name)
        report.append({"mask": name, "score": score, "pixels": int(mask.sum())})
    (work / "segmentation.json").write_text(json.dumps(report, indent=2))


def inpaint(source, work):
    sys.path.insert(0, str(Path.cwd()))
    from annotators.lama_inpainter import apply_inpaint

    image = np.array(Image.open(source).convert("RGBA"))
    mask = np.array(Image.open(work / "mask.png")) > 0
    options = json.loads((work / "inpaint.json").read_text())
    padding = options["padding"]
    context_mask = Image.fromarray(mask.astype("uint8") * 255)
    if padding:
        context_mask = context_mask.filter(ImageFilter.MaxFilter(2 * padding + 1))
    painted = apply_inpaint(image[:, :, :3], np.array(context_mask))
    under = image.copy()
    under[mask, :3] = painted[mask]
    child = image.copy()
    child[~mask, 3] = 0
    Image.fromarray(under).save(work / "underpaint.png")
    Image.fromarray(child).save(work / "component.png")


if __name__ == "__main__":
    mode, source, work, *rest = sys.argv[1:]
    if mode == "segment":
        segment(Path(source), Path(work), rest[0])
    elif mode == "inpaint":
        inpaint(Path(source), Path(work))
    else:
        raise ValueError("Unknown component step")
