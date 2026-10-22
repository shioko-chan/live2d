# Detailed components and artwork fidelity

Use when detail is lost during conversion, layers move as large sheets, or the
brief requires independent face/hair/clothing behavior.

## Prepare a component map

Inspect the source at its actual resolution and record each proposed layer's role,
front/back neighbors, attachment, intended movement and exposed area. Examples:

| Region | Separate when the requested motion needs it | Check before binding |
| --- | --- | --- |
| Eyes | Upper/lower lids, lashes, whites, iris/pupil, highlights | Closed and half-closed contours, clipping, gaze at corners |
| Mouth | Upper/lower lip contours, cavity, teeth, tongue | Clean closed seam, varied opening/width, no floating teeth |
| Brows/cheeks | Left/right brows, blush, tears or expression shadows | Shape change beyond translation; effects follow the face |
| Hair | Actual crossing locks, left/right side locks, rear sections | Complete root-to-tip silhouette, depth order, hidden roots |
| Clothing | Collars, sleeves, ribbons, hems | Filled fabric behind moving pieces, intact seams |
| Accessories | Pendants, earrings, bows and their attachments | Root stays attached; unrelated surface stays still |

These are artistic roles, not automatically supported semantic tags. Inspect
existing material semantics and the imported rig. Verify that the engine maps each
new layer to its intended mesh/parent; naming a layer "lower eyelid" does not create
a specialized eyelid rig. Keep static decorative detail in its texture when no
independent control or occlusion benefit is needed.

## Preserve source detail

- Keep the original artwork and intended canvas dimensions. Inspect the face and
  smallest important ornament at the intended display scale, plus enlarged edges.
- Current automatic PNG decomposition uses a 1024-pixel stage and can redraw
  details. For a fidelity-focused task, use a prepared PSD and inspected layers
  from the selected image tool where feasible; record any actual resampling.
- A larger atlas or upscaled PNG does not restore missing embroidery, line work
  or identity details. Compare the material composite with the original artwork.
- Use consistent canvas coordinates and explicit part placement. A transparent
  PNG's tight crop is not the full-character coordinate system.

## Use the agent image tool effectively

Follow the tool's image-editing instructions. Supply the actual image/layer and a
specific task: identify the target lock/lid/ribbon, preserve style and alignment,
complete the hidden surface and return a transparent isolated result when needed.
Request a real transparent background through the tool's transparency option.
Avoid treating a painted checkerboard as alpha.

For a hair lock, describe root, tip, crossings and the visible/hidden continuation.
For fill, identify the surface revealed by movement and preserve adjacent features.
Inspect generated results for changed proportions, extra decoration, unwanted
background, missing alpha and shifted placement before importing. Do not assume
that a generated layer is pixel-aligned or that its neutral composite is unchanged.

## Apply through versioned tools

1. Inspect current materials and select real part IDs.
2. Use `repair --mode replace` for revised existing artwork and `--mode add` for
   new layers, each with `--source`. Update the full edit with placement, semantic,
   side and binding as appropriate; follow [repair-recipes.md](repair-recipes.md).
3. For an opaque connected region that needs extraction, prepare both the extracted
   component and the filled underlying layer. Put their add/replace changes into
   one `materials-batch` edit; include ordering, placement, semantics and bindings.
   Preview the entire composition, get evidence, view it, record review, then apply.
   The batch captures source PNGs and commits one revision only after all operations
   and the selected build stage succeed. Do not split dependent changes into
   separately applied repair drafts.
4. Compare neutral composition, hidden/moved exposure probes and actual bound
   poses. Deliberate artwork corrections may change pixels; document those edits.
   Existing deterministic split routes require their neutral-pixel invariant and
   must not have that check relaxed to accept generated artwork.
5. Apply only with current evidence. Retest the base and rebuilt rig as needed.

Read `l2d materials-batch schema` before authoring. Existing canvas dimensions stay
fixed; an empty initialized project requires `canvas: [width,height]`. PNG size must
equal placement width/height: there is no implicit resampling. Final drawing orders
must be unique; coordinate reordering across all affected parts in the same edit.
`update` replaces the full edit and captures sources again. Apply defaults to
`--until materials`; `--until rig` requires a complete material set. Material review
does not establish deformation or physics quality; render the rebuilt rig afterward.

Ordinary masks and transparent-island separation are local image processing.
SAM2/LaMa/Qwen paths remain reserved for an explicit fully local run. Agent mode
has no general flat-PNG-to-high-detail-PSD command; identify that gap when prepared
layers cannot be obtained with available tools.

## Completion evidence

Retain the source and material composite, full-resolution problem-region images,
placement records and pose previews. For each new movable component, show the
attachment at rest and at displacement, including the newly exposed surface.
Count only components that have a useful visual or motion role.

## Segmented secondary motion

Before splitting a lock or ribbon, plan which section follows which parent and
where roots remain attached. Multiple physics particles driving one whole-lock
parameter still provide whole-lock motion. For segment-by-segment response, author
corresponding mesh sections or a suitable hierarchy of local deformation targets,
then map chain outputs to those controls. Verify inherited motion is not doubled,
continuity at joins and exposed fill before tuning physics timing.
