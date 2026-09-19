"""Local-only HTTP interface over the existing version store and pipeline."""

from contextlib import asynccontextmanager
import json
from pathlib import Path
import re
import uuid
from urllib.parse import quote

from PIL import Image
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..store import Store, digest, lock, read_json
from .jobs import JobRequest, Jobs, tail
from .presentation import part_names, refinement_changes, resume_targets

STATIC = Path(__file__).parent / "static"
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
PART_NAMES = {
    "back hair": "后发",
    "front hair": "前发",
    "headwear": "头饰",
    "face": "脸部",
    "facedetail": "脸部细节",
    "irides": "虹膜",
    "eyebrow": "眉毛",
    "eyewhite": "眼白",
    "eyelash": "睫毛",
    "eye_close": "闭眼",
    "eyewear": "眼镜",
    "ears": "耳朵",
    "earwear": "耳饰",
    "nose": "鼻子",
    "mouth": "嘴部",
    "mouth_open": "张嘴",
    "mouth_close": "闭嘴",
    "tooth-t": "上齿",
    "tooth-b": "下齿",
    "tongue": "舌头",
    "neck": "脖子",
    "neckwear": "颈饰",
    "topwear": "上衣",
    "handwear": "手部服饰",
    "bottomwear": "下装",
    "legwear": "腿部服饰",
    "footwear": "鞋履",
    "tail": "尾巴",
    "wings": "翅膀",
    "objects": "配件",
}


