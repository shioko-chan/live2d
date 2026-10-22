# Root cause supervision

## Diagnostic stage, no new candidate

The existing implementation thread remains the sole model/code implementer;
the supervisor owns independent analysis and Git publication. Formal head is
`358614b7f88146f5`; rejected experiment is `1bf0686147f84cca`.

The implementation rendered 24 fixed poses (Open 0/.025/.05/.1 × Form -1/0/1
× turn 0/14), each as the actual unmodified composite and five isolated layers.
Only temporary-copy opacities were overridden for isolation. Its integrity receipt
records 144 renders, unchanged sources/head, and no new revision. The supervisor
viewed the six-panel minimal failure image, not only the measurements.

At Open .05/Form -1/turn 0, static/dynamic seam separation reaches 3.24 canvas
pixels; dynamic/static dark support width ratio is 1.3566. Dynamic upper/lower
seams are only .25 canvas pixels apart. Thus the extra line is the static texture
crossfading against a different generated curve, not a normal open-mouth boundary.
Independent extraction at 10%, 20%, 30%, and 40% relative darkness cutoffs gives
the same support width and 3.2443-pixel maximum gap. This is a stable measured
failure, not a threshold chosen after the candidate.

Binder inspection agrees: the static MOUTH_CLOSE has no MouthForm geometry;
generated lip/cavity geometry changes width and curvature with MouthForm. Shared
parents only contribute head pose. Previous translation cannot correct that shape
disagreement.

## Claude Code consultation

Claude was used in print/safe mode with tools disabled and session persistence
disabled. Only the project-specific question was sent. Its response recommends
a common closure curve for the static mesh and generated lips/cavity, preserving
zero displacement at neutral Form 0/Open 0. This is advice, not acceptance.

We rejected or narrowed these parts of the response:

- Counting multiple dark peaks in a composite can misclassify lip sheen and normal
  open-mouth borders. Compare isolated corresponding seams and inspect composites.
- A 41 × 401 full-render grid is unnecessary. Use dense deterministic geometry
  checks plus a bounded real-pixel grid and ordered sweep.
- No audio tracking or target application was supplied; scripted sweeps cannot be
  called real lip sync or target-app acceptance.
- The 506 × 678 iris crop is from the **generated asset**, not the reference image.

## Identity attribution without generation

`analyze_preparation.py` reconstructs all 17 prepared PNGs exactly from recorded
source crops and resize operations. No unexplained pixel edits are needed to
account for the prepared assets.

Left generated iris: 506 × 678 → 44 × 43; horizontal/vertical scale ratio 1.3711.
Width-preserving control is 44 × 59; height-preserving control is 32 × 43. Actual
control images were viewed. Nonuniform scaling explains a substantial roundness
change. Generated colour/highlight/detail differences remain in uniform controls,
so correcting scale alone cannot establish reference identity fidelity.

Twelve layers exceed the audit's one-output-pixel rounding allowance. This is a
mechanical provenance flag, **not twelve visual failures**. Intentional transforms
must be evaluated with their purpose and reference; small distortions and large
identity-sensitive distortions are not equivalent.

## Next boundary

Await the implementer's concrete common-curve data flow and positive/negative
measurement controls before authorizing one structural candidate. Fix numerical
limits before rendering that candidate, retain the known failing case, preserve
neutral/open endpoints and test Form/turn interpolation. No opacity/offset sweep,
new image generation, or promotion of a rejected candidate is authorized here.

## Structural implementation dispatched

The implementer supplied `STRUCTURE.md`: calibrate the neutral static seam C0(u),
retain G(Form,u)-G(0,u) as the common Form displacement, and apply a shared
endpoint-preserving correction to dynamic lips/cavity. The new step starts from
`f83489945b5448c1`, excluding the old translation warp. Its neutral static and
full-open dynamic endpoints are invariant by design and still need pixel proof.

One structural candidate was dispatched with the precommitted limits in
`ACCEPTANCE.md`. Source/scripts/tests/skill edits belong to the original Codex;
the supervisor only edits this report directory and independent production
evidence. No concurrency conflict or new Git owner was introduced.

Independent rehash before this step confirms all 96 formal baseline files remain
unchanged (`pre-fix-integrity.json`).

