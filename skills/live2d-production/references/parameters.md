# Parameters, deformation and combined poses

Use for more expressive controls, improved turns/blinks/mouth shapes, or components
that need independent movement. A parameter is usable when its authored keyforms
produce the intended change throughout the requested range.

## Design controls against the current model

Read `l2d inspect` and the actual `rig-context.json`. Record for each requested
control: actual ID (or clearly proposed ID), meaning, default/range, target mesh or
deformer, key positions, inherited parent and interacting controls.

Start from existing IDs; do not assume two characters share parameter meanings or
ranges. Render the current effect before adding or modifying it. Candidate families:

- Head/body: turns, tilt, shoulder/neck relationships and breathing.
- Eyes: independent opening, gaze, smile/squint shapes and highlight movement.
- Brows: vertical motion plus requested angle/curvature/asymmetry.
- Mouth: opening and form, with independent lips/interior when supported.
- Hair/accessories: per-component sway and optional visibility.
- Expression effects: requested blush/tear/shadow visibility with suitable artwork.

For newly authored custom parameters, prefer stable semantic IDs such as
`ParamHairSideL` and descriptive display names. Existing automatically generated
IDs remain valid: keep a readable ID-to-purpose mapping. Do not assume a parameter
ID rename operation exists. If replacing an ID is necessary, inspect the live tool
schema and migrate keyform axes, physics, expressions and motions together in a new
revision; validate references and re-export. A display-name edit alone does not
change the ID.

Give each control a visible purpose. Do not enlarge the numeric range to imply
greater head rotation unless the corresponding geometry and exposed surfaces work.

## Current execution routes

- Existing parameter metadata/ranges: a plan `kind: "rig"` operation with
  `tool: "parameter_update"`, using the schema saved in the rig context.
- Existing object keyforms: `keyform_set` or `rig_transform` plan operations.
  Use actual rig object IDs, not source part IDs. A plan supports at most 16
  operations; group meaningful work into revisions without changing this contract.
- Image-grounded pose correction: a `repair` draft with `mode: "rig"`, then
  `rig-context`, landmarks/smooth/opacity, preview, evidence and apply.
- New per-component controls: `bind` with `motion: "sway"` creates the component
  sway parameter and child warp; `toggle: true` creates its visibility parameter.
  Inspect generated IDs rather than predicting them. Root/tip are normalized part
  coordinates; a semantic parent determines inherited head/body motion.
- Custom controls: plan `parameter_create`, then `keyform_set`/`rig_transform`
  against that parameter's actual coordinates. Creating metadata alone has no
  visible effect. Operations run in order and refresh IDs after structure changes.
- Hierarchy: plan `warp_create`, then `object_edit` for rename, visibility, move
  or bind using the live schema. Creating a warp requires existing meshes with a
  common Warp parent. Reparenting requires explicit `space: "local"`; it keeps
  local coordinates and does not promise unchanged appearance across parents.
- Deep inspection: `l2d rig-inspect PROJECT --object ID --kind warp --detail object`
  returns full axes/keyforms; `--detail points` returns paged geometry. It opens a
  disposable workspace. Read `read_tools` in the rig context for exact schemas.

`keyform_set` requires a nonempty coordinate, even at neutral: specify the real
parameter and its default value. Warp row/column requests may be aligned to the
parent grid; inspect the actual topology after creation. The executor owns history
HEAD fields; omit them from plans.

Inspect [tools.md](tools.md#authoring-capabilities) when deciding what is executable.
Keep proposed controls in production notes until they actually exist in the rig.

## Author deformation with evidence

1. Establish the neutral geometry and attachment before changing keyforms.
2. Inspect min/default/max and intermediate values; use actual authored key
   positions when available. Correct lids, lips, silhouette and attached roots.
3. Add a shape correction to the smallest relevant mesh/warp. Check its ancestors
   so parent motion is not inadvertently applied twice. Preserve masks and ordering.
4. Test interactions that share geometry: head X/Y, turn plus blink, turn plus
   mouth opening/form, gaze plus half-closed eye, body plus head, component sway
   plus parent turn. Avoid a full Cartesian sweep of unrelated controls.
5. Inspect both endpoints and interpolation. A good endpoint can still interpolate
   through a folded mesh, pinched eyelid or disconnected lip.
6. After a material rebuild, inspect `rig-replay.json`/`needs_review`. Historical
   creation/dependency groups are replayed together only when their coordinate
   frames remain valid. New layers may change parent frames and require reauthoring.
   Current-plan operations execute after historical replay; use new-model coordinates.

For range changes, recheck saved poses, presets, physics outputs and authored
actions that use the parameter. Update their authoring inputs and re-export the
delivery; keep revision artifacts immutable.

## What to verify

| Control | Evidence of a useful implementation |
| --- | --- |
| Eye opening | Fully closed seam and plausible half closure; lash remains attached |
| Gaze | Iris stays inside the eye opening at corners and during a blink |
| Mouth | Clean closure; intermediate opening and mouth-form combinations retain lips/interior |
| Turn | Facial landmarks, silhouette, hair depth and neck connection remain coherent |
| Component sway | Fixed root, flexible tip, filled exposure and no geometry inversion |
| Visibility | Correct part toggles, including in turned and overlapping poses |

Save the parameter values with each rendered image. Geometry diagnostics establish
only their reported scope; passing triangle checks does not establish good facial
shapes, correct occlusion or natural physics.
