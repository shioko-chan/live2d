"""Build API-format ComfyUI graphs; no ComfyUI import, inference or server."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1] / "workflows"


def node(kind, **inputs):
    return {"class_type": kind, "inputs": inputs}


def save(name, graph, bindings):
    (root / name).write_text(
        json.dumps({"prompt": graph, "bindings": bindings, "output_node": "9"}, indent=2)
    )


save(
    "generate.json",
    {
        "1": node("CheckpointLoaderSimple", ckpt_name=""),
        "2": node("CLIPTextEncode", text="", clip=["1", 1]),
        "3": node("CLIPTextEncode", text="", clip=["1", 1]),
        "4": node("EmptyLatentImage", width=832, height=1216, batch_size=1),
        "5": node(
            "KSampler",
            model=["1", 0],
            positive=["2", 0],
            negative=["3", 0],
            latent_image=["4", 0],
            seed=42,
            steps=28,
            cfg=5.0,
            sampler_name="euler_ancestral",
            scheduler="normal",
            denoise=1.0,
        ),
        "8": node("VAEDecode", samples=["5", 0], vae=["1", 2]),
        "9": node("SaveImage", images=["8", 0], filename_prefix="l2d"),
    },
    {
        "checkpoint": [["1", "ckpt_name"]],
        "prompt": [["2", "text"]],
        "negative": [["3", "text"]],
        "width": [["4", "width"]],
        "height": [["4", "height"]],
        "seed": [["5", "seed"]],
        "steps": [["5", "steps"]],
        "prefix": [["9", "filename_prefix"]],
    },
)

save(
    "edit.json",
    {
        "1": node(
            "UnetLoaderGGUFAdvanced",
            unet_name="",
            dequant_dtype="float16",
            patch_dtype="float16",
            patch_on_device=False,
        ),
        "2": node("CLIPLoaderGGUF", clip_name="", type="qwen_image"),
        "3": node("VAELoader", vae_name=""),
        "4": node("LoadImage", image=""),
        "10": node("LoadImage", image=""),
        "5": node(
            "TextEncodeQwenImageEditPlus",
            clip=["2", 0],
            vae=["3", 0],
            image1=["4", 0],
            image2=["10", 0],
            prompt="",
        ),
        "6": node(
            "TextEncodeQwenImageEditPlus",
            clip=["2", 0],
            vae=["3", 0],
            image1=["4", 0],
            image2=["10", 0],
            prompt="",
        ),
        "7": node("VAEEncode", pixels=["4", 0], vae=["3", 0]),
        "11": node("ModelSamplingAuraFlow", model=["1", 0], shift=3.1),
        "13": node(
            "FluxKontextMultiReferenceLatentMethod",
            conditioning=["5", 0],
            reference_latents_method="index_timestep_zero",
        ),
        "14": node(
            "FluxKontextMultiReferenceLatentMethod",
            conditioning=["6", 0],
            reference_latents_method="index_timestep_zero",
        ),
        "12": node(
            "KSampler",
            model=["11", 0],
            positive=["13", 0],
            negative=["14", 0],
            latent_image=["7", 0],
            seed=42,
            steps=28,
            cfg=3.0,
            sampler_name="euler",
            scheduler="simple",
            denoise=1.0,
        ),
        "8": node("VAEDecode", samples=["12", 0], vae=["3", 0]),
        "9": node("SaveImage", images=["8", 0], filename_prefix="l2d-edit"),
    },
    {
        "unet": [["1", "unet_name"]],
        "clip": [["2", "clip_name"]],
        "vae": [["3", "vae_name"]],
        "image": [["4", "image"]],
        "reference": [["10", "image"]],
        "prompt": [["5", "prompt"]],
        "negative": [["6", "prompt"]],
        "seed": [["12", "seed"]],
        "steps": [["12", "steps"]],
        "prefix": [["9", "filename_prefix"]],
    },
)
