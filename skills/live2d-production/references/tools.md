# Tool contracts

Run these examples in the prepared repository shell (`nix develop`), or prefix a
command with `nix develop --offline --command`. Use absolute paths from another
directory. `uv run --no-sync` uses the existing environment without an implicit install.

## Execution mode

Default `execution.mode = "agent"` forbids local AI model processes. Codex handles
planning/review; the agent's built-in image tools generate/edit images by default.
Use another image tool or provider only when the user explicitly specifies it.
An unavailable tool does not authorize switching providers or loading local models.
Only for a user-requested fully local run, make a job-specific
copy of `configs/local.toml`, set `[execution] mode = "local"` (keep its other fields),
and pass `--config /absolute/full-local.toml` to model-using commands. Missing mode
also defaults to agent. `doctor` reports the active mode and whether local models
are enabled. `nix develop --offline` concerns environment setup, not model policy.

Binding, rendering, pixel checks, add/replace/mask/bind repairs and transparent-island
splits do not need local AI. Automatic PNG decomposition, SAM segmentation, LaMa
fill, Qwen repaint and local planner/VLM calls require fully local mode. The current
code has no hosted decomposition adapter: use a prepared PSD or state that gap.

## Authoring capabilities

| Need | Current execution route | Boundary |
| --- | --- | --- |
| Detailed artwork/layers | Agent image tools, inspected PSD, repair add/replace/mask, atomic `materials-batch` | No agent-mode automatic flat PNG decomposition adapter |
| Per-component sway/visibility | Repair bind / `Binding.motion` and `Binding.toggle` | Generates component parameters; does not implement arbitrary facial controls |
| Parameter metadata | Plan rig `parameter_create`/`parameter_update` | Author actual keyforms separately |
| Existing-object deformation | Plan rig `keyform_set`/`rig_transform`, or rig repair draft | Use actual object IDs and supported channels/coordinates |
| Warp hierarchy / object editing | Plan rig `warp_create`/`object_edit`; read `rig-inspect` | Common Warp parent for creation; explicit local space for reparenting |
| Physics tuning | `physics compose/put`, managed full physics3 document; simple `physics_put` remains | Multi-particle inputs/outputs supported; CPU solver is not SDK-equivalent |
| Expressions/actions | `performance` snapshots, authoring, sampling, preview and normal export | Parameter curves; no Model/PartOpacity curves or event/audio dispatch |
| Continuous checks | `performance preview --physics`, `verify-playback --core` | CPU simulation and official Core trace geometry are separate; target app requires actual testing |

The current plan rig allowlist is `parameter_create`, `parameter_update`,
`warp_create`, `object_edit`, `rig_transform`, `keyform_set`, `physics_put`.
Their argument schemas are in the actual rig context. Do not infer
pipeline support from a tool found only in the upstream backend.

## Inspect and plan

```sh
uv run --no-sync l2d doctor
uv run --no-sync l2d history projects/character
uv run --no-sync l2d inspect projects/character
uv run --no-sync l2d inspect projects/character --revision REV --stage materials --part PART_ID
uv run --no-sync l2d rig-inspect projects/character --object OBJECT_ID --kind warp --detail object
uv run --no-sync l2d schema plan
uv run --no-sync l2d init projects/character
uv run --no-sync l2d plan projects/character '用户的完整需求' --source /absolute/layered-art.psd --plan-file /absolute/plan.json
uv run --no-sync l2d run projects/character --revision REV --until materials
uv run --no-sync l2d run projects/character --revision REV --until rig
```

Minimal import plan:

```json
{"request":"保留这张立绘，制作小幅转头、眨眼和张嘴动作","prompt":"front view, upper body, closed mouth","operations":[]}
```

Describe the actual mouth state accurately: PNG normalization uses the literal
`closed mouth` / `open mouth` phrases to select required mouth layers. PSD respects
its existing semantics. The prompt alone does not author motions.

`plan` without `--plan-file` requires fully local mode. In agent mode, `run` accepts
prepared PSD/materials and deterministic edits; it rejects model-dependent work
before replacing stage artifacts. In fully local mode it may start image/decomposition
models, but never invokes a text planner to repair missing layers.
Missing materials return `status: paused`; inspect `materials.quality`, add layers
through drafts, apply to materials and continue. Exit zero means execution succeeded,
not that a model is built or visually approved.

`inspect` is offline: absolute image paths, source-layer bounds, material checks,
saved rig parameters/context path and fresh stage-hash checks. Integrity failure
invalidates saved evidence. Existing `review` invokes a local VLM only in fully local mode.

Inherited revisions edit materials and have no image stage. Planning against an
unfinished revision creates a sibling from its completed base; use repair drafts
to preserve useful materials from an unfinished revision.

## Repair drafts

```sh
uv run --no-sync l2d repair create projects/character --revision REV --part PART_ID --mode replace --source /absolute/edited-part.png
uv run --no-sync l2d repair create projects/character --revision REV --mode add --source /absolute/mouth.png
uv run --no-sync l2d repair show projects/character DRAFT
uv run --no-sync l2d schema repair-edit
uv run --no-sync l2d repair update projects/character DRAFT /absolute/edit.json --version 1
uv run --no-sync l2d repair run projects/character DRAFT preview --version 2
uv run --no-sync l2d repair run projects/character DRAFT binding-preview --version 2
uv run --no-sync l2d repair evidence projects/character DRAFT --version 2
uv run --no-sync l2d schema visual-review
uv run --no-sync l2d repair record-review projects/character DRAFT /absolute/review.json --version 2
uv run --no-sync l2d repair apply projects/character DRAFT --version 2 --until rig
```

