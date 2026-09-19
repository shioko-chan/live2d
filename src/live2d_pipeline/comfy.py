import copy
import math
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import httpx
from PIL import Image, ImageStat
from .process import free_port, owned_process, wait_http
from .store import read_json, write_json


def bind_workflow(template, values):
    graph = copy.deepcopy(template["prompt"])
    for name, places in template["bindings"].items():
        if name not in values:
            raise ValueError(f"Missing workflow binding: {name}")
        for node, key in places:
            graph[node]["inputs"][key] = values[name]
    return graph


class ComfyClient:
    def __init__(self, url, timeout, client=None):
        self.client = client or httpx.Client(base_url=url, timeout=timeout, trust_env=False)
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout

    def request(self, method, url, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("ComfyUI inference time budget exhausted")
        return self.client.request(method, url, timeout=remaining, **kwargs)

    def upload(self, image):
        with Path(image).open("rb") as f:
            r = self.request(
                "POST",
                "/upload/image",
                files={"image": (uuid.uuid4().hex + ".png", f, "image/png")},
                data={"overwrite": "false", "type": "input"},
            )
        r.raise_for_status()
        value = r.json()
        return "/".join(x for x in [value.get("subfolder", ""), value["name"]] if x)

    def execute(self, graph, output, output_node):
        info = self.request("GET", "/object_info")
        info.raise_for_status()
        missing = {n["class_type"] for n in graph.values()} - info.json().keys()
        if missing:
            raise RuntimeError(f"Missing ComfyUI nodes: {sorted(missing)}")
        r = self.request("POST", "/prompt", json={"prompt": graph, "client_id": uuid.uuid4().hex})
        r.raise_for_status()
        body = r.json()
        if body.get("node_errors"):
            raise RuntimeError(str(body["node_errors"]))
        pid = body["prompt_id"]
        end = self.deadline
        while time.monotonic() < end:
            r = self.request("GET", f"/history/{pid}")
            r.raise_for_status()
            entry = r.json().get(pid)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    write_json(output.with_suffix(".error.json"), entry)
                    messages = [
                        event[1].get("exception_message", "Unknown execution error")
                        for event in status.get("messages", [])
                        if len(event) == 2 and event[0] == "execution_error"
                    ]
                    raise RuntimeError(
                        f"ComfyUI execution failed: {'; '.join(messages) or 'inspect error JSON'}"
                    )
                images = entry.get("outputs", {}).get(output_node, {}).get("images", [])
                if images:
                    data = self.request("GET", "/view", params=images[0])
                    data.raise_for_status()
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data.content)
                    with Image.open(output) as im:
                        im.verify()
                    return
                if status.get("completed"):
                    raise RuntimeError("ComfyUI completed without required image")
            time.sleep(min(0.5, max(0, end - time.monotonic())))
        raise TimeoutError(f"ComfyUI job {pid} timed out")


@contextmanager
def session(config, log, mounts=(), precision="float16", timeout=None):
    if precision not in {"float16", "float32"}:
        raise ValueError("Unsupported inference precision")
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("Inference timeout must be positive and finite")
    deadline = time.monotonic() + timeout if timeout is not None else None
    port = config["comfy"]["port"]
    free_port(port)
    root = config.runtime / "sources/comfy"
    cmd = [
        str(root / "main.py"),
        "--listen",
        "127.0.0.1",
        "--port",
        str(port),
        "--force-fp32" if precision == "float32" else "--force-fp16",
        "--use-pytorch-cross-attention",
        "--disable-auto-launch",
        "--extra-model-paths-config",
        str(config.runtime / "extra_model_paths.yaml"),
    ]
    cmd = config.inference_command("comfy", cmd, mounts)
    with owned_process(cmd, log, cwd=root, env={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}) as proc:
        url = f"http://127.0.0.1:{port}"
        startup_timeout = config["limits"]["startup_timeout"]
        if deadline is not None:
            startup_timeout = min(startup_timeout, max(0, deadline - time.monotonic()))
        wait_http(proc, url + "/system_stats", startup_timeout)
        execution_timeout = config["limits"]["timeout"]
        if deadline is not None:
            execution_timeout = min(execution_timeout, deadline - time.monotonic())
            if execution_timeout <= 0:
                raise TimeoutError("ComfyUI startup exhausted the inference time budget")
        service = ComfyClient(url, execution_timeout)
        try:
            yield service
        finally:
            service.client.close()


