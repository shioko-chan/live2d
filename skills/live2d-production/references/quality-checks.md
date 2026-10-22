# Production checks

Evaluate against the brief and inspect pixels. Keep structural results and visual
observations distinct. Log images and poses for each actionable defect.

| Stage | Inspect | Continue when |
| --- | --- | --- |
| Artwork | Whole image and face/accessories; identity, anatomy, framing, line clarity | Requested features and important moving parts are identifiable |
| Materials | Source → generated layers → placed composite at matching scale; identity, aspect-ratio changes, alpha edges, mask/fill and exposure | Intended identity is retained; nonuniform resizing is reviewed; required semantics exist; exposed surfaces have no remnants/holes |
| Early compatibility | Actual export through configured Core; target app when available | Core accepts the sampled geometry; remaining application checks are stated separately |
| Neutral rig | Render versus material composite | Proportions, ordering and facial alignment are retained |
| Face | Eye closed/half closed, mouth, gaze, head X/Y endpoints/intermediates | No persistent eye slit, detached lash, distorted mouth or skin gap |
| Combined poses | Turn + blink, turn + mouth, body + head | Connections and occlusion remain plausible in tested poses |
| Components | Sway endpoints/intermediates and attachment | Root stays attached, silhouette is preserved, geometry checks pass |
| Animation | Sequence and representative frames | Sampled motion shows no observed jumps or disappearing parts |
| Control coverage | Requested feature table versus actual parameter effects | Each claimed control has authored shape/opacity behavior and evidence |
| Expressions | Neutral, subtle/full intensity, head-turn combinations and transitions | Intended emotion is recognizable; eye/mouth drivers combine as intended |
| Authored actions | Key beats, interpolation, loop seam or return to idle | Requested action is present with coherent timing and no observed discontinuity |
| Physics | Stateful CPU playback and, separately, target runtime settling | Observed response identifies its solver; roots and amplitudes remain plausible |
| Delivery | Export references, exact library snapshot and fresh reports | Presets/actions/physics are registered and any untested runtime is identified |

Record `not_evaluated` for unsupported or unobserved properties. Distinguish:

- `l2d render` and the original diagnostic `preview-video`: pose/parameter rendering;
  neither proves authored action playback or continuous physics.
- `l2d performance sample/preview`: authored motion/expression composition, optional
  stateful CPU physics, and actual CPU rendering when previewed. Preserve the
  snapshot ID, request, frame trace, video and playback receipt. CPU physics is
  labeled `simulated`; it is not the official SDK solver.
- `l2d verify-playback BUNDLE TRACE_JSON --core LIB`: an optional actual Core
  update for every trace frame, validating parameter results and finite drawable
  vertices. It does not execute SDK motion/expression/physics managers or render
  application pixels. No local official Core run is established merely by this
  interface's existence; claim a run only from its successful result.
- Target-application playback: required evidence for app-specific transitions,
  tracking/lip-sync interactions and the application's physics behavior.

For authored animations inspect intermediate frames, expression fades, loop seam,
one-shot return/hold behavior and composed eye/mouth drivers. Parameter counts or a
finite trace cannot establish natural shape changes. Static endpoint renders cannot
establish inertia/settling; use the stateful preview and identify its solver.

Compare artwork separately from decomposition: source details may be lost during
the 1024-pixel decomposition stage. Atlas dimensions do not measure original detail.
Prioritize identity/face, eye/mouth gaps, silhouette tears and attachment drift,
then accessory polish. Layer counts are not quality targets; new layers should
enable requested motions or prevent visible artifacts.

For thin facial strokes, also compare the placed PNG's visible support with the
bound mesh and UV coverage. A complete texture in the atlas does not prove that
the mesh draws its endpoints. Small antialiased staircase fragments can become
separate islands under four-connected contour tracing and be removed by component
area filters. When a rendered stroke is shorter, trace alpha preprocessing,
connected components, mesh bounds and actual UVs before blaming keyform scaling.
Treat this as a source-coverage defect; do not quietly widen a frozen comparison
baseline while evaluating a different deformation repair.

For pipeline comparisons, fix inputs, tested poses and comparable compute budgets.
Record manual interventions and provider changes separately from orchestration
changes. A smoke test on an existing asset establishes tool operability, not
general production quality.

For a detailed/reference brief, use the feature table from
[detailed-production.md](detailed-production.md) to report coverage. Existing basic
blink/turn checks alone do not fulfill requested richer expressions or actions.
Expression JSON and motion JSON can pass structure checks while their controls
produce no useful shape; judge their visible effect separately.

For speaking/expressive characters use the regional checks in
[facial-production.md](facial-production.md). Record weak or indistinguishable
expressions even if they have no geometry defects. Run the early and final checks
in [runtime-validation.md](runtime-validation.md); a pass from another model or
an older export does not validate the delivery.

## Visual failures override technical passes

Inspect the baseline for absolute defects as well as comparing changes. Identical
neutral pixels prove preservation, not baseline quality. Mark an observed broken
closure, detached contour, exposed remnant or incoherent connection as `failed`;
reserve `weak` for an intact result with insufficient expression or amplitude.
For blinks, inspect the entire eye region: upper lashes, lower outlines, whites
and iris must form a coherent closed eye. Checking only for iris leakage misses
stationary lower contours below a closed lash seam. Record whether the defect is
inherited, introduced or of unknown origin, with pose and image evidence.

A technical pass cannot override a failed visual feature. Report both separately;
when a repair budget ends with a visual failure, retain the candidate as an
experiment and state that the feature remains failed. Do not infer the responsible
layer from the composite alone; inspect source layers and deformation ownership
before prescribing its repair.

When changing this skill after a missed visual defect, retain the actual failing
source layers and rendered poses as a project regression case. Record the expected
rejection and observed cause separately from hypotheses. Re-evaluate that case
with the revised procedure before claiming the review gap addressed; a skill
format validator checks neither visual judgment nor production improvement. A
subsequent production run is still needed to establish that the revised workflow
prevents the defect.