Use returned IDs/versions; example values are illustrative. `update` replaces the
**full edit** and increments its version. Start from `repair show`'s edit, change
intended fields and submit. Other steps publish results without incrementing the
edit version. An applied draft is immutable. `--source` is only for add/replace;
it copies the local PNG/JPEG and needs no web server or upload ID.

Modes: `split`, `mask`, `replace`, `add`, `repaint`, `bind`, `rig`.
Other run steps: `rig-context`, `underpaint`, `inspect`, `search`.
Only in fully local mode, `segment` uses SAM2 and preview may use LaMa/Qwen for split/repaint;
binding-preview/rig-context run the CPU rig process; inspect/search invoke the
local VLM and require fully local mode. Search obeys the draft's bounded candidate/time settings.
In default mode inspect/compare candidates with Codex and use evidence/record-review.

`evidence` prepares comparison/exposure images without inference. **View the
returned images** before writing this report using its real evidence ID:

```json
{
  "evidence_id": "COPY_THE_RETURNED_ID",
  "summary": "描述实际观察到的变化和限制",
  "items": [{"name":"具体检查项","status":"pass","reason":"指明图像和可见证据"}]
}
```

Statuses: `pass`, `fail`, `uncertain`. An agent report is explicitly attributed and
does not record human acceptance. Version, preview receipts and image hashes are
checked again at apply. After editing/rebuilding, regenerate evidence and review.
CLI apply requires a current review; hard pixel/geometry failures still block it.
Failed/uncertain visual judgments block normal application.

For incomplete models use `apply --until materials`, repair the next missing part,
then `run --until rig`. For changes to a completed model, generate and inspect
binding-preview when materials are complete. Apply promotes that exact preview
when available. Material changes can rebuild the rig: read `needs_review` and
`rig-replay.json` for old keyforms requiring reinspection.

`repair list` lists drafts; `repair discard ... --version N` retires a draft and
keeps its evidence. Apply requires the project head to match its base revision.
Inspect conflicts before checking out a different base; preserve other work.

## Coordinated layer assembly

```sh
uv run --no-sync l2d materials-batch schema
uv run --no-sync l2d materials-batch create projects/character /absolute/layers.json
uv run --no-sync l2d materials-batch preview projects/character DRAFT --version 1
uv run --no-sync l2d materials-batch evidence projects/character DRAFT --version 1
# View the returned images, then write the visual-review report with its evidence_id.
uv run --no-sync l2d materials-batch record-review projects/character DRAFT /absolute/review.json --version 1
uv run --no-sync l2d materials-batch apply projects/character DRAFT --version 1 --until rig
```

An edit has `request`, optional `canvas` for an empty project, and `operations`.
Each operation uses `action: add|replace|update|remove`, unique `id`, and appropriate
`source`, `placement: [x,y,width,height]`, `semantic`, `side`, `order`, `binding`.
Source PNG paths resolve relative to the edit JSON. Existing IDs come from inspect;
new IDs must be unique. Native PNG dimensions must match placement exactly.
Use `show/list/update/discard` to manage drafts. Updates replace the full edit and
invalidate evidence. A failed candidate leaves head unchanged. Material application
does not claim motion quality; inspect the rebuilt geometry and poses separately.

## Poses, animation and export

```sh
uv run --no-sync l2d schema render
uv run --no-sync l2d render projects/character /absolute/pose.json --revision REV
uv run --no-sync l2d preview-video projects/character --revision REV
uv run --no-sync l2d export projects/character /absolute/new-delivery --revision REV
```

Pose JSON (use actual parameter ranges):

```json
{"parameters":{"ParamAngleX":15,"ParamEyeLOpen":0},"long_edge":768}
```

Optional `region: [left,top,width,height]` uses full-model canvas pixels. Unspecified
parameters reset to defaults; unknown/out-of-range values are rejected. Transparent
PNG and JSON receipts go under `previews/<revision>/renders/`; the saved workspace
is copied before use. Match pose and viewport for before/after comparisons.

Video is the existing 8-second, 24-fps parameter preview outside immutable revisions.
It proves neither continuous physics nor official runtime compatibility. Export
requires a built revision and a new destination outside the store; accepted is not
required. `accept` is reserved for actual user acceptance. Copy preview/reports into
the delivery after export if needed, preserving model/source files.

For authored playback use `performance schema playback`, `performance preview`
and `performance sample`; `--physics` enables chain simulation. Use `physics schema
authoring`, `physics compose` and `physics put --version N` to prepare and store
multi-particle chains. See [performance.md](performance.md) for the full workflow.
Normal export automatically includes the revision's pinned library in `performance.json`
and registers its expression, motion and physics files in the copied model3.json.
`l2d verify-playback /absolute/export/model /absolute/parameters.json --core /absolute/libLive2DCubismCore.so`
checks every sampled frame through a supplied official Core library in a subprocess.
Alternatively set `CUBISM_CORE_LIBRARY` in the command environment and omit
`--core`; an explicit flag takes precedence. Follow
[runtime-validation.md](runtime-validation.md) for discovery and early/final checks.
This validates parameter references/ranges and finite geometry, not image quality,
official SDK physics parity or behavior in a target application.

New commands return JSON; validation/execution errors return `error` and `message`
with nonzero exit. Argument syntax errors use Typer diagnostics. Long jobs share
the task/project locks. Inspect status/draft before retrying interrupted apply: a
revision may already have been created.
