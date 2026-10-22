import json
import copy
from .process import free_port, owned_process, wait_http
from .schema import Plan
import httpx

SYSTEM = """你是本地 Live2D 流水线的需求规划器。只输出符合 schema 的 JSON，不执行操作。
先判断信息是否足够，再选择操作。你只能看到文字和工程元数据，看不到图片、圈选、鼠标选择或顶点坐标。
如果用户没有指明部件（例如“那个部件”），或要求按未提供的圈选、方向、距离修改：
必须 operations=[]，clarification 用中文询问缺少的信息，unsupported=[]。绝不猜测目标或几何数值。
例如“把那个改成红色”必须先问是哪个部件；“沿我圈出的方向移动一点”必须先问方向和距离。
缺少信息可以澄清，不属于 unsupported；只有无法支持的功能才写 unsupported。

新建角色：operations=[]，把用户要求逐项翻译为英文逗号分隔的 Animagine 标签，放在 prompt。
检查不得漏掉：性别年龄、发色、头发长度、眼睛颜色、衣服类型和颜色、构图、姿势、表情、背景。
女性用 1girl，男性用 1boy，其他用 1other；成年用 mature female/mature male。
长发必须单独包含 long hair；正面用 front view；半身用 cowboy shot；站立用 standing；
双臂自然下垂用 arms at sides；纯白背景用 white background。加入 solo, upright posture, looking at viewer。
negative 包含 lowres, bad anatomy, cropped, text, watermark, blurry, multiple views, extra faces, extra heads，
再加入用户明确排除的元素。不要擅自排除饰品或脸部细节。否定要求只放在 negative。
标签写法示例（只学习格式，不能照抄与本次需求无关的特征）：
输入“成年男性，蓝色短发，金色眼睛，红色外套，正面半身站立，白底，不戴帽子”时，
prompt="1boy, mature male, blue hair, short hair, yellow eyes, red coat, solo, front view, cowboy shot, standing, arms at sides, upright posture, looking at viewer, white background"，
negative="lowres, bad anatomy, cropped, text, watermark, blurry, multiple views, extra faces, extra heads, hat"。
发色与长度是两个标签，例如 silver hair, long hair；禁止把 no hat/no accessories 写入 prompt。

修改现有工程时，prompt 保留 previous_prompt。operations 仅包含本次请求的修改。
两种 ID 不可混用：
1. 素材操作 recolor/repaint/remove 的 target 只能填 context.parts.parts[].id。
   用 semantic/name/side 找到素材，但不能把 name、ArtMesh ID 或 lyid 当成素材 ID。
   改色用 recolor + color（#RRGGBB），保留阴影和透明度；删除用 remove，绝不改用 regenerate。
   同时修改前发和后发时，每个匹配的素材各一个操作，不要擅自拆成左右网格。
   repaint/add/regenerate 的英文图像指令放 instruction，不能放 arguments。
   add 需要明确的画布像素 bounds=[x,y,width,height] 和 semantic；未知位置先澄清。
2. 绑定操作用 kind=rig，tool 和 arguments 遵循 context.rig.tools 的 schema。
   调整参数范围用 parameter_update；透明度/网格关键帧用 keyform_set，不能用 regenerate。
   keyform_set 的 target={kind,id} 必须来自 context.rig.objects；网格是 ArtMesh ID，不能用素材 ID 或 lyid。
   利用 rig.layers 的 semanticTag/side 和对象 layerId 找到目标，coordinate 使用真实参数 ID。
   已知闭眼坐标和透明度足够设置关键帧，不需要顶点数据。例如 ParamEyeLOpen=0、透明度0：
   coordinate={"ParamEyeLOpen":0}, channels={"opacity":0}。只写请求的通道，不添加 geometry。
   修改顶点却没有坐标、目标位移或形状信息时，必须澄清，不编造控制点。
   没有 rig 上下文时，先要求构建模型。expected_history_head_node_id 由执行器填写，不输出也不询问。
regenerate 只用于明确的整图重生成或无法局部实现的轮廓改变，并且必须是唯一操作。
"""


