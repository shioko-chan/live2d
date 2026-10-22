# Repair recipes

Read the current edit and schema. Mask coordinates are pixels in `source.png`;
placement is `[x,y,width,height]` on the full canvas; binding root/tip are normalized
within the component; rig drags use full-canvas pixels at the selected pose.
Convert resized display coordinates before using them.

Default to Codex's visual judgment and the agent's built-in image generation/editing.
Use another image tool or provider only when the user explicitly specifies it.
Import the result through add/replace and inspect the preview. The local-model branches below
apply only when the user explicitly requested a fully local run and its config is
active. Ordinary pixel masks and rig operations remain local tools in either mode.

## Missing material

Inspect existing layers and reference art. Obtain and inspect a matching transparent
PNG with the selected image tool (built-in by default). Create `--mode add --source ...`; set name, semantic (`mouth_open`, `mouth_close`,
etc.), side and placement. Preview scale, alignment and edge blending. Apply to
materials while required layers are missing, then build when complete.

## Accessory mixed with clothing/hair

In default mode, inspect the layer and use explicit paint/erase masks to separate
already isolated transparent regions. For connected art requiring reconstruction,
obtain extracted/filled layers with the selected image tool and import
them using add/replace; verify neutral composition and exposed surfaces. If that
workflow is unavailable, report the specific gap instead of loading local models.

For a fully local run, create `split` for the actual part. Supply a source-pixel box and keep/exclude marks,
run segment and inspect candidates. Choose `mask.candidate` if needed. Keep/exclude
points guide SAM; paint/erase strokes directly alter the mask. Include dangling
ends and exclude surrounding fabric. Use small named inclusion/exclusion checks
only where the semantic role is clear; SAM scores are not visual acceptance.

Preview with LaMa cleanup. Inspect component, underlying surface and exposure probe.
Correct contamination before binding. Transparent islands can split without
inpainting. Preserve neutral recomposition; the tool checks pixel invariants.

## Residue under an extracted object

Default: edit the underlying layer with the agent's built-in image tool and import the inspected
result using replace, preserving placement and checking unrelated pixels.

Fully local: keep extraction and cleanup scopes separate. Adjust `cleanup.expansion` or its
paint/erase marks. Use `cleanup.backend: "comfy"` and a specific prompt for semantic
inpainting. Set `allow_visible: true` only when the requested repair includes those
visible pixels, and record why. Pixels outside the cleanup mask remain protected.
Reinspect enlarged fill and exposure probes; probes translate/hide a component and
are not rig simulation. For complete models follow with binding-preview.

## Local artwork defect

Default: use the agent's built-in image editing on the actual layer and import with replace.
Inspect the result's alpha, alignment, outside-region changes and style before applying.

For a fully local run, use `repaint`, paint the region and describe the correction in `edit.request`.
The local implementation preserves alpha and pixels outside the mask; it cannot
expand the silhouette. Use replace with an inspected source and placement for a
changed silhouette. Compare outlines, seams, style and the whole character.

## Attachment or sway

Use bind for an existing component. Motion supports fixed/sway; choose root/tip
from the real attachment and start with a small angle. Root pinning and softness
control shape, not arbitrary multisegment physics. Inspect binding-preview at
affected poses. Source semantics determine the head/body parent; cross-frame
reparenting is outside the current contract.

## Specific deformed pose

Create rig, set `edit.rig.pose`, then run rig-context. Inspect objects, posed points
and images before choosing a real mesh/warp ID. For landmarks give explicit
start/end points and fixed attachment points in canvas pixels. The schema also
supports smooth and opacity. Preview actual arrival, inversion/collapse and fixed
point checks. Inspect neutral, problem and neighboring poses. A visual pass cannot
override geometry failure. Complete structural material changes before final
keyform polish; rebuilding can invalidate old corrections.

## No improvement

Revise the identified cause, not only the random seed. Default to at most three
candidates and two rounds per defect; reduce the budget for ambiguous inputs.
Preserve the best revision and record unresolved limitations. Local repair search
is restricted to fully local runs; native Codex review uses evidence/record-review.
