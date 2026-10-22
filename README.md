# Local AIGC → Live2D

[English](README.md) | [简体中文](README.zh-CN.md)

A Live2D production skill, local web workbench, and command-line toolkit: requirements → image generation → layer decomposition and inpainting → rigging → inspection and repair → a `.moc3` model package. Supports natural-language revisions, revision branches, resuming interrupted work, and export.

Real planning, image generation, image editing, layer decomposition, mouth-shape extraction, headless rigging, and model-package export have been completed on the local machine. Deletion and parameter changes requested in Chinese, rebuilding after recoloring, and resuming interrupted work have also been verified. Closed-mouth, open-mouth, blinking, and slight head-turn samples of the silver-haired candidate have been inspected; automated visual quality still needs review, and compatibility with the official Core and target applications has not been confirmed. See the [actual run log](docs/runs/2026-09-12.md).

## Codex Production Skill

The entry point is [`skills/live2d-production/SKILL.md`](skills/live2d-production/SKILL.md). Codex plans production, inspects actual images, and chooses repairs; Python tools execute changes, manage revisions, and validate results. The skill is versioned with this repository. Link its directory into your local Codex skills directory to invoke `$live2d-production`. It requires this repository and a prepared runtime; model weights and character assets are not included.

For example: **“Use $live2d-production to turn this illustration into a Live2D model with blinking, mouth opening, and small head turns. Inspect intermediate results, repair visible problems, and deliver the model.”**

Tool interfaces (run inside `nix develop`):

```sh
# Inspect saved images, parts, parameters, materials, and artifact hashes without inference
uv run --no-sync l2d inspect projects/my-character
uv run --no-sync l2d schema plan
uv run --no-sync l2d schema repair-edit
# Create an added-layer draft from a local image without a web upload
uv run --no-sync l2d repair create projects/my-character --mode add --source /absolute/mouth.png
uv run --no-sync l2d repair --help
# pose.json: {"parameters":{"ParamAngleX":15},"long_edge":768}
uv run --no-sync l2d render projects/my-character /absolute/pose.json
uv run --no-sync l2d preview-video projects/my-character
```

`repair` supports create/show/list/update/run/evidence/record-review/apply/discard. It reuses the web workbench's drafts, version locks, and pixel/geometry checks, and copies local images into drafts. `evidence` prepares hashed inspection images; after viewing them, Codex records an attributed judgment with `record-review`. CLI application requires a current visual review. Stale or changed evidence cannot be applied, and agent review does not mark human acceptance. See the [tool reference](skills/live2d-production/references/tools.md) for interfaces and coordinates.

**Agent mode is the default.** Codex handles planning, image inspection, and candidate comparison. **Use the agent's built-in image tools for generation, editing, and hidden-surface fill**, unless the user explicitly selects another tool or service; do not assume a particular underlying model. Import actual results through `--plan-file`, layered PSDs, or repair add/replace. **Use local AI models only when the user explicitly requests a fully local run.** Report unavailable tools or failures without automatically switching providers or falling back to local models.

The shared default is `[execution] mode = "agent"` in `configs/local.toml`; older configurations without a mode also default to agent. For a fully local task, copy the configuration into the task directory, set `mode = "local"`, and pass `--config /absolute/full-local.toml`. Do not change the shared default for one task. `inference = "container"/"native"` selects how local backends run; it does not enable local models. `doctor` reports the mode.

This restriction covers the local planner/VLM, Animagine, Qwen Image Edit, See-through, SAM2, and LaMa. PSD processing, pixel checks, transparent-island extraction, rigging, rendering, and export still use local programs. Automatic PNG decomposition currently has only a See-through implementation, with no hosted adapter. In agent mode, use an existing layered PSD or assemble and inspect layers produced by the selected image tool when feasible. Otherwise report the missing capability instead of silently loading a local model.

The skill inspects and repairs in stages. Missing layers preserve materials and return `paused` until explicitly supplied. Structural checks, visual review, official runtime validation, and user acceptance are recorded separately; exposing interfaces alone does not establish improved production quality.

Detailed production references cover [reference goals and task planning](skills/live2d-production/references/detailed-production.md), [components and artwork fidelity](skills/live2d-production/references/components.md), [parameters and deformation](skills/live2d-production/references/parameters.md), and [expressions, actions, and physics](skills/live2d-production/references/performance.md). The skill selects relevant extensions and maps goals to actual components, controls, and acceptance evidence.

