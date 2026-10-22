# Fixed temporary representation control

This follows the rejected 03e76af8caa54714 candidate and its measured residuals;
it is not a parameter search or a new saved model revision.

The identity-only control already reproduces the candidate's full-open pixels
exactly in three frontal Forms. Mesh geometry is identical, yet inserting the
identity parent moves evaluated canvas vertices by up to 0.000162px. Therefore
the full-open exact-pixel failure is attributable to the extra transform path in
these tested cases, rather than a changed full-open keyform.

The shared warp also deviates from its prescribed field by up to .625px at
neutral. Neutral geometry width is 36.3353px versus painted C0 width 35.75px;
rendered dynamic ink width is 37px. Both representation and stroke support
contribute. Caps alone cannot explain the entire discrepancy.

One bounded experiment will apply the SAME frozen C0/G0 and correction function
directly at baseline mesh vertices, using supported keyform edits on a fresh
temporary copy for each of these fixed poses:

- Form -1 / Open .05: original failure.
- Form 0 / Open .1: crossfade boundary, where prior distance was large.
- Form +.5 / Open .05: middle-Form holdout, not a calibration input.
- Form 0 / Open 1: zero-correction preservation control.

Closed gets the same existing relative Form displacement. Dynamic upper/lower
lip and cavity receive the same per-vertex field used by the warp experiment;
no new smoothing, opacity, curve fitting, endpoint trimming or weight is chosen.
The original frozen painted curve is retained even though its extractor was
subsequently calibrated. This isolates representation rather than changing inputs.

Render actual opacity before/after, then forced-visible isolated components.
Use the explicitly versioned v2 diagnostic, with unchanged numerical limits.
Record all results, including ambiguity. A pass on these poses is not acceptance,
does not establish full-grid interpolation, and does not authorize promotion.
If the direct field fails, do not tune it: retain the failed assumption and
decompose the remaining static mesh and stroke-support terms.

No new revision, checkout, saved rig change, inference or external resource is
involved. All source hashes, formal head and revision list must remain unchanged.