## Generation provenance limits

The saved `generation-prompts.md` says every component used `00-reference.png` as
reference. The iris brief requests complete vertical ellipses, a bright lower area,
radial detail and white highlights. It also records that returned parts were
automatically enlarged/recentered and required explicit placement preparation.
The corresponding local source PNG hashes are recorded in image provenance.

This does **not** support a claim that the reference was omitted, nor prove that
one particular phrase caused drift. No new generation experiment was conducted.
The supported attribution is the stage: differences already exist in generated
artwork, and anisotropic preparation additionally changes proportions. Reference
attachment and semantic labels alone are insufficient identity acceptance.

The supervisor rehashed all 12 local generated source PNGs against their saved
provenance; every hash matches. No original session/image-tool cache path was
opened. The supervisor also reran the saved-pixel `check_failure.py`: expected
failure reproduced (3.243478 px maximum gap, 1.356643 width ratio, .25 px normal
upper/lower control); this is a rejected-image regression, not a new render or a
production pass.

## Implementation review in progress

The opt-in `mouth_closure.py` helper correctly simplifies the shared correction:
`C(Form,u)-G(Form,u) = C0(u)-G(0,u)`. No automatic binder replacement is being
made. The actual old isolated seam PNGs are being retained as regression fixtures.

Supervisor review found a concrete validation risk in the initial coordinate-frame
fit: separate x/y variance checks do not reject collinear samples. A rotated line
can fit two positive axis scales exactly, although that inferred inverse is wrong
off the line. The implementer was asked to require a full-rank neutral sample set,
reject nonfinite tolerance/correspondence, and test this false-positive example.
This is a utility validation fix before candidate evaluation, not another model
candidate. A nonneutral Form correspondence test was also requested to catch
double-applied expression offsets and incorrect current-x correspondence.

The supervisor prepared `probe_candidate.py` to render 17 identical poses on the
baseline and final candidate in temporary workspaces. Eight specified neutral,
eye and full-open comparisons must preserve pixels. Four larger-angle stress
poses are separately labeled outside the modest-turn brief. The script has passed
Ruff; it has not yet been run against a candidate.

## Candidate 03e76af8caa54714 and independent verification

The implementation created one candidate using a shared 16-column/12-row warp,
with Form -1/0/1 and Open 0/.1/.5/1 keys, and Form geometry for the static mouth.
Existing opacity curves and the formal head were preserved. Its report contains
130 actual poses, 30 isolated-component poses, and 17 ordered sweep frames.

The supervisor independently viewed full candidate pixels and isolated layers,
reran the frozen measurement tool, and rendered 34 full frames (17 matched
baseline/candidate poses) in fresh temporary workspaces. The probe also captured
actual canvas vertices, including both lip ribbons. Sources and head remain
unchanged. The independent face contact sheet was viewed, including middle Forms,
winks and two large-angle stress poses. No target application was used.

The candidate improves the original double mouth, but **does not pass the frozen
gate**. Under the SAME new shared-u 2D metric with endpoints, the fixed failure
improves from 7.1241 to 1.1695 canvas pixels; width ratio improves from 1.35664 to
1.0625. The earlier 3.2435 figure is a different same-x interior vertical-distance
metric and must not be used as the denominator for this comparison. Only 1/30
closed/fade samples passes all frozen limits; maximum is about 1.300 pixels.

Neutral, eye and wink preservation was independently confirmed. Full-open Form
-1/0 changes 3/15 pixels by one channel level in the frontal independent renders;
Form +1 is identical. The exact-RGBA requirement fails, while this tiny change is
not evidence of a conspicuous visual regression. The new warp's identity mapping
is a plausible precision cause, not yet proven by full-open vertex comparison.

## Connection interruption and continued local work

Sending a coordination message to the existing implementation thread returned
`Transport closed`. Its delivery outcome is **uncertain**, so the message was not
resent. Subsequent supported `read_thread` calls also failed with the same error.
The parent reported desktop reconnection, but only command/file execution has
been verified recovered; thread coordination has not. No duplicate build or new
candidate was started. The successful independent render used the existing
nonblocking runtime/project locks and unique output directory.

