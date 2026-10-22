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

## Check actual content against binding behavior

Before binding a moving facial part, inspect its isolated pixels on light and dark
backgrounds and its placed composite. In the production record map each visible
feature to the source layer, resulting mesh/parent, intended control and expected
behavior at rest and displacement. A filename or semantic tag is not proof of
content: an `eyelash` image may include upper lashes, lower contours and lid folds;
a `mouth_close` image may contain a shaded lower lip as well as the mouth seam.

Include binder-generated meshes (such as automatic lip outlines) in this mapping;
the source-layer list may not contain every visible contour.

Read the existing binder's behavior and inspect the resulting rig before assuming
these features move together correctly. Where one texture contains features that
need different motion, either author a mesh that actually controls each region or
prepare separate artwork and bindings. Prove the choice with open/half/closed
poses before expression polish. If the binder cannot represent the separation,
record the missing capability and repair it or report the affected feature blocked;
renaming layers or adding a shared parent warp is not evidence of a solution.

For unexpected lines, compare source, resized material, material composite and rig
render to locate the first stage where the defect appears. Inspect alpha edges and
pixel dimensions; preserve uncertainty about an unisolated cause. Fix the earliest
faulty stage, then rebuild and repeat the affected poses. For hide/opacity probes,
verify that a known visible feature actually disappears in the rendered pixels;
a successful tool response alone does not establish a valid ablation. Preserve
invalid/no-effect probes separately and do not infer contour ownership from them.
Do not continue downstream
polish of a region whose basic closure or attachment is visibly broken.

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

## Preserve character identity across preparation

At the materials gate compare source reference, generated isolated layers and
placed composite at the same character/face scale. Check iris aspect ratio,
pupil/highlight placement, upper/lower lash silhouette, bang parting and the key
accessory/clothing silhouettes. Matching colors, semantic labels or layer counts
do not establish identity fidelity. Record intentional redesigns separately from
unintended drift, and retain both whole-character and face comparisons.

Record native crop size, output size, horizontal/vertical scale and aspect-ratio
change for each resampled part. Review nonuniform resizing explicitly before
binding: it can turn an elongated iris into a round one even if the generated
artwork was acceptable. Do not silently stretch a part to fit a planned box;
resolve the artwork/placement mismatch and recheck identity. Passing material
composition or runtime checks does not override a failed identity comparison.

For a confirmed preparation-only aspect distortion, use
`scripts/prepare_component.py SOURCE OUTPUT.png --crop L T R B --width W
--canvas-anchor X Y` (or `--height H`) to prepare a fresh PNG and transform receipt.
The tool accepts one target dimension, preserves the native crop, uses premultiplied
alpha for filtering and reports the small aspect error introduced by integer output
dimensions. It never overwrites an existing output or its receipt. The default
source anchor is the crop-box centre; use `--source-anchor U V` for an explicitly
verified normalized anchor. This does not detect the pupil, gaze pivot or identity.
Record which anchor is held and any integer placement error. Reproduce the old
dimensions with the same filter first, so a changed filter is not mistaken for an
aspect correction. At unchanged dimensions preserve original RGBA bytes.

Do not force an elongated iris into the visible eye aperture by shortening its
texture. Keep inspected hidden pixels and test the actual eyewhite clipping at
open, half-open, closed, one-eye closure, gaze and head turns. An unmasked material
composite is not a clipping test. Correcting aspect may change UVs, mesh topology
or parent frames; inspect rebuild/replay warnings and revalidate old keyforms.
A fresh-binder control can establish its own clipping behavior, but cannot prove
preservation of an authored production rig whose historical keys were not replayed.
Use the actual renderer's masking semantics when defining a pixel leakage check.
In the pinned PSD2Live preview, clipping uses the mask mesh's deformed triangle
area, not its texture-alpha support. Iris pixels outside an isolated eyewhite PNG's
alpha therefore do not alone prove a clipping leak. Retain that observation as a
diagnostic, inspect the actual composite and geometry, and test official runtime
masking separately. A known-broken fresh baseline cannot certify eye quality.
Compare reference, generated source, old preparation and corrected output; fixing
resampling does not restore a changed source colour, pupil or highlight design.

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

### Separate generation identity from preparation distortion

Record reference image, generated source, cropped component and placed component
as distinct artifacts with hashes and dimensions. A generated crop is not the
original reference. Giving a reference to image generation proves input provenance,
not that eye shape, palette or highlights were preserved. Attribute visible changes
by comparing each adjacent stage; do not infer a prompt failure from output alone.
For each placement report scale_x, scale_y and their ratio. If unequal scaling is
intentional, state the shape objective and inspect it at target scale. An anisotropy
flag is a mechanical finding requiring review, not proof that every flagged part
has lost identity. Record source-generation differences and preparation distortion
separately; fixing placement alone cannot restore missing source artwork detail.
