# Plan detailed production

Use for a reference-quality brief, richer components/controls, or a request to
improve the current basic model. Scope detail by what the character must do.

## Establish the comparison

Inspect the requested reference and current model. Record the exact reference
variant, current revision, artwork dimensions and viewed images. Downloaded
variants may contain different costumes, hand swaps and motion libraries; compare
the relevant abilities. Reuse local reference files when available.

Inspect three kinds of evidence:

- Artwork/materials: preserved lines, facial features, edge quality and hidden fill.
- Structure: independently movable components, parameter meanings, deformation
  hierarchy, expression/motion entries and physics inputs/outputs.
- Behavior: equivalent head turns, blinks, mouth shapes, combinations and continuous
  playback where a runtime is available. Mark unplayed behavior `not_evaluated`.

Mesh, deformer and parameter counts help locate differences. Do not set a target
such as 300 layers merely because a reference has that count. Runtime ArtMeshes
are not PSD layer counts; alternative hands/costumes can inflate either count.

## Expand the brief into work

Keep one concise feature table in `<project>/production/brief.md`:

| Requested result | Component or actual object IDs | Control and dependency | Execution route | Evidence / state |
| --- | --- | --- | --- | --- |
| Independent side locks | Existing part IDs, or proposed layer names clearly marked | Per-lock sway; filled scalp/face behind them | Layer add/replace, then bind | Image and pose paths; planned/implemented/verified/unsupported |
| Recognizable surprise | Eye, brow and mouth objects | Existing controls; new shapes if insufficient | Rig repair and expression packaging | Neutral, surprise and turned surprise |
| Greeting | Head/body controls; arm/hand layers if required | Authored timing and any hand switch | Motion packaging; missing arm rig recorded | Playback evidence or not_evaluated |

This table is a production note, not a `Plan`, `Parts` or `RepairEdit` schema.
Obtain JSON contracts with `l2d schema`; do not insert these columns into engine
inputs. An intended feature is implemented only after it changes the actual model
or exported performance assets, and verified only after its named checks pass.

For a rich character brief, consider the following groups and omit irrelevant ones:

| Group | Decisions to make |
| --- | --- |
| Face | Lid/lash separation, pupil/iris/highlight movement, brow shaping, mouth cavity/lips/teeth/tongue, cheek effects |
| Hair | Foreground/background locks, roots, overlap order, separate swing directions and amplitudes |
| Clothing/accessories | Collars, sleeves, ribbons, pendants, hems and attachment points |
| Controls | Meaning, actual IDs, defaults/ranges, shape dependencies and combined poses |
| Expressions | Character-appropriate emotions, subtle/full intensity, transition and reset behavior |
| Actions | Intent, timing, loop/one-shot behavior, expression/blink/lip-sync ownership |

The character's brief determines the final selection; an automatically renamed
set of generic presets does not establish richer expressiveness.

## Work in usable increments

1. Preserve the current built revision. Run the available early checks from
   [runtime-validation.md](runtime-validation.md) before expensive polish. Diagnose
   actual Core rejection before proceeding; unavailable applications are a recorded
   gap, not grounds to claim failure or abandon independent artwork preparation.
2. Plan the required layers, hidden fill and hierarchy for the affected region
   before authoring its shapes. For a speaking, expressive avatar, prioritize mouth,
   eye shapes/emotions, brow curvature, then head depth; design their shared head
   structure up front. Hand/arm detail follows when the brief requires it.
   Complete one important region, usually face or hair: layers, parameter shapes,
   combined poses and a representative expression/action. Use this to expose tool
   limitations early, then continue the remaining requested regions.
3. Stabilize the component structure before polishing keyforms. Material rebuilds
   can invalidate rig corrections; inspect replay warnings and retest affected poses.
4. Tune physics and actions after the driven structures work. Each segment of a
   multi-stage response needs a corresponding deformable target; a particle count
   alone does not establish segmented motion. Package and recheck the final export.

Read [components.md](components.md), [parameters.md](parameters.md) and
[performance.md](performance.md) as their stages become relevant. The capability
table in [tools.md](tools.md#authoring-capabilities) distinguishes current routes
from missing interfaces. Continue independent supported work when a feature is
blocked; record the exact dependency instead of claiming it is complete.

## Compare results fairly

Use the same character scale, crop, backgrounds and parameter intent for before/
after images; different models can have different numeric ranges. Check neutral,
one-axis extremes, intermediate states and selected combinations. Do not present a
larger image or denser mesh as evidence of improved motion.

At delivery, report requested features implemented, visible defects corrected,
remaining unsupported items, and checks not performed. Save artifact paths and
revision IDs. Do not replace visual/runtime assessment with a single quality score.

## Validate a change to this skill

When asked to establish whether a workflow improvement works, execute a real
production increment with a preserved baseline; documentation validation alone is
insufficient. State the region and behaviors covered before editing. Keep source
art, framing and input motion fixed where possible, and save both parameter traces
and same-scale renders. Include a neutral regression case and an interacting
control case (for example smile plus blink and speech), not just expression peaks.
Use the normal revision/library/export path and the final Core check. Report
improved, unchanged, regressed and untested outcomes separately. Feed observed
failure causes back into the instructions. A successful regional revision is
limited evidence; it does not validate from-scratch art/decomposition or establish
general quality across characters. Expand the next run only to cover a stated gap.
