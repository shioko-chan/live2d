# Produce visible facial expression

Use before layer preparation and keyform work for expressive avatars or lip sync.
Adapt to the character's style and requested motion; this is not a fixed layer quota.
Assess actual pixels alongside source layers, generated meshes and keyform geometry.
Automatically generated lip outlines, for example, are not independent authored
teeth or tongue layers, but must not be described as absent lip geometry.

## Plan structure before shape polish

For each intended motion, record the moving feature, underlying exposed surface,
mesh/warp parent, interacting controls and the image/pose that will prove it works.
Prepare related extracted parts and hidden fill together in a reviewed material
batch. Stabilize the affected region before its detailed keyforms. Recheck replay
warnings after rebuilding; reauthor invalidated shapes without assuming every
unaffected region must be discarded. Preserve a working base revision.

| Feature | Structure/authoring decision | Visual acceptance evidence |
| --- | --- | --- |
| Speech mouth | Separate lips/interior and teeth/tongue where their independent motion is needed; define corner, opening and form behavior | Closed seal; small/medium/full openings crossed with required mouth forms; no interior leaks, popping teeth or detached lip; turned speaking pose |
| Smile/squint | Shape upper/lower lids and lashes together, preserve iris clipping; eye opening alone may not produce a smile | Neutral versus smile at matching head pose; lower-lid lift/curvature where appropriate; half blink plus gaze and smile |
| Surprise | Author a distinct lid shape if default opening is already at maximum; coordinate brows and mouth | Readable eye/face change without iris escape or stretched lashes; no range extension without corresponding shapes |
| Concern/serious brows | Control inner end, arch and outer end through local keyforms or a warp; tune line contrast if needed | Curvature/inner-end difference visible at delivery scale, with neutral and intended emotions side by side |
| Head depth | Plan relative movement of face contour, nose, eyes, ears, front/back hair and neck; split artwork where differential motion needs it | X/Y turns and diagonals show coherent perspective/occlusion; neck/collar attachment and exposed surfaces remain intact |

Whole-brow rotation can contribute to expression, but is insufficient evidence of
brow shaping. Check the actual contour, contrast and occlusion before blaming hair.
Drawing brows above bangs is a style decision, not an automatic fix. For mouth
production, prioritize needed intermediate shapes over counting open/closed images.
A smile preset with unchanged eyes may suit a subtle brief; it must not be claimed
as an authored smile-eye control. A flat-looking turn needs landmark/contour and
occlusion comparison; missing separate nose art alone does not prove pure translation.

## Avoid mistaking a shared warp for eyelid control

Inspect the actual lattice and child subtree before changing an eye parent warp.
A deformation intended to lift the lower lid can flatten the upper lid, move or
squash the iris, and alter the closed seam. Compare eye contours and iris shape at
open, half-open and closed states; narrowing alone does not establish a smile.
Reject a closure repair that removes the gap by crushing lashes into thin or broken
lines. Compare retained lash shape as well as disappearance of the unwanted contour.
For a repair, author against a saved baseline geometry when replacing a failed
shape, rather than accumulating unexplained transforms. If the shared structure
cannot produce the target expression within the repair budget, mark it failed
when visible defects remain, or weak when intact but insufficiently expressive.
Plan dedicated lid/iris controls or needed artwork; do not rename the outcome as
a successful expression.

## Author mixed contours without flattening the whole layer

When one eye texture contains upper lashes and a lower contour, inspect whether
its mesh has enough vertices to move the regions independently. Use
`rig_inspect` points in parent-local and canvas space to establish units and
feature ownership before authoring `keyform_set` position deltas. Derive separate
upper/lower anchors from the actual artwork, retain each contour's thickness and
make their trajectories meet at the intended closed seam. If the sampled topology
cannot support this without visible folding or tearing, split/repaint the parts
through the material workflow instead. Do not copy another character's anchor
coordinates or scaling constants.

Check opening 0/.25/.5/.75/1, both one-eye closures, head-turn combinations and
requested smile controls. Keep full renders and close views. A thin closed line
alone does not prove correct intermediate clipping or retained lash shape.

For separate closed/open mouth artwork, inspect the binder-generated lip meshes
as well as both source textures. A static closed-mouth layer fading across the
entire opening range can leave a second mouth line over the opening. Author its
exit and the generated outlines' entry together. Check a sample inside the fade
interval (for example 0.05 when exit is at 0.1), its endpoints, intermediate
openings and MouthForm min/default/max. Two contours at different positions remain
a double mouth even when each is partially transparent: align their geometry
before judging the crossfade. Sample more densely around any observed mismatch. Preserve intended open-mouth borders; do not hide all outlines merely
to pass the closed pose.

## Judge recognizability and driver ownership

Build a comparison sheet at matching scale and head pose for each requested
expression. Inspect the full face and relevant original-resolution regions. If two
intended emotions are indistinguishable, report the feature as weak and repair the
responsible art/shape within budget; JSON validity and different numbers do not pass
this check. Keep “no visible defects” separate from “emotion is recognizable.”

For each shared control, record its driver: tracking, blink, lip sync, expression,
motion or physics, and whether the expression is a transient override or a modifier.
Do not write a full neutral parameter dictionary into every preset by default.
Use only needed channels. An intentional closed-eye emotion may override blinking;
a speaking smile should preserve mouth opening unless deliberately specified.
Add/Multiply are not automatically correct either: test their composed range.

