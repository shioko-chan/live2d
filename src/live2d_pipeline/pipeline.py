import asyncio
import shutil
from PIL import Image
from . import assets, comfy, decompose, rig, segment, refinement
from .schema import Part, Stage
from .store import digest, fingerprint, read_json, write_json
from .validation import validate_bundle


class Pipeline:
    def __init__(self, config, store):
        self.config, self.store = config, store

    def model_identity(self):
        result = {}
        for spec in read_json(self.config.root / "models.lock.json")["files"]:
            path = self.config.models / spec["destination"]
            if path.is_file():
                stat = path.stat()
                result[spec["destination"]] = [stat.st_size, stat.st_mtime_ns]
        return result

    def stage(self, revision, name, inputs, action):
        directory = self.store.revision_dir(revision.id)
        # Code/workflow fingerprints prevent reuse after implementation/config changes.
        root = self.config.root
        implementation = {
            str(p.relative_to(root)): digest(p)
            for base in ("src", "workflows", "integrations", "scripts", "containers")
            for p in (root / base).rglob("*")
            if p.is_file()
            and p.suffix in {".py", ".json", ".kt", ".kts", ".txt", ".Dockerfile"}
            and not p.is_relative_to(root / "src/live2d_pipeline/web")
        }
        key = fingerprint(
            {
                "inputs": inputs,
                "config": self.config.data,
                "implementation": implementation,
                "upstreams": read_json(root / "upstreams.lock.json"),
                "models": read_json(root / "models.lock.json"),
                "model_files": self.model_identity(),
            }
        )
        old = revision.stages.get(name)
        if (
            old
            and old.state == "complete"
            and old.fingerprint == key
            and all(
                (directory / p).is_file() and digest(directory / p) == sha for p, sha in old.artifacts.items()
            )
        ):
            return directory / name
        # Stages are isolated directories belonging to this revision, never external source paths.
        target = directory / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir()
        revision.stages[name] = Stage(fingerprint=key, state="running")
        self.store.save(revision)
        try:
            action(target)
            files = {str(p.relative_to(directory)): digest(p) for p in target.rglob("*") if p.is_file()}
            if not files:
                raise RuntimeError(f"{name} produced no artifacts")
            revision.stages[name] = Stage(fingerprint=key, state="complete", artifacts=files)
        except BaseException as exc:
            revision.stages[name] = Stage(
                fingerprint=key, state="failed", error=str(exc) or type(exc).__name__
            )
            self.store.save(revision)
            raise
        self.store.save(revision)
        return target

    def context(self, revision):
        directory = self.store.revision_dir(revision.id)
        result = {"previous_request": revision.plan.request, "previous_prompt": revision.plan.prompt}
        for key, path in [
            ("parts", directory / "materials/parts.json"),
            ("rig", directory / "rig/rig-context.json"),
        ]:
            if path.exists():
                result[key] = read_json(path)
        return result

    def run(self, revision, until="rig"):
        if until not in {"image", "materials", "rig"}:
            raise ValueError("until must be image, materials, or rig")
        if revision.plan.clarification or revision.plan.unsupported:
            raise ValueError(revision.plan.clarification or "; ".join(revision.plan.unsupported))
        if revision.status == "accepted":
            raise ValueError("Accepted revisions are immutable; create a new revision")
        directory = self.store.revision_dir(revision.id)
        parent = self.store.get(revision.parent) if revision.parent else None
        operations = revision.plan.operations
        regenerate = any(o.kind == "regenerate" for o in operations)
        inherit = parent is not None and revision.source is None and not regenerate
        if revision.reference and (revision.source or inherit):
            raise ValueError("此方案的参考图不会被使用，请移除冲突输入后重新规划。")
        if revision.plan.refinements and not inherit:
            raise ValueError("Refinements require an existing completed model")
        if inherit and until == "image":
            raise ValueError("Art edits run to materials; inherited revisions have no image stage")
        if inherit and parent.status not in {"built", "accepted"} and not revision.prepared_materials:
            raise ValueError("Build the parent revision first")
        # Reject model-dependent work before changing status or replacing stage artifacts.
        model_work = any(op.kind in {"repaint", "add", "regenerate"} for op in operations)
        model_work |= any(edit.action == "split" for edit in revision.plan.refinements)
        if not revision.prepared_materials and not inherit:
            model_work |= not revision.source
            model_work |= bool(revision.source and not revision.source.endswith(".psd") and until != "image")
        if model_work:
            self.config.require_local_models("立绘生成、自动拆层或模型修图")
        revision.status = "running"
        self.store.save(revision)
        try:
            if revision.prepared_materials:
                from .repair import copy_materials, material_files

                prepared = directory / revision.prepared_materials
                art_key = material_files(prepared)

                def produce(target):
                    copy_materials(prepared, target)
            elif inherit:
                parent_dir = self.store.revision_dir(parent.id)
                parent_parts = parent_dir / "materials"
                art_key = {
                    str(p.relative_to(parent_parts)): digest(p)
                    for p in parent_parts.rglob("*")
                    if p.is_file()
                }

                def produce(target):
                    shutil.copytree(parent_parts, target, dirs_exist_ok=True)
                    self.modify(target, operations)
                    refinement.apply(self.config, target, revision.plan.refinements)
            else:
                source = directory / revision.source if revision.source else None
                reference = directory / revision.reference if revision.reference else None

                def image_action(target):
                    if source:
                        shutil.copyfile(source, target / ("input" + source.suffix))
                        return
                    prompt = next(
                        (o.instruction for o in operations if o.kind == "regenerate"), revision.plan.prompt
                    )
                    if regenerate and parent:
                        prior = self.store.revision_dir(parent.id) / "materials/composite.png"
                        comfy.image_job(
                            self.config,
                            prompt,
                            revision.plan.negative,
                            target / "input.png",
                            target / "generation.log",
                            image=prior,
                            reference=reference,
                        )
                    else:
                        comfy.image_job(
                            self.config,
                            prompt,
                            revision.plan.negative,
                            target / "input.png",
                            target / "generation.log",
                        )
                        if reference:
                            comfy.image_job(
                                self.config,
                                "Preserve Picture 2 character identity. " + revision.plan.prompt,
                                revision.plan.negative,
                                target / "reference-edit.png",
                                target / "reference.log",
                                image=target / "input.png",
                                reference=reference,
                            )
                            (target / "reference-edit.png").replace(target / "input.png")

                gen = self.stage(
                    revision,
                    "image",
                    {
                        "plan": revision.plan.model_dump(),
                        "source": digest(source) if source else None,
                        "reference": digest(reference) if reference else None,
                    },
                    image_action,
                )
                if until == "image":
                    revision.status = "paused"
                    self.store.save(revision)
                    return directory
                psds = list(gen.glob("*.psd"))
                if psds:
                    layered = psds[0]
                else:
                    source_image = next(
                        p for p in gen.glob("input.*") if p.suffix in {".png", ".jpg", ".jpeg"}
                    )

                    def split(target):
                        result = decompose.decompose(
                            self.config, source_image, target / "raw", target / "decompose.log"
                        )
                        shutil.copyfile(result, target / "input.psd")

                    split_dir = self.stage(revision, "decompose", digest(source_image), split)
                    layered = split_dir / "input.psd"
                art_key = digest(layered)

                def produce(target):
                    assets.normalize(layered, target)
                    if source is None or source.suffix.lower() != ".psd":
                        assets.filter_unsupported_parts(
                            target, source_image, split_dir / "raw" / source_image.stem / "src_img.png"
                        )
                        assets.prepare_generated_face(target, revision.plan.prompt)
                    self.modify(target, [o for o in operations if o.kind != "regenerate"])

            materials = self.stage(
                revision,
                "materials",
                {
                    "art": art_key,
                    "operations": [o.model_dump() for o in operations if o.kind != "rig"],
                    "refinements": [e.model_dump() for e in revision.plan.refinements],
                },
                produce,
            )
            report = assets.quality(materials)
            if report["missing_parts"] or report["errors"]:
                write_json(materials / "quality.json", report)
                revision.stages["materials"].artifacts = {
                    str(p.relative_to(directory)): digest(p) for p in materials.rglob("*") if p.is_file()
                }
                revision.status = "paused"
                self.store.save(revision)
                return directory
            if until == "materials":
                revision.status = "paused"
                self.store.save(revision)
                return directory
            # Reuse editable workspace only when source art is unchanged. Structural edits rebuild and replay.
            changed_art = (
                not inherit or bool(revision.prepared_materials) or bool(revision.plan.refinements) or any(o.kind != "rig" for o in operations)
            )
            rig_ops = [o for o in operations if o.kind == "rig"]
            parent_workspace = None
            if inherit:
                if changed_art:
                    from .repair_rig import history_operations, safe_replay

                    historical_ops = history_operations(self.store, parent)
                    replay, stale = (
                        safe_replay(self.store, parent, materials, historical_ops)
                        if historical_ops else ([], [])
                    )
                    # Current operations target the newly built rig. Only historical
                    # geometry is checked against the previous material coordinates.
                    rig_ops = replay + rig_ops
                    write_json(directory / "rig-replay.json", {"needs_review": stale})
                else:
                    parent_workspace = self.store.revision_dir(parent.id) / "rig/source.psd2live"

            def build(target):
                assets.write_psd(materials, target / "source.psd")
                asyncio.run(rig.build(self.config, target / "source.psd", target, rig_ops, parent_workspace))
                write_json(target / "validation.json", validate_bundle(target / "bundle"))

            self.stage(
                revision,
                "rig",
                {
                    "parts": revision.stages["materials"].artifacts,
                    "ops": [o.model_dump() for o in rig_ops],
                    "parent": digest(parent_workspace) if parent_workspace else None,
                },
                build,
            )
            revision.status = "built"
            self.store.save(revision)
        except BaseException:
            revision.status = "failed"
            self.store.save(revision)
            raise
        return directory

    def modify(self, directory, operations):
        manifest = assets.load_parts(directory)
        for index, op in enumerate(operations):
            if op.kind == "rig":
                continue
            parts = {p.id: p for p in manifest.parts}
            if op.kind in {"recolor", "repaint", "remove"} and op.target not in parts:
                raise ValueError(f"Unknown/ambiguous part: {op.target}")
            part = parts.get(op.target)
            if op.kind == "remove":
                manifest.parts.remove(part)
                (directory / part.image).unlink()
            elif op.kind == "recolor":
                with Image.open(directory / part.image) as im:
                    result = assets.recolor(im, op.color)
                result.save(directory / part.image)
            elif op.kind == "repaint":
                path = directory / part.image
                original = Image.open(path).convert("RGBA")
                output = directory / f"edit-{index}.png"
                comfy.image_job(
                    self.config,
                    op.instruction + " Preserve the silhouette and placement.",
                    "",
                    output,
                    directory / f"edit-{index}.log",
                    image=path,
                    reference=directory / "composite.png",
                )
                with Image.open(output) as edited:
                    result = assets.preserve_outside(original, edited, original.getchannel("A"))
                result.save(path)
            elif op.kind == "add":
                if op.semantic not in assets.SEMANTICS:
                    raise ValueError(f"Unknown new-part semantic: {op.semantic}")
                x, y, w, h = op.bounds
                if x + w > manifest.width or y + h > manifest.height:
                    raise ValueError("New part exceeds canvas")
                pid = f"{op.semantic.replace(' ', '_')}_added_{index}"
                ordinal = index
                while pid in parts:
                    ordinal += 1
                    pid = f"{op.semantic.replace(' ', '_')}_added_{ordinal}"
                output = directory / f"{pid}.png"
                generated = directory / f"{pid}-generated.png"
                matte = directory / f"matte-{index}.png"
                Image.new("RGB", (max(w, 64), max(h, 64)), (0, 255, 0)).save(matte)
                instruction = (
                    op.instruction
                    + " Draw only this isolated part, centred with empty margins, on pure bright green #00FF00 background, matching Picture 2 style. Do not draw surrounding body parts or text."
                )
                if op.semantic in {"mouth_open", "mouth_close"}:
                    instruction += (
                        " The complete mouth, lips and teeth must fit inside the central half of the image width"
                        " and central 30 percent of its height. Do not include a nose, chin, neck or cheeks."
                    )
                comfy.image_job(
                    self.config,
                    instruction,
                    "",
                    generated,
                    directory / f"add-{index}.log",
                    image=matte,
                    reference=directory / "composite.png",
                )
                if op.semantic in {"mouth_open", "mouth_close"}:
                    segment.mouth(self.config, generated, output, (w, h), directory / f"segment-{index}.log")
                else:
                    im = Image.open(generated).convert("RGBA")
                    # Key before resizing so transparent green pixels cannot bleed into the edge.
                    alpha = Image.new("L", im.size)
                    alpha.putdata(
                        [
                            0 if g > 160 and g > r * 1.4 and g > b * 1.4 else 255
                            for r, g, b, _ in im.get_flattened_data()
                        ]
                    )
                    if alpha.getextrema() != (0, 255):
                        raise RuntimeError(
                            "Generated new part has no usable green matte; correction required"
                        )
                    im.putalpha(alpha)
                    im.resize((w, h), Image.Resampling.LANCZOS).save(output)
                manifest.parts.append(
                    Part(
                        id=pid,
                        name=op.semantic,
                        semantic=op.semantic,
                        order=max((p.order for p in manifest.parts), default=-1) + 1,
                        bounds=op.bounds,
                        image=output.name,
                    )
                )
            assets.save_parts(directory, manifest)
            assets.assemble(directory, directory / "composite.png")
        write_json(directory / "quality.json", assets.quality(directory))
