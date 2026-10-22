---
name: live2d-production
description: "Create, inspect, and improve Live2D models using the live2d-pipeline repository. Use for detailed component preparation, parameter and deformation design, expression presets, motion authoring, model repairs, and reference-based quality checks. Combines agent image tools with local l2d execution."
---

# Live2D production

Use Codex to plan and inspect; use `l2d` for state changes, inference jobs, binding
and rendering. Work within the user's scope. Assessment requests call for
inspection. Production requests authorize ordinary reversible stages and repairs
needed for the requested deliverable.

## Model policy

**Default to the agent's own built-in image generation and editing tools.**
Use them for artwork, repairs and hidden-surface fill; use Codex for planning,
grounding, visual review and candidate comparison. Use another image tool or
provider only when the user explicitly specifies it. Do not pin a backend model
name unless requested, or infer one from the tool's name.
**Use local AI models only when the user explicitly requires a fully local run.**
Import actual outputs through plan files, PSDs, repair add/replace inputs or
reviewed `materials-batch` drafts for coordinated layer changes.
If the selected tool is unavailable, report the specific gap; do not automatically
switch to another provider or local inference.

The default `execution.mode = "agent"` blocks local model processes. For an explicitly
requested fully local run, copy the config to the job's production directory, set
`execution.mode = "local"`, and pass that file with `--config` to model-using commands.
Do not switch the shared default merely to make a failing command proceed.
This applies to the local planner/VLM, Animagine, Qwen Image Edit, See-through,
SAM2 and LaMa. Local PSD processing, deterministic masks/pixel checks, PSD2Live
binding/rendering and export are ordinary tools and remain available in agent mode.

Current PNG-to-layer decomposition uses See-through and has no hosted adapter.
In agent mode use a prepared layered PSD, or assemble and inspect layers produced
with the selected image tool when feasible. Otherwise explain the missing
decomposition capability and request layered input. A flat
PNG cannot be passed to the existing materials stage without local inference.

## Locate the tools

This skill ships at `<repo>/skills/live2d-production`. Resolve the real path of
this `SKILL.md`, including installation symlinks; its third parent is the repository.
Run there. Read [tools.md](references/tools.md) before the first invocation.
On this project's NixOS setup use
`nix develop --offline --command uv run --no-sync l2d ...` with the prepared
environment. `doctor` checks files without loading models. Missing local weights
are not a blocker in agent mode. If required tool dependencies are missing, report
which ones and follow the user's existing setup authorization.

Keep production assets in `projects/<name>`, deliveries outside that store, and
the skill free of character assets or model weights. Inspect history and the
current revision before changing an existing project's working head.

## Production loop

1. **Record the brief.** Under `<project>/production/`, save identity, framing,
   requested motions, target application, chosen input and assumptions. When the
   user asks for detailed production, more expressive models, or reference quality,
   use [detailed-production.md](references/detailed-production.md) to define the
   component, parameter, expression and motion deliverables before making assets.
   For an unspecified first model, frontal half-body art, blink, mouth opening,
   small head motion and suitable hair/accessory motion are a starting scope.
   A request for a finished model also needs a visual feature acceptance table;
   do not silently equate this starting scope with finished facial expressiveness.
   For expressive characters or lip sync, read
   [facial-production.md](references/facial-production.md) before preparing layers.
   An explicit quality/reference brief takes precedence over this basic scope.
   Start with one artwork; default
   to at most three candidates and two repair rounds per defect unless the user
   supplies another budget. Record progress for resumability.
2. **Check runtime and plan structure.** Read
   [runtime-validation.md](references/runtime-validation.md). Inspect configured Core
   paths now. For an existing model, test its actual export before expensive polish;
   for a new model, test the first usable export. Plan the affected region's layers,
   hidden surfaces and deformation parents before keyforms.
   **Plan explicitly.** Read `l2d schema plan`, write JSON, and use `plan --plan-file`.
   Use actual part/object IDs from inspection. This bypasses the local text planner.
   Import a layered PSD with `--source`; PSD skips decomposition. For new artwork
   use the agent's built-in image tool unless the user explicitly selected another
   tool or a fully local run. Save the returned file, record the tool used and
   inspect the actual image before continuing.