A second targeted Claude print consultation (tools disabled) discussed only the
observed geometry/pixel residuals. It recommends error decomposition before any
representation change, direct mesh-key correction only if measured residuals
justify it, and separate cap/support and raster calibration. Some numerical
assumptions in its response are hypotheses, not adopted acceptance criteria.

`audit_field.py` compares the actual candidate vertices to applying the prescribed
analytic field directly to the actual baseline vertices, in the measured parent
frame. At neutral, where Form/Open interpolation cannot explain the difference,
max spatial representation error is 0.62533 canvas pixels (p95 0.49806). At the
known failure it is 0.66442 (also includes Open-key interpolation). This establishes
a real representation contribution; it does not yet prove it explains all of the
pixel error or that another candidate would pass. No new candidate authorized.

## Measurement calibration defect and bounded supervisor fix

The implementation's new `HANDOFF.json` explicitly records implementation complete,
locks released, and supervisor-only Git publication. With supported thread tools
still unavailable, the supervisor continued within a separate, documented boundary:
the measurement helper, its tests/CLI, and the calibration guidance. The model,
candidate plan, original gate and all previous reports were not changed.

Known-centre synthetic controls expose v1's first-maximum bias: identical y=20
geometry produces measured y=19.913 for a soft narrow stroke, y=19.5 for a 1.5px
flat stroke, and y=18.75 for a 3px flat stroke. The control images were viewed.
Copies/translations of one profile had missed this failure.

The fix uses the complete equal-maximum plateau and adjacent antialias samples;
equal maxima separated by a below-threshold gap are explicitly ambiguous. The
first implementation rejected a real fixture because of one 0.037% quantized
colour dip inside a connected stroke. Inspecting that column established it was
one ink component; the final check uses the existing darkness threshold to detect
a separating gap, not exact floating-point equality of every interior pixel.
All six coincident controls now measure y=20. Missing/ambiguous input emits an
`unmeasurable` CLI report with exit code 2, distinct from a measured gate failure.

Reports use `ridge-plateau-v2`; the old instrument is preserved in
`audit_ridge_bias.py` for reproducibility. Diagnostic remeasurement of the SAME
unchanged candidate gives 7/30 within the same numerical limits, original failure
1.06160px, worst 1.29980px. It still fails. This is not retroactive acceptance or
a relaxation of the frozen v1 gate, whose 1/30 result remains intact.

## Completed independent checks

- Full prepared-environment suite: **298 passed**, two existing deprecation
  warnings, 18.99s. Ruff, skill validation and whitespace checks pass.
- Three stored real examples replayed into new drafts without applying revisions:
  ear split `a57fd930fd6440caadd731da500bef99`; missing mouth
  `0b1310f7f84649b9907108d021de85a4`; known anchor drift
  `3a16ce2d83804bd1b04761e49cb7effc`. Fresh material images and before/after
  drift crops were viewed. Ear neutral pixels remain unchanged; drift target
  error is 0.0176409px. These are their bounded operation/defect scopes.
- Independent export audit: all model references confined and present, readable
  1024×1280 PSD, 17 source layers, editable workspace and CMO3 present. Three
  component entries describe component motion data, not the source layer count.
- Independent official Core run: exact exported MOC with the 17 independent
  poses, 168606 finite vertex checks, passed. The implementation separately
  reported 130 pose and 3015 dense geometry samples. None establishes target-app
  pixels, live tracking, SDK physics, or user acceptance.

No new candidate was created after the structural experiment. The remaining
representation/cap/static-mesh residuals need measured decomposition before a
further model change. Source identity still needs artwork work; no new paid
generation was attempted.

## Publication content review

The parent independently reverified the user's later stage-commit/push instruction
from the original conversation. Before the permitted single retry, the supervisor
confirmed the existing origin is this project's expected GitHub remote and that
remote main remains at the previous 7527d31 milestone. The rejected push did not
publish the local 90fcb7a commit.

Publication is limited to this project's code, tests, small rendered mouth fixtures,
skill guidance and validation summaries. Raw Claude questions/responses are kept
locally and removed from the unpublished commit; their necessary conclusions and
limitations remain in this journal. No user message identifiers, credentials,
unrelated project assets or account/session records are included. Only the
unpublished local commit is amended, with normal fast-forward push still required.
