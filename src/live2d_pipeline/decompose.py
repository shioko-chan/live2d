from .process import run


def decompose(config, image, output, log):
    output.mkdir(parents=True, exist_ok=True)
    source = config.runtime / "sources/see-through"
    command = [
        str(config.root / "scripts/see_through_fp16.py"),
        "--source-root",
        str(source),
        "--srcp",
        str(image),
        "--save_dir",
        str(output),
        "--repo_id_layerdiff",
        str(config.models / "see-through/layerdiff"),
        "--repo_id_depth",
        str(config.models / "see-through/marigold"),
        "--resolution",
        "1024",
        "--seed",
        str(config["limits"]["seed"]),
        "--save_to_psd",
        "--tblr_split",
    ]
    command = config.inference_command("see-through", command, [image.parent, output])
    run(
        command,
        log,
        config["limits"]["timeout"],
        cwd=source,
        env={
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_HUB_CACHE": str(config.hf_cache),
            "L2D_LAMA_MODEL": str(config.models / "see-through/lama/lama_large_512px.ckpt"),
        },
    )
    artwork = output / f"{image.stem}.psd"
    if not artwork.is_file():
        raise RuntimeError(f"See-through did not produce its color PSD: {artwork}")
    return artwork