3. **Inspect materials.** Run to `--until materials`, read `inspect`, and view the
   composite and relevant original-resolution layers. Import native image edits
   with add/replace drafts or `materials-batch` for several related layers. Use
   SAM2/LaMa/Qwen steps only in fully local mode. A paused revision can have
   useful materials and missing semantics: repair these through drafts. Follow
   [quality-checks.md](references/quality-checks.md) for motion-driven separation,
   alpha edges, source fidelity and hidden surfaces. For detailed layers or
   fidelity loss, use [components.md](references/components.md).
4. **Exercise the rig.** Continue to `--until rig`. Run the early Core check on
   the actual export when the library is available; investigate rejection before
   further polish. Inspect saved poses and actual
   parameter ranges. Use `rig-inspect` for full keyforms, hierarchy and geometry.
   Plans expose parameter/warp creation and object editing; author shapes after
   creating controls. Render problem poses, endpoints, intermediate values and
   affected combinations. Keep whole-character context alongside close views.
   Use matching parameter values and viewports for before/after comparisons.
   For additional controls, facial shaping or coupled poses, use
   [parameters.md](references/parameters.md) and check the current tool boundaries.
5. **Repair a named defect.** Use [repair-recipes.md](references/repair-recipes.md).
   Create a draft, update its full edit, preview, and request `repair evidence`.
   View the returned images, then `record-review` with concrete pass/fail/uncertain
   observations and the evidence ID. Reinspect after edits. Apply candidates only
   when rules and visual review pass. Continue ordinary repairs within the brief
   without asking at every stage.
6. **Author requested performance.** After the component/parameter design is stable,
   use [performance.md](references/performance.md) for expression presets, authored
   actions, physics tuning and their export references. Use `performance` and
   `physics` commands to version inputs and play the requested timeline. Normal
   export includes the selected revision's library. A sample pose or built-in preview video
   alone does not fulfill a requested expression/action library.
7. **Deliver with evidence.** Generate the requested performance preview, inspect
   representative frames and export the chosen built revision with sources.
   Recheck the final exported model and its final trace, not only an earlier build.
   Report each requested visual feature as verified, weak, unsupported or untested;
   distinguish a deliverable package from target-application acceptance.
   Summarize improvements, remaining defects and checks actually performed.
   Preserve a known-good revision when a candidate regresses.

Read only the extensions needed for the request. Reuse the revision/draft workflow;
the commands in these extensions do not authorize editing saved artifacts in place.
Keep unsupported features visible in the production brief and delivery report.

## Evidence and stopping rules

- Keep a production journal outside immutable revision directories: inputs,
  revision/draft/version IDs, defect and pose, viewed images, action, comparison
  outcome, and budget consumed. Resume from this record.
- Distinguish structural checks, visual observations, official Core compatibility,
  continuous physics and user acceptance. `built` and static poses do not establish
  the other results. `performance preview --physics` simulates multi-particle chains
  with an independent CPU solver, not the Cubism SDK solver. `verify-playback`
  optionally checks a trace through official Core; target-app behavior needs its
  own actual check. Diagnostic `preview-video` remains a parameter-only animation.
- Confidence scores, layer counts and parameter counts are insufficient evidence
  of improvement. Check the affected pixels and motions against the brief.
- Use `uncertain` for ambiguous evidence. Do not set `visual_accepted` or call
  `accept` to substitute an agent judgment for user acceptance. Local VLM `review`,
  `refine`, repair `inspect` and `search` are reserved for explicit fully local runs.
- Stop a repair when it passes, its budget is exhausted, or supported operations
  cannot fix it. Preserve the best candidate and explain the specific limitation.
  Ask only for genuinely ambiguous targets or missing authorization; continue
  independent work.
- Never edit saved `parts.json`, stage receipts, workspaces or revision metadata
  in place. Use revision/draft APIs and preserve their locks and hashes.
