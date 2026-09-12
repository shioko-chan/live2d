from .process import run


def mouth(config, source, output, size, log):
    command = [
        str(config.root / "scripts/extract_mouth.py"),
        "--model",
        str(config.models / "sam2"),
        "--input",
        str(source),
        "--output",
        str(output),
        "--width",
        str(size[0]),
        "--height",
        str(size[1]),
    ]
    run(
        config.inference_command("comfy", command, [source.parent, output.parent]),
        log,
        timeout=180,
        env={"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
    )