Exercise activation, sustained expression plus speech/blink, crossfade to another
expression, and release back to live controls. Preview scripted parameter drivers
locally when tracking is unavailable, and label that scope. Never claim live lip
sync integration from a prerecorded mouth-opening curve. Save the request, trace,
selected frames and unresolved driver conflicts alongside the library snapshot.

## Shared closure correspondence and measurable failure

Before changing a fading closed/open mouth pair, isolate Closed, upper lip,
low lip and cavity with the renderer's `include_layer_ids` on a disposable copy.
Render both authored opacity and forced opacity 1; never alter the saved candidate
just to measure its hidden contours. Freeze viewport, scale, score threshold,
poses and acceptance limits before evaluating the candidate. Preserve the exact
baseline and keep geometry acceptance separate from opacity and visual judgment.

Translation cannot correct different widths or curvature. For an opt-in common
closure design, define a neutral painted seam C0(u) and existing dynamic closed
curves G(Form,u), with the same verified source-column u for all components.
C(Form,u) = C0(u) + G(Form,u) - G(0,u) preserves the neutral static shape.
Its shared dynamic correction is C0(u) - G(0,u); recover u through the current
Form/Open horizontal mapping, rather than matching arbitrary canvas x. Fade this
correction to exactly zero at full opening. Apply it to upper/lower lips and cavity
together; do not double-apply inherited head transforms. A mathematical identity
does not prove a finite mesh/lattice can represent the curve: measure actual output.

`live2d_pipeline.mouth_closure` provides explicitly supplied curve sampling and
correction math. It is not an automatic anatomy detector or a global binder
replacement. `Frame.fit` requires finite, noncollinear correspondences and rejects
non-axis-aligned mappings; use a verified inverse for other frames. Inspect pinned
mesh topology before extracting a ribbon centre; caps, overlaps and joins must not
be guessed. Reject missing/ambiguous contours instead of fabricating anchors.

Use `scripts/measure_mouth_closure.py DIRECTORY --viewport L T W H
--max-distance LIMIT --max-width-error LIMIT --output REPORT.json` for independently
rendered closed.png/upper.png/lower.png. It measures 21 shared-u samples including
endpoints and separately reports component support widths. Record raster resolution
and threshold sensitivity; do not relax limits after a failure. This is painted
support, not anatomical mouth width. Preserve normal upper/lower separation at large
opening, and inspect cavity coverage separately. Check the original failing pose,
all Form extrema/defaults, middle Forms, fade interiors, small head turns, exact
neutral/full-open endpoints, eyes/winks and a bounded ordered real-render sweep.
Calibrate the extractor with coincident geometric strokes having different widths
and intensity profiles, not only copies, translations and width changes of one
profile. A first-maximum detector selects the upper edge of a flat dark plateau;
it can falsely move an otherwise aligned centreline as thickness changes. The
measurement helper centres the peak plateau and rejects equally dark peaks in
disconnected ink components. This is for isolated contours, not a rule against
normal upper/lower mouth borders. Reports identify the measurement version. If a
calibration bug changes the instrument, retain the old gate/results, rerun positive
and negative controls, and label new measurements separately; do not silently
retrofit a passing certificate to the old candidate.

Official Core geometry checks still do not establish visual or tracking acceptance.
If one authorized structural candidate fails, record the failed representation or
assumption before another proposal; do not search opacity or offsets to hide it.

### Decompose representation and stroke support before another repair

When unchanged full-open keyforms fail exact pixel preservation after reparenting,
test an identity-only parent on a disposable baseline. Compare the same pose's
vertices and actual pixels with both baseline and candidate. An identity transform
can still change finite-precision evaluation and edge rasterization. Keep that
cause separate from a nonidentity lattice's spatial approximation error. A direct
mesh correction that is mathematically zero at full opening must retain the actual
endpoint keys and hierarchy; do not assume adding zero-valued transforms does so.

A fixed per-pose direct-field control can isolate lattice representation without
creating a new revision. Preserve input curves, opacity and correction function;
predeclare cases and retain failures. Passing individually authored pose keys does
not prove interpolation across a complete Form/Open grid, turns or an ordered
transition. Verify actual evaluated vertices, not only written keyform deltas.

Distinguish geometric ribbon centres from painted stroke support. At an endpoint,
record horizontal and vertical error separately. Where possible, decompose the
horizontal term into geometric support mismatch and ink beyond that support.
Predicted geometry leaves evaluation error inside the latter term; actual preview
geometry narrows that uncertainty but is still not official Core evidence. Caps,
join patches, antialiasing and threshold sampling can contribute. A nonzero-open
tip overlap may be multivalued in y(x); do not silently collapse it into one curve.
Mapping each stroke vertex by its current x can also alter stroke support: sharing
the formula does not establish stable source-u correspondence across paired edges.

Measure subpixel raster-phase sensitivity with fixed world geometry and a shifted
viewport, recording the viewport in the conversion back to canvas coordinates.
Sensitivity measured on one painted profile is not a universal bound for generated
outlines. Keep the original gate result even when a failure is close to the sampling
resolution. Neither rounding down, discarding endpoints, trimming caps nor relaxing
the limit is an evidence-backed repair. Calibrate on unchanged baseline and holdout
poses before adopting an ink-based correspondence; never assume ink-minus-centre
offset is invariant under deformation.