def contained(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise HTTPException(400, "文件路径超出了项目范围。")
    return path


def project_path(root, name):
    if not re.fullmatch(r"[\w-]{1,64}", name):
        raise HTTPException(400, "项目名称限 1–64 个中英文字、数字、下划线或连字符。")
    return contained(root, name)


def asset_url(project, revision, path):
    return f"/api/projects/{quote(project)}/assets/{revision}/{quote(path)}"


def previews(store, rev):
    directory = contained(store.root, str(store.revision_dir(rev.id)))
    found = []

    def add(path, label, group):
        if path.is_file() and path.suffix.lower() in IMAGES:
            relative = str(path.relative_to(directory))
            contained(directory, relative)
            found.append(
                {
                    "url": asset_url(store.root.name, rev.id, relative),
                    "label": label,
                    "group": group,
                    "view": path.stem if group == "poses" else None,
                    "parameters": {} if group == "poses" else None,
                }
            )

    add(directory / "rig/pose-00.png", "默认姿态", "poses")
    samples = directory / "rig/pose-samples.json"
    poses = read_json(samples) if samples.is_file() else []
    for path in sorted((directory / "rig").glob("pose-*.png")):
        if path.name == "pose-00.png":
            continue
        index = int(path.stem.split("-")[-1])
        pose = poses[index] if index < len(poses) else {}
        labels = []
        for key, value in pose.items():
            if key == "ParamAngleX":
                labels.append("向左转头" if value < 0 else "向右转头")
            elif key in {"ParamEyeLOpen", "ParamEyeROpen"}:
                labels.append(
                    ("左眼" if key == "ParamEyeLOpen" else "右眼") + ("闭合" if value < 0.5 else "睁开")
                )
            elif key in {
                "ParamAngleY",
                "ParamAngleZ",
                "ParamEyeBallX",
                "ParamEyeBallY",
                "ParamBrowLY",
                "ParamBrowRY",
                "ParamHairFront",
                "ParamHairBack",
            }:
                labels.append(
                    {
                        "ParamAngleY": "抬低头",
                        "ParamAngleZ": "歪头",
                        "ParamEyeBallX": "横向视线",
                        "ParamEyeBallY": "纵向视线",
                        "ParamBrowLY": "左眉",
                        "ParamBrowRY": "右眉",
                        "ParamHairFront": "前发摆动",
                        "ParamHairBack": "后发摆动",
                    }[key]
                    + f" {value:g}"
                )
            elif key in {
                "ParamBodyAngleX",
                "ParamBodyAngleY",
                "ParamBodyAngleZ",
                "ParamEyeBallForm",
                "ParamMouthForm",
            }:
                labels.append(
                    {
                        "ParamBodyAngleX": "身体左右",
                        "ParamBodyAngleY": "身体俯仰",
                        "ParamBodyAngleZ": "身体倾斜",
                        "ParamEyeBallForm": "眼睛形状",
                        "ParamMouthForm": "嘴部表情",
                    }[key]
                    + f" {value:g}"
                )
            elif key.startswith(("ParamComponent_", "ParamVisible_")):
                component_file = directory / "rig/components.json"
                components = read_json(component_file) if component_file.exists() else []
                component = next(
                    (c for c in components if key in {c.get("parameter"), c.get("toggle")}), None
                )
                labels.append((component["name"] if component else "部件") + f" {value:g}")
            elif key == "ParamMouthOpenY":
                labels.append("张嘴" if value > 0.5 else "闭嘴")
        add(path, " · ".join(labels) or f"姿态 {index}", "poses")
        found[-1]["parameters"] = pose
    add(directory / "materials/composite.png", "拆分合成", "art")
    for path in sorted((directory / "image").glob("input.*")):
        add(path, "角色立绘", "art")
    manifest = directory / "materials/parts.json"
    if manifest.is_file():
        for part in read_json(manifest).get("parts", []):
            side = {"left": "左侧", "right": "右侧"}.get(part.get("side"), "")
            label = side + PART_NAMES.get(part.get("semantic"), part["name"])
            add(
                contained(directory, "materials/" + part["image"]),
                part["name"] if part.get("binding") else label,
                "layers",
            )
            found[-1]["part"] = part["id"]
    return found


def revision_summary(rev):
    return {
        "id": rev.id,
        "parent": rev.parent,
        "status": rev.status,
        "plan": rev.plan.model_dump(),
        "runtime_verified": rev.runtime_verified,
        "source": rev.source,
        "reference": rev.reference,
        "resume_targets": resume_targets(rev),
        "stages": {
            key: {"state": value.state, "error": value.error, "files": len(value.artifacts)}
            for key, value in rev.stages.items()
        },
    }


class LocalRequests:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            if scope["method"] not in {"GET", "HEAD"}:
                origin = headers.get(b"origin")
                expected = b"http://" + headers.get(b"host", b"")
                if headers.get(b"x-l2d-request") != b"1" or (origin and origin != expected):
                    await JSONResponse({"error": "请从本地工作台提交操作。"}, 403)(scope, receive, send)
                    return

        async def secured(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"cache-control", b"no-cache"),
                        (b"referrer-policy", b"same-origin"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; img-src 'self' blob: data:; "
                            b"style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'",
                        ),
                    ]
                )
            await send(message)

        await self.app(scope, receive, secured)