def generation_schema(context=None):
    schema = Plan.model_json_schema()
    schema["properties"]["refinements"] = {"type": "array", "maxItems": 0, "items": {"type": "object"}}
    schema["$defs"].pop("Refinement", None)
    schema["$defs"].pop("Binding", None)
    properties = schema["$defs"]["Operation"]["properties"]
    fields = {
        "recolor": ["target", "color"],
        "repaint": ["target", "instruction"],
        "add": ["instruction", "bounds", "semantic"],
        "remove": ["target"],
        "rig": ["tool", "arguments"],
        "regenerate": ["instruction"],
    }
    variants = []
    for kind, required in fields.items():
        props = {"kind": {"const": kind}}
        for name in required:
            prop = dict(properties[name])
            if "anyOf" in prop:
                prop = next(p for p in prop["anyOf"] if p.get("type") != "null").copy()
            prop.pop("default", None)
            if prop.get("type") == "string":
                prop["minLength"] = 1
            props[name] = prop
            if name == "target":
                targets = [p["id"] for p in (context or {}).get("parts", {}).get("parts", [])]
                if targets:
                    prop["enum"] = targets
        variants.append(
            {
                "type": "object",
                "properties": props,
                "required": ["kind", *required],
                "additionalProperties": False,
            }
        )
    schema["$defs"]["Operation"] = {"anyOf": variants}
    return schema


def parse_plan(payload):
    # Some llama templates include reasoning separately; content itself must be JSON.
    result = Plan.model_validate_json(payload)
    if result.clarification or result.unsupported:
        return result
    return result


def compact_context(context):
    # Keep authoritative IDs and geometry bounds; omit duplicated transport/status fields.
    result = dict(context)
    if "rig" in result:
        rig = result["rig"]
        tools = copy.deepcopy(rig["tools"])
        for schema in tools.values():
            schema.get("properties", {}).pop("expected_history_head_node_id", None)
            if "required" in schema:
                schema["required"] = [
                    key for key in schema["required"] if key != "expected_history_head_node_id"
                ]
        keys = ("id", "sourceName", "semanticTag", "side", "bounds", "visible", "deleted")
        result["rig"] = {
            "canvas": {k: rig["state"].get(k) for k in ("canvasWidth", "canvasHeight")},
            "layers": [{k: row[k] for k in keys if k in row} for row in rig["layers"]["layers"]],
            "parameters": [
                {k: v for k, v in row.items() if k != "current"} for row in rig["parameters"]["parameters"]
            ],
            "objects": rig["objects"],
            "physics": rig["physics"],
            "tools": tools,
        }
    return result


def make_plan(config, request, context, log):
    config.require_local_models("本地需求规划")
    cfg = config["planner"]
    path = config.resolve(cfg["model"])
    if not path.is_file():
        raise FileNotFoundError(f"Planner model missing: {path}")
    port = cfg["port"]
    free_port(port)
    command = [
        cfg["executable"],
        "-m",
        str(path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-c",
        str(cfg["context"]),
        "-ngl",
        str(cfg["gpu_layers"]),
        "--parallel",
        "1",
    ]
    url = f"http://127.0.0.1:{port}"
    schema = generation_schema(context)
    with owned_process(command, log) as proc:
        wait_http(proc, url + "/health", config["limits"]["startup_timeout"])
        with httpx.Client(timeout=config["limits"]["timeout"], trust_env=False) as client:
            response = client.post(
                url + "/v1/chat/completions",
                json={
                    "model": "local",
                    "temperature": 0.1,
                    "max_tokens": 3072,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "live2d_plan", "schema": schema},
                    },
                    "messages": [
                        {
                            "role": "system",
                            "content": SYSTEM + "\nOutput schema:\n" + json.dumps(schema),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"request": request, "context": compact_context(context)}, ensure_ascii=False
                            ),
                        },
                    ],
                },
            )
            response.raise_for_status()
            from .store import write_json

            completion = response.json()
            write_json(log.with_suffix(".response.json"), completion)
            choice = completion["choices"][0]
            if choice.get("finish_reason") == "length":
                raise RuntimeError(
                    "Planner output truncated; inspect its response JSON before changing the budget"
                )
            plan = parse_plan(choice["message"]["content"])
            plan.request = request
            return plan
