import copy
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

    def upload(self, image):
        with Path(image).open("rb") as f:
            r = self.client.post(
                "/upload/image",
                files={"image": (uuid.uuid4().hex + ".png", f, "image/png")},
                data={"overwrite": "false", "type": "input"},
            )
        r.raise_for_status()
        value = r.json()
        return "/".join(x for x in [value.get("subfolder", ""), value["name"]] if x)

    def execute(self, graph, output, output_node):
        info = self.client.get("/object_info")
        info.raise_for_status()
        missing = {n["class_type"] for n in graph.values()} - info.json().keys()
        if missing:
            raise RuntimeError(f"Missing ComfyUI nodes: {sorted(missing)}")
        r = self.client.post("/prompt", json={"prompt": graph, "client_id": uuid.uuid4().hex})
        r.raise_for_status()
        body = r.json()
        if body.get("node_errors"):
            raise RuntimeError(str(body["node_errors"]))
        pid = body["prompt_id"]
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            r = self.client.get(f"/history/{pid}")
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
                    data = self.client.get("/view", params=images[0])
                    data.raise_for_status()
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data.content)
                    with Image.open(output) as im:
                        im.verify()
                    return
                if status.get("completed"):
                    raise RuntimeError("ComfyUI completed without required image")
            time.sleep(0.5)
        raise TimeoutError(f"ComfyUI job {pid} timed out")


@contextmanager
def session(config, log, mounts=(), precision="float16"):
    if precision not in {"float16", "float32"}:
        raise ValueError("Unsupported inference precision")
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
        wait_http(proc, url + "/system_stats", config["limits"]["startup_timeout"])
        service = ComfyClient(url, config["limits"]["timeout"])
        try:
            yield service
        finally:
            service.client.close()


def image_job(config, prompt, negative, output, log, image=None, reference=None):
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
    ) as service:
        values = {
            **config["comfy"],
            **config["limits"],
            "prompt": prompt if image else prompt + ", masterpiece, high score, great score, absurdres",
            "negative": negative,
            "prefix": uuid.uuid4().hex,
        }
        if image:
            # VAE inputs need a sensible resolution and multiples of 16; preserve aspect ratio.
            prepared = output.parent / (output.stem + "-input.png")
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
        service.execute(graph, output, template["output_node"])
        validate_generated_image(output, log)


def validate_generated_image(path, log=None):
    with Image.open(path) as image:
        stats = ImageStat.Stat(image.convert("RGB"))
    if max(stats.stddev) < 1.0:
        raise RuntimeError(f"Degenerate generated image (nearly uniform); inspect precision and log: {log}")