def create_app(config, projects=None):
    projects = Path(projects or config.root / "projects").resolve()
    jobs = Jobs(config, projects)

    def store_for(name):
        store = Store(project_path(projects, name))
        if not (store.root / "project.json").is_file():
            raise HTTPException(404, "项目不存在。")
        return store

    def job_directory(job_id):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "任务不存在。")
        directory = jobs.root / job_id
        if not (directory / "job.json").is_file():
            raise HTTPException(404, "任务不存在。")
        return directory

    @asynccontextmanager
    async def lifespan(app):
        with lock(config.runtime / "web/server.lock"):
            jobs.recover()
            try:
                yield
            finally:
                await run_in_threadpool(jobs.close)

    def index(request):
        markup = (STATIC / "index.html").read_text()
        for asset in ("app.js", "repair.js", "style.css"):
            markup = markup.replace(f'/static/{asset}"', f'/static/{asset}?v={digest(STATIC / asset)[:12]}"')
        return HTMLResponse(markup)

    def state(request):
        items, warnings = [], []
        for path in projects.glob("*/project.json"):
            try:
                store = store_for(path.parent.name)
                project = read_json(path)
                rev = store.get() if project["head"] else None
                gallery = previews(store, rev) if rev else []
                items.append(
                    {
                        "name": path.parent.name,
                        "head": project["head"],
                        "accepted": project["accepted"],
                        "status": rev.status if rev else "empty",
                        "request": rev.plan.request if rev else "",
                        "thumbnail": gallery[0]["url"] if gallery else None,
                        "updated": path.stat().st_mtime,
                    }
                )
            except (ValueError, OSError, HTTPException) as exc:
                warnings.append(f"{path.parent.name}：{getattr(exc, 'detail', str(exc))}")
        return JSONResponse(
            {
                "projects": sorted(items, key=lambda x: x["updated"], reverse=True),
                "jobs": jobs.list(),
                "warnings": warnings,
            }
        )

    async def create_project(request):
        data = await request.json()
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise ValueError("请输入项目名称。")
        path = project_path(projects, data["name"].strip())

        def create():
            with lock(path / ".lock"):
                Store(path).init()

        await run_in_threadpool(create)
        return JSONResponse({"name": path.name}, 201)

    def project_detail(request):
        from .. import repair

        store = store_for(request.path_params["name"])
        project = read_json(store.root / "project.json")
        rid = request.query_params.get("revision") or project["head"]
        rev = store.get(rid) if rid else None
        history = []
        for path in sorted(
            store.root.glob("revisions/*/revision.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            item = read_json(path)
            history.append(
                {
                    "id": item["id"],
                    "parent": item["parent"],
                    "status": item["status"],
                    "request": item["plan"]["request"],
                }
            )
        return JSONResponse(
            {
                "name": store.root.name,
                **project,
                "revision": revision_summary(rev) if rev else None,
                "history": history,
                "previews": previews(store, rev) if rev else [],
                "part_names": part_names(store, rev, PART_NAMES) if rev else {},
                "repair_parts": read_json(store.revision_dir(rev.id) / "materials/parts.json")["parts"]
                if rev and (store.revision_dir(rev.id) / "materials/parts.json").is_file()
                else [],
                "repairs": repair.list_drafts(store, rev.id) if rev else [],
                "rig_replay": read_json(store.revision_dir(rev.id) / "rig-replay.json")
                if rev and (store.revision_dir(rev.id) / "rig-replay.json").is_file()
                else None,
                "refinement_changes": refinement_changes(store, rev) if rev else [],
                "video": f"/api/projects/{quote(store.root.name)}/video/{rev.id}?v="
                + str((store.root / "previews" / rev.id / "preview.mp4").stat().st_mtime_ns)
                if rev and (store.root / "previews" / rev.id / "preview.mp4").is_file()
                else None,
                "review": read_json(store.root / "reviews" / rev.id / "latest.json")
                if rev and (store.root / "reviews" / rev.id / "latest.json").is_file()
                else None,
            }
        )

    def repair_payload(store, draft):
        from .. import repair

        root = repair.directory(store, draft.id)
        prefix = f"/api/projects/{quote(store.root.name)}/repairs/{draft.id}"

        def url(path):
            return prefix + "/assets/" + path

        result = draft.model_dump()
        result.update(
            source=url("source.png"),
            before=url("base/composite.png"),
            mask=prefix + f"/mask?v={draft.version}",
        )
        images = []
        preview = draft.result.get("preview", {})
        for suffix, label in [
            ("component.png", "提取 / 替换部件"),
            ("underpaint.png", "遮挡补全"),
            ("cleanup-mask.png", "底层清理范围"),
            ("materials/composite.png", "修正后合成"),
        ]:
            path = preview.get("path", "") + "/" + suffix
            if preview and (root / path).is_file():
                images.append({"url": url(path), "label": label})
        for row in preview.get("views", []):
            images.append({**row, "url": url(row["path"])})
        for row in draft.result.get("binding_preview", {}).get("views", []):
            images.append({**row, "url": url(row["path"])})
        for row in draft.result.get("visual", {}).get("views", []):
            images.append({**row, "url": url(row["path"])})
        result["images"] = images
        segment = draft.result.get("segmentation", {})
        result["candidates"] = [
            {**r, "url": url(segment["path"] + "/" + r["mask"])} for r in segment.get("candidates", [])
        ]
        context = draft.result.get("rig_context")
        if context:
            result["rig_context"] = {**context, "url": url(context["image"])}
        return result

    async def repairs(request):
        from .. import repair
        from ..repair_schema import DraftCreate

        store = store_for(request.path_params["name"])
        if request.method == "GET":
            return JSONResponse(repair.list_drafts(store, request.query_params.get("revision")))
        spec = DraftCreate.model_validate(await request.json())
        with lock(store.root / ".lock"):
            draft = repair.create(config, store, spec)
        return JSONResponse(repair_payload(store, draft), 201)

    async def repair_draft(request):
        from .. import repair
        from ..repair_schema import DraftUpdate

        store = store_for(request.path_params["name"])
        identifier = request.path_params["draft"]
        if request.method == "PUT":
            draft = repair.update(store, identifier, DraftUpdate.model_validate(await request.json()))
        elif request.method == "DELETE":
            data = await request.json()
            repair.discard(store, identifier, int(data["version"]))
            return JSONResponse({"discarded": True})
        else:
            draft = repair.load(store, identifier)
        return JSONResponse(repair_payload(store, draft))

    def repair_asset(request):
        from .. import repair

        store = store_for(request.path_params["name"])
        root = repair.directory(store, request.path_params["draft"])
        path = contained(root, request.path_params["path"])
        if path.suffix.lower() not in IMAGES or not path.is_file():
            raise HTTPException(404, "修正图片不存在")
        return FileResponse(path)

    def repair_mask(request):
        import io
        from starlette.responses import Response
        from .. import repair

        store = store_for(request.path_params["name"])
        draft = repair.load(store, request.path_params["draft"])
        stream = io.BytesIO()
        mask_function = (
            repair.cleanup_mask if request.query_params.get("layer") == "cleanup" else repair.mask_image
        )
        mask_function(
            repair.directory(store, draft.id),
            draft,
            annotations=request.query_params.get("annotations") != "0",
        ).save(stream, format="PNG")
        return Response(stream.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})

    async def submit(request):
        store = store_for(request.path_params["name"])
        spec = JobRequest.model_validate(await request.json())
        if spec.action == "plan" and not spec.request.strip():
            raise ValueError("请先描述需求。")
        for token in (spec.source, spec.reference):
            if token and not (config.runtime / "web/uploads" / token).is_file():
                raise ValueError("上传素材不存在，请重新上传。")
        # Linux PDEATHSIG follows the spawning thread, not just its process.
        # Spawn on the long-lived event-loop thread; inference still runs in the worker.
        record = jobs.submit(store.root.name, spec)
        return JSONResponse(record, 202)

    async def upload(request):
        async with request.form(max_files=1, max_fields=0) as form:
            file = form.get("file")
            if not isinstance(file, UploadFile):
                raise ValueError("请选择一个 PNG、JPEG 或 PSD 文件。")
            suffix = Path(file.filename or "").suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".psd"}:
                raise ValueError("只支持 PNG、JPEG 或 PSD。")
            token = uuid.uuid4().hex + suffix
            directory = config.runtime / "web/uploads"
            directory.mkdir(parents=True, exist_ok=True)
            dest = directory / token

            def save():
                import shutil

                try:
                    if suffix == ".psd":
                        from psd_tools import PSDImage

                        PSDImage.open(file.file)
                    else:
                        with Image.open(file.file) as im:
                            if im.format not in {"PNG", "JPEG"}:
                                raise ValueError("图片内容与格式不符。")
                            im.verify()
                    file.file.seek(0)
                    with dest.open("xb") as stream:
                        shutil.copyfileobj(file.file, stream)
                except Exception as exc:
                    dest.unlink(missing_ok=True)
                    raise ValueError(f"无法读取或保存素材，请检查文件是否有效：{type(exc).__name__}") from exc

            await run_in_threadpool(save)
            return JSONResponse({"token": token, "name": file.filename}, 201)

    def asset(request):
        store = store_for(request.path_params["name"])
        directory = contained(store.root, str(store.revision_dir(request.path_params["revision"])))
        path = contained(directory, request.path_params["path"])
        if path.suffix.lower() not in IMAGES or not path.is_file():
            raise HTTPException(404, "预览图片不存在。")
        return FileResponse(path)

    def video(request):
        store = store_for(request.path_params["name"])
        rev = store.get(request.path_params["revision"])
        path = contained(store.root, f"previews/{rev.id}/preview.mp4")
        if not path.is_file():
            raise HTTPException(404, "预览视频尚未生成。")
        return FileResponse(
            path,
            media_type="video/mp4",
            filename=f"{store.root.name}-{rev.id}-preview.mp4",
            content_disposition_type="attachment" if "download" in request.query_params else "inline",
        )

    async def cancel(request):
        job_directory(request.path_params["job_id"])
        await run_in_threadpool(jobs.cancel, request.path_params["job_id"])
        return JSONResponse({"status": "cancelling"})

    def logs(request):
        directory = job_directory(request.path_params["job_id"])
        job = read_json(directory / "job.json")
        store = store_for(job["project"])
        files = [directory / "output.log"]
        planner_log = store.root / "planner.log"
        if (
            job["spec"]["action"] == "plan"
            and planner_log.is_file()
            and planner_log.stat().st_mtime >= job["created"]
        ):
            files.append(planner_log)
        if job["revision"]:
            revision = store.revision_dir(job["revision"])
            files.extend(sorted(revision.rglob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:6])
        result = []
        for path in files:
            if path.is_file():
                contained(directory if path.is_relative_to(directory) else store.root, str(path))
                result.append({"name": path.name, "text": tail(path)})
        return JSONResponse(result)

    def download(request):
        directory = job_directory(request.path_params["job_id"])
        job = read_json(directory / "job.json")
        if job["status"] != "succeeded" or not (directory / "model.zip").is_file():
            raise HTTPException(404, "模型包尚未生成。")
        return FileResponse(directory / "model.zip", filename=f"{job['project']}-{job['revision']}.zip")

    async def error(request, exc):
        if isinstance(exc, HTTPException):
            return JSONResponse({"error": exc.detail}, exc.status_code)
        if isinstance(exc, ValidationError):
            return JSONResponse(
                {"error": "输入不符合要求：" + "; ".join(e["msg"] for e in exc.errors())}, 400
            )
        status_code = (
            404 if isinstance(exc, FileNotFoundError) else 409 if isinstance(exc, RuntimeError) else 400
        )
        return JSONResponse({"error": str(exc)}, status_code)

    app = Starlette(
        routes=[
            Route("/", index),
            Route("/api/state", state),
            Route("/api/projects", create_project, methods=["POST"]),
            Route("/api/projects/{name}", project_detail),
            Route("/api/projects/{name}/jobs", submit, methods=["POST"]),
            Route("/api/projects/{name}/repairs", repairs, methods=["GET", "POST"]),
            Route("/api/projects/{name}/repairs/{draft}", repair_draft, methods=["GET", "PUT", "DELETE"]),
            Route("/api/projects/{name}/repairs/{draft}/mask", repair_mask),
            Route("/api/projects/{name}/repairs/{draft}/assets/{path:path}", repair_asset),
            Route("/api/projects/{name}/assets/{revision}/{path:path}", asset),
            Route("/api/projects/{name}/video/{revision}", video),
            Route("/api/uploads", upload, methods=["POST"]),
            Route("/api/jobs/{job_id}/cancel", cancel, methods=["POST"]),
            Route("/api/jobs/{job_id}/logs", logs),
            Route("/api/jobs/{job_id}/download", download),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ],
        lifespan=lifespan,
        middleware=[
            Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]),
            Middleware(LocalRequests),
        ],
        exception_handlers={
            HTTPException: error,
            ValueError: error,
            RuntimeError: error,
            FileNotFoundError: error,
            json.JSONDecodeError: error,
        },
        max_body_size=512 * 1024 * 1024,
    )
    app.state.jobs = jobs
    return app
