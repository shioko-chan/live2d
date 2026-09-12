"""Evaluate real Chinese planning requests without applying any generated operation.

Run with the project environment, for example:
  .venv/bin/python scripts/evaluate_planner.py --config configs/local.toml \
      --output projects/diagnostics/planner-evaluation.json

Each request uses make_plan's owned llama-server process. The runtime task lock
prevents overlapping this evaluation with image generation or other pipeline jobs.
"""

import argparse
import copy
import csv
import json
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

from live2d_pipeline.config import ROOT, Config
from live2d_pipeline.pipeline import Pipeline
from live2d_pipeline.planner import compact_context, make_plan
from live2d_pipeline.process import owned_process
from live2d_pipeline.store import Store, digest, fingerprint, lock, write_json


@contextmanager
def sample_gpu():
    """Own one 200 ms NVIDIA query; retain only matching planner process memory."""
    stats = {
        "status": "unavailable",
        "interval_seconds": 0.2,
        "process_filter": "process name ends with /llama-server or equals llama-server",
        "peak_process_mib": None,
        "matching_samples": 0,
        "pids": [],
    }
    stop = threading.Event()
    with tempfile.TemporaryDirectory(prefix="l2d-planner-gpu-") as temporary:
        log = Path(temporary) / "nvidia.csv"
        command = [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
            "--loop-ms=200",
        ]
        # owned_process installs signal handlers and must enter on the main thread.
        process = owned_process(command, log)
        try:
            proc = process.__enter__()
        except OSError as exc:
            stats["error"] = str(exc)
            yield stats
            return
        stats["status"] = "available"

        def monitor():
            try:
                with log.open() as stream:
                    pending = ""
                    while True:
                        pending += stream.read()
                        lines = pending.split("\n")
                        pending = lines.pop()
                        for line in lines:
                            row = next(csv.reader([line]), [])
                            if len(row) != 3:
                                continue
                            pid, name, memory = (value.strip() for value in row)
                            if name != "llama-server" and not name.endswith("/llama-server"):
                                continue
                            try:
                                memory = float(memory)
                                pid = int(pid)
                            except ValueError:
                                continue
                            stats["matching_samples"] += 1
                            stats["peak_process_mib"] = max(stats["peak_process_mib"] or 0, memory)
                            if pid not in stats["pids"]:
                                stats["pids"].append(pid)
                        if proc.poll() is not None:
                            stats["status"] = "unavailable"
                            stats["error"] = f"nvidia-smi exited with {proc.returncode}"
                            break
                        if stop.is_set():
                            break
                        # Wake on shutdown, then consume the final buffered sample.
                        stop.wait(0.2)
            except Exception as exc:
                stats["status"] = "unavailable"
                stats["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(target=monitor, name="planner-gpu-sampler", daemon=True)
        try:
            thread.start()
            yield stats
        finally:
            stop.set()
            thread.join(timeout=1)
            process.__exit__(None, None, None)
            if stats["status"] == "available" and not stats["matching_samples"]:
                stats["status"] = "no_matching_process_observed"


def build_cases(context):
    """Resolve expected targets from the actual character, independently of the LLM."""
    parts = context["parts"]["parts"]

    def part_ids(semantic):
        matches = [part["id"] for part in parts if part["semantic"] == semantic]
        if not matches:
            raise ValueError(f"Evaluation character needs a {semantic!r} part")
        return matches

    hair = part_ids("front hair") + part_ids("back hair")
    noses = part_ids("nose")
    if len(noses) != 1:
        raise ValueError("Evaluation requires an unambiguous nose part")
    rig = context["rig"]
    layers = {
        row["id"]
        for row in rig["layers"]["layers"]
        if row.get("semanticTag") == "irides" and row.get("side") == "left" and not row.get("deleted")
    }
    meshes = [row["id"] for row in rig["objects"] if row["kind"] == "mesh" and row.get("layerId") in layers]
    if len(meshes) != 1:
        raise ValueError("Evaluation requires one authoritative left iris ArtMesh")
    parameter_ids = {row["id"] for row in rig["parameters"]["parameters"]}
    if not {"ParamAngleX", "ParamEyeLOpen"} <= parameter_ids:
        raise ValueError("Evaluation requires head angle and left eye openness parameters")
    return [
        {
            "id": "new_character",
            "request": "生成一个成年女性动漫角色，红色长发、绿色眼睛、黑色大衣，正面站立半身，双臂自然下垂，白色背景，不戴任何饰品。",
            "context": {},
            "expected": {
                "kind": "generation",
                "positive_groups": [
                    ["1girl"],
                    ["mature female", "adult woman", "adult female"],
                    ["red hair"],
                    ["long hair"],
                    ["green eyes"],
                    ["black coat", "black overcoat"],
                    ["front view", "front facing"],
                    ["cowboy shot", "half body"],
                    ["standing"],
                    ["arms at sides"],
                    ["white background"],
                ],
                "negative_groups": [["accessories", "jewelry", "jewellery"]],
            },
        },
        {
            "id": "recolor_hair",
            "request": "把前发和后发都改成紫色 #8A2BE2，保留原来的透明度、轮廓、阴影和其他部件。",
            "context": context,
            "expected": {"kind": "recolor", "targets": hair, "color": "#8a2be2"},
        },
        {
            "id": "remove_nose",
            "request": "删除鼻子图层，保留脸和其余所有图层，不需要重新生成图片。",
            "context": context,
            "expected": {"kind": "remove", "targets": noses},
        },
        {
            "id": "parameter_range",
            "request": "把头部左右转动参数 ParamAngleX 的范围改成 -20 到 20，默认值设为 0，其他参数和图层保持不变。",
            "context": context,
            "expected": {
                "kind": "rig",
                "tool": "parameter_update",
                "arguments": {"parameter_id": "ParamAngleX", "min": -20, "max": 20, "default": 0},
            },
        },
        {
            "id": "artmesh_keyform",
            "request": "让角色左眼虹膜在左眼完全闭合（ParamEyeLOpen=0）时不可见：只把该网格在这个坐标的关键帧不透明度设为 0，不改变顶点、其他坐标或其他部件。",
            "context": context,
            "expected": {
                "kind": "rig",
                "tool": "keyform_set",
                "arguments": {
                    "target": {"kind": "mesh", "id": meshes[0]},
                    "coordinate": {"ParamEyeLOpen": 0},
                    "channels": {"opacity": 0},
                },
            },
        },
        {
            "id": "ambiguous_target",
            "request": "把那个部件改成红色。",
            "context": context,
            "expected": {"kind": "clarification"},
        },
        {
            "id": "missing_geometry",
            "request": "把左眼虹膜网格的第三个顶点往我图里圈出的方向移动一点。",
            "context": context,
            "expected": {"kind": "clarification"},
        },
    ]


def assess(case, plan):
    """Check requested semantics and authoritative IDs, beyond JSON shape alone."""
    errors = []
    context, expected = case["context"], case["expected"]

    def check(condition, message):
        if not condition:
            errors.append(message)

    if expected["kind"] == "clarification":
        check(bool(plan.clarification), "Ambiguous request requires a clarification")
        check(not plan.operations, "Ambiguous request must not contain executable operations")
        check(not plan.unsupported, "Missing user information is clarifiable, not unsupported")
        return errors
    check(not plan.clarification, "Actionable request unexpectedly requires clarification")
    check(not plan.unsupported, "Supported request was rejected")

    if expected["kind"] == "generation":
        check(not plan.operations, "New character must not edit nonexistent parts")
        for field, groups in [("prompt", "positive_groups"), ("negative", "negative_groups")]:
            text = getattr(plan, field).lower().replace("_", " ").replace("-", " ")
            tags = {tag.strip() for tag in text.split(",")}
            for alternatives in expected[groups]:
                check(any(tag in tags for tag in alternatives), f"{field} omitted {alternatives}")
            if field == "prompt":
                check(
                    not any(tag.startswith(("no ", "not ", "without ")) for tag in tags),
                    "Negative instruction was placed in the positive prompt",
                )
        return errors

    check(bool(plan.operations), "Actionable revision produced no operations")
    part_ids = {row["id"] for row in context["parts"]["parts"]}
    rig = compact_context(context)["rig"]
    objects = {(row["kind"], row["id"]) for row in rig["objects"]}
    parameters = {row["id"] for row in rig["parameters"]}
    for operation in plan.operations:
        check(operation.kind == expected["kind"], f"Unexpected operation: {operation.kind}")
        if operation.kind in {"recolor", "remove", "repaint"}:
            check(operation.target in part_ids, f"Invented part ID: {operation.target}")
        if operation.kind != "rig":
            continue
        args = operation.arguments
        check("expected_history_head_node_id" not in args, "Planner invented executor-owned history ID")
        schema = rig["tools"].get(operation.tool)
        if schema is None:
            errors.append(f"Unavailable rig tool: {operation.tool}")
            continue
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as exc:
            errors.append(f"Invalid {operation.tool} arguments: {exc.message}")
        check(not set(args) - set(schema["properties"]), "Rig arguments contain unknown fields")
        if operation.tool == "parameter_update":
            check(args.get("parameter_id") in parameters, "Invented parameter ID")
        if operation.tool == "keyform_set":
            target = args.get("target", {})
            check((target.get("kind"), target.get("id")) in objects, "Target is not an actual rig object")
            check(set(args.get("coordinate", {})) <= parameters, "Keyform uses an invented parameter")

    if expected["kind"] in {"recolor", "remove"}:
        check(
            sorted(operation.target or "" for operation in plan.operations) == sorted(expected["targets"]),
            "Selected targets differ from the requested parts (or were duplicated)",
        )
        if expected["kind"] == "recolor":
            check(
                all((operation.color or "").lower() == expected["color"] for operation in plan.operations),
                "Recolor did not preserve the specified hex color",
            )
    if expected["kind"] == "rig":
        check(len(plan.operations) == 1, "Requested exactly one rig edit")
        for operation in plan.operations:
            check(operation.tool == expected["tool"], "Incorrect rig tool")
            for key, value in expected["arguments"].items():
                check(operation.arguments.get(key) == value, f"Incorrect rig argument: {key}")
            check(
                set(operation.arguments) <= set(expected["arguments"]),
                "Rig edit changes unrequested fields",
            )
    return errors


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--project", type=Path, default=ROOT / "projects/final-acceptance")
    parser.add_argument("--revision", help="Use a fixed revision; defaults to the current project head")
    parser.add_argument("--output", type=Path, required=True, help="JSON report outside the source project")
    args = parser.parse_args()
    config, store = Config(args.config), Store(args.project)
    output = args.output.resolve()
    if output.is_relative_to(store.root):
        parser.error("--output must be outside the source project to keep it unchanged")
    logs = output.parent / (output.stem + "-logs")
    started = time.perf_counter()
    with lock(config.runtime / "task.lock"), lock(store.root / ".lock"):
        revision = store.get(args.revision)
        context = Pipeline(config, store).context(revision)
        cases = build_cases(context)
        directory = store.revision_dir(revision.id)
        inputs = [
            store.root / "project.json",
            directory / "revision.json",
            directory / "materials/parts.json",
            directory / "rig/rig-context.json",
        ]
        hashes = {str(path): digest(path) for path in inputs}
        report = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project": str(store.root),
            "revision": revision.id,
            "config": str(config.path),
            "config_sha256": digest(config.path),
            "planner_source_sha256": digest(ROOT / "src/live2d_pipeline/planner.py"),
            "evaluation_source_sha256": digest(Path(__file__)),
            "planner": config["planner"],
            "model_resolved_path": str(config.resolve(config["planner"]["model"]).resolve()),
            "context_sha256": fingerprint(context),
            "input_sha256": hashes,
            "timing": "Each case includes llama-server loading, inference, and process cleanup.",
            "scope": "Planning only; no generated image or rig operation is executed.",
            "cases": [],
        }
        write_json(logs / "context.json", context)
        for case in cases:
            entry = {key: value for key, value in case.items() if key != "context"}
            entry["uses_existing_character"] = bool(case["context"])
            before = time.perf_counter()
            try:
                with sample_gpu() as gpu:
                    entry["gpu"] = gpu
                    plan = make_plan(
                        config, case["request"], copy.deepcopy(case["context"]), logs / f"{case['id']}.log"
                    )
                entry["plan"] = plan.model_dump()
                entry["errors"] = assess(case, plan)
            except Exception as exc:
                entry["errors"] = [f"{type(exc).__name__}: {exc}"]
            entry["elapsed_seconds"] = round(time.perf_counter() - before, 3)
            entry["passed"] = not entry["errors"]
            report["cases"].append(entry)
            report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            write_json(output, report)
            print(
                json.dumps(
                    {key: entry[key] for key in ("id", "passed", "elapsed_seconds", "errors")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        report["source_unchanged"] = hashes == {str(path): digest(path) for path in inputs}
        report["passed"] = report["source_unchanged"] and all(row["passed"] for row in report["cases"])
        report["passed_count"] = sum(row["passed"] for row in report["cases"])
        report["total_count"] = len(cases)
        report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        write_json(output, report)
    print(f"{report['passed_count']}/{len(cases)} passed; report: {output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
