import asyncio
import secrets
import shutil
import time
from datetime import timedelta
from contextlib import asynccontextmanager
import httpx
import jsonschema
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from .process import owned_process
from .store import write_json, read_json
from .schema import Operation

ALLOWED = {"parameter_update", "rig_transform", "keyform_set", "physics_put"}


def blink_operations(layers, objects):
    operations = []
    by_id = {layer["id"]: layer for layer in layers}
    for obj in objects:
        if obj["kind"] != "mesh" or obj.get("layerId") not in by_id:
            continue
        layer = by_id[obj["layerId"]]
        if layer.get("semanticTag") not in {"irides", "eyewhite"} or layer.get("deleted"):
            continue
        parameter = {"left": "ParamEyeLOpen", "right": "ParamEyeROpen"}.get(layer.get("side"))
        if parameter is None:
            continue
        for opening, opacity in [(0.0, 0.0), (0.15, 1.0), (1.0, 1.0)]:
            operations.append(
                Operation(
                    kind="rig",
                    tool="keyform_set",
                    arguments={
                        "target": {"kind": "mesh", "id": obj["id"]},
                        "coordinate": {parameter: opening},
                        "channels": {"opacity": opacity},
                    },
                )
            )
    return operations


def content(result):
    if result.isError:
        raise RuntimeError(str(result.content))
    if result.structuredContent is not None:
        return result.structuredContent
    import json

    for block in result.content:
        if block.type == "text":
            return json.loads(block.text)
    return {}


class RigSession:
    def __init__(self, session):
        self.session = session
        self.tools = {}

    async def discover(self):
        listing = await self.session.list_tools()
        self.tools = {t.name: t.inputSchema for t in listing.tools}
        return self.tools

    async def call(self, name, args=None):
        args = args or {}
        if name not in self.tools:
            raise RuntimeError(f"Pinned PSD2Live tool unavailable: {name}")
        jsonschema.validate(args, self.tools[name])
        return content(await self.session.call_tool(name, args))

    async def apply(self, operations):
        targets = None
        for operation in operations:
            if operation.kind != "rig":
                continue
            if operation.tool not in ALLOWED:
                raise ValueError("Rig operation is not allowed")
            state = await self.call("project_get_state")
            args = dict(operation.arguments)
            if operation.tool == "keyform_set":
                if targets is None:
                    targets = {(row["kind"], row["id"]) for row in await self.objects()}
                target = args.get("target", {})
                if (target.get("kind"), target.get("id")) not in targets:
                    raise ValueError("keyform_set requires an actual rig object ID, not a source layer ID")
            args["expected_history_head_node_id"] = state["historyHeadNodeId"]
            # Do not retry uncertain writes. This process edits a disposable candidate workspace.
            await self.call(operation.tool, args)

    async def objects(self):
        objects = []
        offset = 0
        while True:
            page = await self.call("rig_list_objects", {"offset": offset, "limit": 64})
            objects.extend(page.get("objects", []))
            offset = page.get("nextOffset")
            if offset is None:
                break
        return objects

    async def context(self):
        return {
            "state": await self.call("project_get_state"),
            "layers": await self.call("project_list_layers"),
            "parameters": await self.call("project_list_parameters"),
            "objects": await self.objects(),
            "physics": await self.call("physics_list"),
            "tools": {k: v for k, v in self.tools.items() if k in ALLOWED},
        }


@asynccontextmanager
async def open_rig(config, psd, workspace, control, log):
    control.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    argfile = control / "args.json"
    write_json(control / "config.json", config["rig"])
    write_json(argfile, [str(psd), str(workspace), str(control), str(control / "config.json")])
    source = config.runtime / "sources/psd2live"
    command = [
        str(source / "gradlew"),
        "--offline",
        "--no-daemon",
        "--console=plain",
        "pipelineServer",
        f"-PpipelineArgs={argfile}",
    ]
    with owned_process(
        command,
        log,
        cwd=source,
        env={"L2D_MCP_TOKEN": token, "GRADLE_USER_HOME": str(config.runtime / "gradle-home")},
    ) as proc:
        ready = control / "ready.json"
        deadline = time.monotonic() + config["limits"]["timeout"]
        while not ready.exists():
            if proc.poll() is not None:
                raise RuntimeError(f"PSD2Live exited; see {log}")
            if time.monotonic() > deadline:
                raise TimeoutError("PSD2Live startup timed out")
            await asyncio.sleep(0.2)
        endpoint = read_json(ready)["endpoint"]
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=config["limits"]["timeout"], trust_env=False
        ) as http:
            async with streamable_http_client(endpoint, http_client=http) as (reader, writer, _):
                async with ClientSession(
                    reader, writer, read_timeout_seconds=timedelta(seconds=config["limits"]["timeout"])
                ) as session:
                    await session.initialize()
                    rig = RigSession(session)
                    await rig.discover()
                    yield rig, proc