Available authoring tools:

- Plans support `parameter_create`, `warp_create`, and `object_edit` to create controls, hierarchies, and keyforms in order. `rig-inspect` reads complete objects and paged geometry.
- `materials-batch` captures multiple native-size PNGs, checks placement, draw order, semantics, and binding, and applies them together after preview and review. Failure leaves the project head unchanged.
- `performance` manages immutable expression, motion, and physics snapshots. New model revisions pin the current configuration; normal `export` registers and packages it. Playback supports timelines, expression composition, looping, and actual MP4 previews.
- `physics compose/put` authors complete multi-particle physics3 chains. `performance preview --physics` runs continuous CPU simulation; `verify-playback` checks parameter traces frame by frame through a supplied official Core library.

```sh
uv run --no-sync l2d materials-batch schema
uv run --no-sync l2d rig-inspect projects/my-character --object OBJECT_ID --kind warp --detail object
uv run --no-sync l2d performance schema playback
uv run --no-sync l2d physics schema authoring
uv run --no-sync l2d performance preview projects/my-character /absolute/playback.json --physics
uv run --no-sync l2d verify-playback /absolute/delivery/model /absolute/parameters.json --core /absolute/libLive2DCubismCore.so
```

See the [capability table](skills/live2d-production/references/tools.md#authoring-capabilities) for arguments and version workflows. Motions currently support parameter curves, without Model/PartOpacity curves or audio/event dispatch. CPU physics uses an independent solver and does not promise official SDK numerical parity. Core checks cover sequential parameter updates and finite geometry; target-application visuals and interaction still require actual testing.

See the [2026-10-15 production skill validation](docs/runs/2026-10-15-production-skill.md) and [authoring tools and combined playback validation](docs/runs/2026-10-15-authoring-tools.md).

## Web Interface

Once the local environment is ready, run these commands in the project directory. For fully local tasks requiring autonomous web planning and inference, also pass `--config /absolute/full-local.toml`:

```sh
cd /home/kurage/Projects/live2d
nix develop
uv sync --locked
uv run l2d web
```

Open **http://127.0.0.1:7860**. Use `uv run l2d web --port 7861` to change the port and `--config` to specify a local configuration. The service listens only on the local machine and requires no Node.js, frontend build, or online service. Press Ctrl+C in the terminal to stop it. The Nix environment files are in the repository root. Use `nix develop` (do not use `path:.`); Nix uses Git-tracked files as inputs, while models, runtime environments, and generated artifacts are excluded by `.gitignore`. Run `git add` after creating or modifying flake files.

- Existing projects under `projects/` are loaded automatically. You can also create a project in the web interface and upload PNG/JPEG character illustrations, layered PSDs, or reference images (up to 500 MB per file).
- Enter requirements in Chinese and choose whether to plan only, generate an image first, stop at materials, or generate a complete model. For an unfinished revision, load its current requirements, edit them into **a complete new set of requirements**, and plan again. The new and old plans share the same base revision, and the old artifacts are retained.
- View the original illustration, the composite of the decomposed layers, individual layers, and pose samples from the rigging output. Previews are actual CPU-rendered images, not a real-time Live2D player in the browser.
- Continue to a complete model after inspecting the illustration or materials. After a failure, retry work following the completed stages. Existing stage-cache and validation rules still apply, and the entire failed stage may run again.
- Submit changes to completed revisions, view history, switch the starting revision for further work, accept revisions, and validate and download a ZIP containing the model and source materials.
- Tasks and logs are saved in `.runtime/web/`; refreshing or closing the browser does not stop tasks. The web interface runs one task at a time and shares a task lock with the CLI. The stop button and shutting down the web service terminate the model processes they own while preserving completed stages. Tasks interrupted by an unexpected service shutdown are shown as interrupted; inference does not restart automatically.

Starting the web interface, uploading materials, and viewing previews do not load models. Submitting planning or generation operations may use the GPU. The web interface reuses the existing Starlette, Uvicorn, Pydantic, and project pipeline; it does not install additional generation backends, and interface changes are not included in model-stage cache fingerprints.

## Local Environment and Checks

Configured for an RTX 2080 Ti 22GB / 32GB RAM system. Planning uses local `llama-cpp` and Qwen3-4B-Instruct-2507 Q4_K_M (the non-thinking version, with approximately 2.33 GiB of weights), at this model path:

```
models/planner/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
```

The following operations **do not load models or start services**:

```sh
nix develop
uv sync --locked
uv run l2d doctor
uv run pytest -q
uv run ruff check .
uv run l2d init projects/demo
uv run l2d plan projects/demo '测试角色' --plan-file configs/example-plan.json
uv run l2d status projects/demo
```

`plan` without `--plan-file` requires fully local mode; agent mode refuses to start the local planner. In fully local mode, `run`, `resume`, and `revise` may use the GPU. Importing code, entering the Nix shell, running `doctor`, `init`, `history`, or `checkout`, and packaging existing artifacts do not start models.

## Fully Local Inference Setup

```sh
uv run l2d prepare-sources
uv run l2d install-environments
uv run l2d download-models
```

These three commands prepare pinned source versions, software environments, and model weights, respectively; none implicitly calls the others. They do not install Cubism Editor. `upstreams.lock.json`, `models.lock.json`, `uv.lock`, and `flake.lock` record sources and versions. Model weights, including the 4B planning model, total approximately 39.8 GiB; additional space is needed for containers, build caches, and artifacts. You can point `paths.models` / `paths.runtime` in `configs/local.toml` to another local disk. Update `planner.model` as well when moving the models.

Separate ComfyUI / See-through containers are used by default to avoid differences in dynamic libraries on NixOS. Docker and NVIDIA Container Toolkit with CDI configured are required (the GPU is only needed when inference is later run). Java 21 modeling and `llama-server` use the flake environment. Set `execution.inference = "native"` to use separately isolated Python environments; in this mode, the same installation command creates `.runtime/envs/` and still does not load models.

Once the local models and dependencies are ready, Hugging Face offline mode is enabled at runtime. `llama-server` uses only the configured local file and does not download a substitute model. The language model is terminated after planning, before the image service starts; each image service exits when its task completes. The 4B model reduces loading and planning overhead, while still freeing GPU memory for image models after planning. Only processes started by this pipeline or containers with distinct random names are terminated; other GPU tasks are neither unloaded nor terminated.

On the 2080 Ti, image generation and layer decomposition use FP16; Qwen editing uses quantized weights with FP32 computation. In actual tests, FP16 editing overflowed in the first image MLP and produced black images. A full 28-step edit in FP32 worked normally; correcting the background of a single image took about 18 minutes, substantially slower than image generation. See the run log for detailed results. Actual container dependency versions are recorded in `containers/*-requirements.lock.txt`.

The 4B planner passed seven real Chinese-language scenario checks, with peak process GPU memory usage of approximately 4.84 GiB. New plans took about 3 seconds; revisions with full project context took about 7–8.5 seconds, including loading and shutdown. Material ID mix-ups and prompt-format issues have been fixed. See the [small-model evaluation log](docs/runs/2026-09-12-small-planner.md).

## Fully Local Usage

These examples are for explicitly requested fully local tasks. Copy the configuration, set `[execution] mode = "local"`, and pass that task configuration. See the skill section above for the default agent workflow.

```sh
# Create a plan from natural language; this runs only the planning model
uv run l2d plan projects/demo '银发蓝眼，白色外套，正面半身二次元角色' --config /absolute/full-local.toml
# Generate an image first and pause to inspect the composition
uv run l2d run projects/demo --until image --config /absolute/full-local.toml
# Inspect the illustration and continue when satisfied; use --until materials to inspect layers before rigging
uv run l2d resume projects/demo --config /absolute/full-local.toml

# You can also start from existing materials: PNGs are decomposed, while PSDs proceed directly to material normalization
uv run l2d init projects/imported
uv run l2d plan projects/imported '保留角色，生成基础动作' --source /absolute/character.psd --config /absolute/full-local.toml
uv run l2d run projects/imported --config /absolute/full-local.toml

# Create a revision branch; by default, this only plans and does not edit images
uv run l2d revise projects/demo '把前发改成浅蓝色' --config /absolute/full-local.toml
uv run l2d run projects/demo --config /absolute/full-local.toml
# Or plan and execute in one command
uv run l2d revise projects/demo '减小头发摆动幅度' --execute --config /absolute/full-local.toml

uv run l2d history projects/demo
uv run l2d checkout projects/demo REVISION_ID
uv run l2d resume projects/demo --config /absolute/full-local.toml
# Accept after viewing the preview; accepted revisions cannot be rerun and overwritten
uv run l2d accept projects/demo REVISION_ID
uv run l2d export projects/demo /absolute/delivery
```

Use `--config /absolute/config.toml` to specify the runtime configuration. `plan --reference` accepts a local reference image and uses Qwen editing after initial image generation to constrain the appearance according to the reference; it does not guarantee identity consistency.

## Revision and Failure Behavior

- Supports recoloring, local inpainting within a fixed outline, adding and removing parts, permitted rigging-parameter/keyform operations, and full-image regeneration. The planner produces only Pydantic-validated operations; it does not execute shell commands.
- The planner uses a 16384-token context containing only text and project metadata, without claiming visual understanding. It outputs `clarification` when the object or location is unknown. Complex motions and parts that cannot be located safely may require a more specific description.
- Recoloring preserves alpha and dimensions. Inpainting uses the original alpha as its bounds and preserves the original transparency; outline changes require regeneration/re-rigging. New parts use explicit green-screen generation and background removal, which suits non-green materials. Green parts or unclean edges require manual correction.
- New mouth shapes use SAM 2.1 Tiny box/point prompts to extract the central mouth region, then place it at the specified location while preserving its aspect ratio. If no reliable standalone mouth shape is obtained, processing stops and retains the original image, candidate masks, and segmentation report, avoiding shrinking the lower half of the face into a mouth patch. Other parts have their green background removed before resizing.
- Material changes trigger re-rigging, and **preserving manually refined meshes is not currently guaranteed**. Corrections are replayed only when their part, layer-order, rigging, and parent-geometry prerequisites still match; the rest are marked for reinspection. Changes limited to pose rigging copy the parent revision's entire workspace and edit it through MCP.
- If required mouth or eye layers are missing, the pipeline preserves materials and pauses. Supply them explicitly through the web interface or `l2d repair`; `run` does not start hidden planning/inpainting tasks. The skill schedules bounded repairs based on inspected images.
- For white-background PNG decomposition, the pipeline compares against the aligned original image saved by the upstream tool, filters hallucinated parts lying almost entirely outside the character outline, records `source-support.json`, and retains the original PSD. This check cannot detect all semantic errors and is not applied to non-white backgrounds or existing PSDs.
- Each revision saves copies of source inputs, its plan, dependency hashes, and stage artifacts. Resuming validates artifacts and fingerprints. Accepted revisions remain unchanged, and failed modifications do not alter them.
- A single-task lock prevents this pipeline from running multiple large-model tasks at once; each workspace also has an exclusive lock. This tool does not manage external GPU usage.

## Artifacts

```
projects/demo/
  project.json                 # Current branch and accepted revision
  revisions/<id>/
    revision.json              # Requirements, parent revision, status, stage summaries
    image/                     # Original image or imported materials
    decompose/                 # See-through intermediate results (PNG workflow)
    materials/                 # Stable part IDs, PNGs, composite.png, inspection reports
    rig/
      source.psd
      source.psd2live           # Resumable PSD2Live workspace
      rig-context.json          # Actual objects, parameters, permitted MCP tool definitions
      pose-*.png                # CPU-rendered pose samples
      bundle/                   # MOC3, CMO3, textures, JSON
      validation.json           # Structural checks; not equivalent to runtime acceptance
```

Production dependencies are not distributed with this repository; see the [dependency notes](docs/dependencies.md) for licenses. See the [acceptance procedure](docs/acceptance.md) for actual model acceptance testing.

## Part Refinement and Visual Inspection

In the web interface, first choose an operation: “Generate or modify appearance,” “Refine parts and motions,” or “Inspect only, without modifying,” then describe the requirements in Chinese. Refinement generates an editable correction draft; a formal revision is created only after you inspect the candidate and click “Apply.” Inspection does not modify the model. Existing plans have a separate stage selector, allowing you to stop at the illustration, materials, or model in sequence. Inspection feedback and individual layers can both open the part-correction workspace. “Mark manual acceptance as passed” does not change the starting revision for further work; use the separate button in revision history to switch the starting revision. Tasks and downloads always correspond to the revision currently being viewed.

```sh
# Generate a decomposition/rigging draft → rule validation → visual review; apply in the web interface
uv run l2d refine projects/my-character '把领结独立拆出来，根部固定，尾部小幅摆动' --config /absolute/full-local.toml
# Inspect only, without modifying the project; optionally include natural-language feedback
uv run l2d review projects/my-character '检查转头和闭眼时有没有穿帮' --config /absolute/full-local.toml
```

The vision model is **Qwen3.5-9B Q4_K_M + F16 vision projector**, with a separate role from the 4B text planner. It starts and releases its resources as needed. Weights reside in the main Hugging Face cache, with symlinks under the project's `models/vision/`. `l2d download-models` downloads the pinned versions; configure a local llama-server in the `[vision]` section of `configs/local.toml`. Localization within a single image uses normalized 0–1000 coordinates, which the program converts to source-layer pixels, avoiding confusion between coordinates for the full character and an individual layer.

The fully local extraction executor reuses SAM2 to extract source pixels, uses the VLM to verify that the extracted region is the intended part, then uses the existing anime LaMa to inpaint the occluded region underneath. Extracted parts retain provenance records and their original semantic rigging; the default composite must match pixel for pixel. New rigging uses PSD2Live's separate child deformers, swing keyframes with fixed roots, and two-node physics groups, and can add visibility switches to existing parts. Clothing, hair, earrings, headwear, and other accessories are supported. Use the authoring tools above for further hierarchy editing and multi-particle physics chains. Inspect appearance after edits across parent coordinate systems; automatic outfit-change design is not implemented.

Previews cover the parameter endpoints and some combinations for existing head turns, head pitch, gaze, eyebrows, mouth, hair, and new parts. The program checks file integrity, materials, the neutral composite, and triangle flipping/collapse in new deformations. The VLM checks semantics and outlines in images, without substituting confidence scores for pixel evidence. Correction suggestions are limited to operations permitted by the current type. Once a single part has been located unambiguously, a bounded set of candidates can be reviewed and compared. If localization is ambiguous, the user selects the part on the image; if no judgment can be made, the evidence is retained. The original revision remains unchanged.

### Part-Correction Workspace

1. Select a part and correction type, then create a draft. Supported operations include extracting a separate part, repairing masks, replacing or adding PNG/JPEG images, local inpainting, attachment and swing, and correction of a specified pose.
2. Adjust masks using inclusion points, exclusion points, box selection, and a brush/eraser. Switch among SAM2 candidates, zoom and pan, and undo/redo. Hand-painted regions override the segmentation result at the end and are not discarded by resegmentation. Disconnected islands of nontransparent pixels are split directly. Edit the extraction outline and the cleanup region underneath separately; the latter can be expanded and has its own brush/eraser, with a choice of LaMa completion or local semantic inpainting guided by text requirements. By default, composite pixels must match exactly. Changes to the original illustration within the cleanup region are allowed only when “Allow repainting regions visible in the original illustration” is explicitly enabled; pixels outside the region must still remain identical.
3. Use “Check inclusion/exclusion regions” to build a named checklist. The program checks coverage and contamination, while the VLM provides an independent semantic assessment. Regions suggested by the model require the “Regions verified” checkbox. After importing an image, select an explicit category (including open-mouth/closed-mouth shapes), set canvas X/Y/width/height, and inspect the composite preview.
4. For pose correction, load the pose first, then mark problematic areas, fix attachment points, drag mesh points, and select the scope of influence. The backend uses the upstream deformation evaluator to solve inversely for parent-local coordinates; it rejects cases that are noninvertible, out of bounds, or have mapping errors greater than 0.25 pixels. After execution, it checks the actual destination error of moved points; a result cannot be applied if the error exceeds 0.5 pixels. Control points at part boundaries are also validated. Candidates are checked at the default pose, problematic pose, parameter endpoints, and intermediate values, with foldovers, collapse, and fixed-point drift recorded.
5. “Preview rigging effect” builds a complete candidate. “Apply” creates a new revision and reconnects it to pose previews, video, and export. Corrections can still be saved when required materials such as mouth shapes are missing; rigging pauses and export is disabled until the missing materials are supplied. If visual inspection fails or requires confirmation, correct and review the result again, or explicitly confirm the candidate after examining the evidence. Failures of hard program rules cannot be bypassed with a manual checkbox. Final model acceptance remains the user's responsibility.

For local inpainting, start with an empty mask and paint the region to modify. The program crops the region and surrounding context, uses the original local image as the style reference, samples at a longest edge of 768, uses the existing Qwen Image Edit model, and feeds the mask into ComfyUI's `SetLatentNoiseMask`. After generation, it restores the original dimensions and protects the original alpha and pixels outside the mask. It does not expand the material's silhouette. Generation inputs, masks, and crop coordinates are saved with the result for inspection. By default, part inpainting does not additionally encode the full character image; ordinary full-image editing and the optional second-reference-image interface retain their existing behavior.

Decomposition with inpainting, local inpainting, and rigging corrections support “Automatically try and compare candidates,” up to three candidates, while still running model tasks serially. Material candidates try the current settings, expanded cleanup context, and semantic inpainting in that order; inpainting candidates compare different seeds; rigging searches only bounded amplitudes or fixed ranges. Candidates that fail rules do not enter visual selection. The VLM examines the overall image, enlarged views of the underlying layer, and material exposure probes. Exposure probes are composites made by shifting layers and are not presented as actual rigging or continuous physics validation. For rigging, it examines overall and local before/after comparisons at actually sampled poses.

Each candidate is an independent draft. Its card separately shows program results, VLM judgment, and elapsed time; you can open it, continue editing, and return to the comparison. The default search budget is 600 seconds per run; no new steps start once it is reached. Steps already in progress may exceed the budget and can be canceled with the task stop button. Repeating a search reuses completed results and retries transient failures. Candidates that have been manually edited, applied, or discarded are marked stale and are not recommended using old evidence. Without a clear improvement or baseline evidence, no recommendation is made and the current model revision is not changed.

Drafts are saved in `projects/<project>/repairs/<id>/`, including source images, base materials, annotations, candidates, and inspection evidence, and can be reopened after refreshing. Tasks are tied to the draft revision and input hashes; stale results are not published. Base-revision conflicts require switching back to the original starting revision first. Individual part-processing steps use hash-based caches, so failed retries reuse completed steps. SAM2 image encodings are stored on disk, and editing in the web interface does not keep models resident in GPU memory. The API provides creation/listing at `/api/projects/<project>/repairs` and reading/updating/discarding of individual drafts; execution consistently uses the `repair` action under `/jobs`.

For actual correction acceptance records, see the [2026-09-18 correction loop](docs/runs/2026-09-18-repair-loop.md) and [2026-09-19 local inpainting and candidate improvements](docs/runs/2026-09-19-repair-improvements.md).

The fixed regression checklist is in `docs/repair-regression-cases.json`. By default, `nix develop --command uv run python scripts/repair_regression.py` checks only locally saved inputs and does not start models. Adding `--execute` explicitly runs the cases serially in independent drafts; it does not apply formal revisions. Reports separately record program checks, VLM judgments, candidate counts, elapsed time, and the number of source-draft saves (not mouse gestures), and distinguish actual defects, operation regressions, and cases lacking a baseline.

This remains an incremental automation feature with limited capabilities: VLM/SAM may fail to locate fine strands of hair, LaMa's occlusion inpainting needs inspection in motion poses, and static pose images cannot test the temporal response of physics. Passing inspection does not mean user acceptance, nor does it imply validation with the official Live2D Core or target software.

After the model is built, click **Generate preview video** in the preview area to generate an 8-second, 24 fps MP4 with a longest edge of 512 pixels; once complete, it can be played or downloaded directly. The video shows parameter animation for head turns, blinking, mouth opening, the body, and rigged parts. It does not simulate continuous physics or verify compatibility with the official runtime. Rendering reuses PSD2Live's CPU renderer and FFmpeg without loading image-generation models. Progress is visible and tasks can be stopped. Results are saved per revision at `projects/<project>/previews/<revision>/preview.mp4` and replaced after successful regeneration, without changing the original model revision.