def prepare_inpaint(image, mask, output, crop_padding=64):
    """Crop image and mask together, retaining context and undistorted pixel coordinates."""
    if isinstance(crop_padding, bool) or not isinstance(crop_padding, int) or not 0 <= crop_padding <= 2048:
        raise ValueError("Crop padding must be an integer from 0 to 2048")
    with Image.open(image) as im:
        original = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")
    with Image.open(mask) as im:
        selected = im.convert("L")
    if selected.size != original.size:
        raise ValueError("Repaint mask must have the same dimensions as its source image")
    bounds = selected.getbbox()
    if bounds is None:
        raise ValueError("Repaint mask is empty")
    x0, y0, x1, y1 = bounds
    crop = (
        max(0, x0 - crop_padding), max(0, y0 - crop_padding),
        min(original.width, x1 + crop_padding), min(original.height, y1 + crop_padding),
    )
    region = original.crop(crop)
    # Use a matte for transparent parts: invisible RGB must not become model context.
    if region.mode == "RGBA":
        rgb = Image.new("RGB", region.size, "white")
        rgb.paste(region, mask=region.getchannel("A"))
    else:
        rgb = region
    # Local repair uses a smaller sampling canvas than full-image editing on the 22 GB card.
    scale = min(768 / max(region.size), 4)
    content_size = tuple(max(1, round(v * scale)) for v in region.size)
    size = tuple(max(64, math.ceil(v / 16) * 16) for v in content_size)
    offset = tuple((s - c) // 2 for s, c in zip(size, content_size))
    prepared = Image.new("RGB", size, "white")
    prepared.paste(rgb.resize(content_size, Image.Resampling.LANCZOS), offset)
    prepared_mask = Image.new("L", size)
    # Nearest keeps explicitly excluded holes and disconnected islands intact.
    prepared_mask.paste(selected.crop(crop).resize(content_size, Image.Resampling.NEAREST), offset)
    input_path = output.parent / (output.stem + "-input.png")
    mask_path = output.parent / (output.stem + "-mask.png")
    prepared.save(input_path)
    prepared_mask.save(mask_path)
    geometry = {
        "original_size": list(original.size), "mask_bounds": list(bounds),
        "crop_box": list(crop), "content_size": list(content_size),
        "inference_size": list(size), "offset": list(offset), "crop_padding": crop_padding,
    }
    return input_path, mask_path, original, selected, geometry


def composite_inpaint(generated, original, mask, geometry, output):
    """Restore source dimensions; neither the crop padding nor VAE drift may alter other pixels."""
    with Image.open(generated) as im:
        if list(im.size) != geometry["inference_size"]:
            raise RuntimeError("ComfyUI returned an unexpected repaint size; refusing misaligned composite")
        dx, dy = geometry["offset"]
        w, h = geometry["content_size"]
        patch = im.convert("RGB").crop((dx, dy, dx + w, dy + h))
    x0, y0, x1, y1 = geometry["crop_box"]
    patch = patch.resize((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    if original.mode == "RGBA":
        patch = patch.convert("RGBA")
        patch.putalpha(original.getchannel("A").crop((x0, y0, x1, y1)))
    result = original.copy()
    result.paste(patch, (x0, y0), mask.crop((x0, y0, x1, y1)))
    result.save(output)


def image_job(
    config, prompt, negative, output, log, image=None, reference=None,
    *, mask=None, seed=None, crop_padding=64, timeout=None,
):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if mask is not None and image is None:
        raise ValueError("Masked repaint requires a source image")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64):
        raise ValueError("Seed must be an unsigned 64-bit integer")
    prepared_repaint = prepare_inpaint(image, mask, output, crop_padding) if mask is not None else None
    template = read_json(config.root / "workflows" / ("edit.json" if image else "generate.json"))
    with session(
        config,
        log,
        [
            output.parent,
            *([Path(image).parent] if image else []),
            *([Path(reference).parent] if reference else []),
        ],
        precision="float32" if image is not None else "float16",
        timeout=timeout,
    ) as service:
        values = {
            **config["comfy"],
            **config["limits"],
            "prompt": prompt if image else prompt + ", masterpiece, high score, great score, absurdres",
            "negative": negative,
            "prefix": uuid.uuid4().hex,
        }
        if seed is not None:
            values["seed"] = seed
        if image:
            # VAE inputs need a sensible resolution and multiples of 16; preserve aspect ratio.
            prepared = output.parent / (output.stem + "-input.png")
            if prepared_repaint:
                prepared = prepared_repaint[0]
                values["prompt"] = (
                    "Edit only the selected area of Picture 1. Preserve surrounding line art, lighting and colors. "
                    + ("Picture 2 is the original character reference; preserve its drawing style and identity. "
                       if reference is not None else "")
                    + prompt
                )
            else:
                with Image.open(image) as im:
                    scale = min(1024 / max(im.size), 4)
                    size = tuple(max(64, round(v * scale / 16) * 16) for v in im.size)
                    im.convert("RGB").resize(size, Image.Resampling.LANCZOS).save(prepared)
            values["image"] = service.upload(prepared)
            values["reference"] = service.upload(reference) if reference is not None else ""
            values["steps"] = values["edit_steps"]
        graph = bind_workflow(template, values)
        if image is not None and reference is None:
            graph.pop("10")
            for key in ("5", "6"):
                graph[key]["inputs"].pop("image2")
        target = output
        if prepared_repaint:
            # LoadImage's alpha output is inverted. Read the explicit white=edit red channel instead.
            graph["15"] = {
                "class_type": "LoadImageMask",
                "inputs": {"image": service.upload(prepared_repaint[1]), "channel": "red"},
            }
            graph["16"] = {
                "class_type": "SetLatentNoiseMask",
                "inputs": {"samples": ["7", 0], "mask": ["15", 0]},
            }
            graph["12"]["inputs"]["latent_image"] = ["16", 0]
            target = output.parent / (output.stem + "-generated.png")
        service.execute(graph, target, template["output_node"])
        validate_generated_image(target, log)
        if prepared_repaint:
            _, _, original, selected, geometry = prepared_repaint
            composite_inpaint(target, original, selected, geometry, output)
            write_json(output.with_suffix(".inpaint.json"), {
                **geometry, "seed": values["seed"], "reference": reference is not None,
                "mask_conditioning": "SetLatentNoiseMask", "alpha_preserved": original.mode == "RGBA",
                "outside_mask_preserved": True,
            })


def validate_generated_image(path, log=None):
    with Image.open(path) as image:
        stats = ImageStat.Stat(image.convert("RGB"))
    if max(stats.stddev) < 1.0:
        raise RuntimeError(f"Degenerate generated image (nearly uniform); inspect precision and log: {log}")