async def build(config, psd, output, operations, parent_workspace=None):
    output.mkdir(parents=True, exist_ok=True)
    workspace = output / "source.psd2live"
    if parent_workspace:
        shutil.copyfile(parent_workspace, workspace)
    control = output / "control"
    if control.exists():
        shutil.rmtree(control)
    async with open_rig(config, psd, workspace, control, output / "rig.log") as (rig, proc):
        if parent_workspace is None:
            # A zero-height clipping mesh can still leave a coloured antialiased slit.
            # Author the final blink fade only on newly generated rigs; preserve existing workspaces.
            layers = await rig.call("project_list_layers")
            defaults = blink_operations(layers["layers"], await rig.objects())

            def coordinate_key(operation):
                args = operation.arguments
                target = args.get("target", {})
                return (
                    target.get("kind"),
                    target.get("id"),
                    tuple(sorted(args.get("coordinate", {}).items())),
                )

            authored = {coordinate_key(op) for op in operations if op.tool == "keyform_set"}
            await rig.apply([op for op in defaults if coordinate_key(op) not in authored])
        await rig.apply(operations)
        write_json(output / "rig-context.json", await rig.context())
        # Render finite parameter samples on the upstream CPU renderer.
        state = await rig.call("project_list_parameters")
        params = {p["id"]: p for p in state.get("parameters", [])}
        from base64 import b64decode

        samples = [
            {},
            *[
                {k: p["min"]}
                for k, p in params.items()
                if k in {"ParamAngleX", "ParamEyeLOpen", "ParamMouthOpenY"}
            ],
            *[
                {k: p["max"]}
                for k, p in params.items()
                if k in {"ParamAngleX", "ParamEyeLOpen", "ParamMouthOpenY"}
            ],
        ]
        # Use exact live schema; request construction is checked before calling.
        snapshot = await rig.call("project_get_state")
        if "ParamAngleX" in params and "ParamEyeLOpen" in params:
            samples.append(
                {"ParamAngleX": params["ParamAngleX"]["max"], "ParamEyeLOpen": params["ParamEyeLOpen"]["min"]}
            )
        if {"ParamEyeLOpen", "ParamEyeROpen"} <= params.keys():
            samples.append({"ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0})
        write_json(output / "pose-samples.json", samples)
        for i, pose in enumerate(samples):
            arguments = {
                "parameters": {**{k: p["default"] for k, p in params.items()}, **pose},
                "viewport": {
                    "mode": "canvas_rect",
                    "left": 0,
                    "top": 0,
                    "width": snapshot["canvasWidth"],
                    "height": snapshot["canvasHeight"],
                },
                "background": "transparent",
                "target_long_edge": 768,
            }
            jsonschema.validate(arguments, rig.tools["view_render_model"])
            rendered = await rig.session.call_tool("view_render_model", arguments)
            if rendered.isError:
                raise RuntimeError(f"Pose preview failed: {rendered.content}")
            images = [block for block in rendered.content if block.type == "image"]
            if not images:
                raise RuntimeError("Missing pose image")
            (output / f"pose-{i:02}.png").write_bytes(b64decode(images[0].data))
        bundle = output / "bundle"
        write_json(control / "export.request.json", {"output": str(bundle)})
        deadline = time.monotonic() + config["limits"]["timeout"]
        done = control / "export.done.json"
        while not done.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("PSD2Live export failed or timed out")
            await asyncio.sleep(0.2)
        result = read_json(done)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "Export failed"))
        return bundle
