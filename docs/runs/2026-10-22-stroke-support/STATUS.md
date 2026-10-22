# Resumable supervision journal

## Design review and ownership

Session tools recovered, and the existing implementation thread was verified idle
in this project. A fresh task was acknowledged; no uncertain previous message was
repeated. Codex wrote `production/stroke-support-20261023/PLAN.md`, then received
supervisor review to execute its one owner-translation/neutral-ink prototype on
six fixed poses, at most 52 actual renders. It owns mouth code/tests, while the
supervisor owns preprocessing code, skill guidance, journals and publication.

Claude Code consultation used print safe mode with tools disabled. Adopted:
separate correspondence from stroke geometry; same owner displacement does not
guarantee thickness normal to the new tangent; validate shear, triangle orientation
and cavity coverage; distinguish source identity from preprocessing distortion.
Rejected: its mistaken interpretation of 658 vertices as both lips combined, its
suggested path-index/arc-length u across reversed overlap, replacement of the
painted-ink gate with a geometric-centre gate, and a universal raster error bound.
Actual pinned source confirms each lip's 277 path points, 554 pairs' vertices,
two cap blocks and two join blocks. The source column ordering must still match
actual imported geometry before any authoring.

## Iris preprocessing implementation

Added `component_preparation.py` and `scripts/prepare_component.py` to require one
target dimension and an explicit canvas anchor, preserve the inspected crop,
filter in premultiplied alpha, record integer rounding, reject empty outputs and
refuse overwriting existing images/receipts. No-resize input retains exact RGBA,
including low-alpha RGB. The CLI hashes the exact source bytes it loaded.

Both real crops were prepared once at width 44 and height 59. Source crop-box
centres remain exactly (448,372.5) and (598,372.5), giving placements (426,343)
and (576,343). This is an explicitly held crop centre, not a detected pupil or
gaze pivot. Post-rounding aspect factors are .99926308 and .99729215, versus the
old approximately 1.37 distortion. Recreating old 44x43 materials with the same
filter is pixel identical, separating filtering from aspect correction.

Viewed the complete generated source and light/dark prepared controls: the full
elongated source pixels remain, with no repaint, colour change or invented hidden
content. Generated source details still differ from the reference. Preservation
of aspect is not reference-identity acceptance.

The first batch-create attempt returned `Task already running` on the project
lock before creating a draft. This is the original Codex's active prototype, not
an approval denial. No lock was bypassed and no process interrupted. Creation is
deferred until it reports release; static preparation and helper tests continued.

The planned identity rig assay uses two separate fresh-binder copies with 14 fixed
poses, actual and isolated iris/white renders (84 total). Historical facial keys
will not be silently replayed into changed material coordinates. Therefore this
can establish fresh-binder clipping only; it cannot establish preservation of the
authored rig. Both actual eye-region changes and any changes outside the declared
eye ROI will be reported. Formal material application remains unperformed.


## Mouth evidence and follow-up

The B owner-translation prototype completed exactly 52 renders on temporary copies.
Independent measurement of all five fixed closure cases passed the unchanged v2
1px/10% limits: .565647, .349020, .500008, .382814 and .285452px maximum distance.
Neutral and the single full-open control remained RGBA-identical. Actual geometry
matched the declared field to .000116px, but normal stroke quality failed: at the
original visible failure, tangent rotation67.09 degrees reduced width1.5 to .5839px.
Viewed actual before/after whole-pose and mouth contact images; double contours
improved, but local jaggedness remains. No candidate or full-grid expansion.

Original Codex then completed zero-render offline controls. Rebuilding original
radius .75 normals/caps/joins on the exact same B paths produced numerous main
boundary crossings and triangle flips. Raw native artwork also retained sharp
extracted turns. Full native mapped ridge was1.47951 times the actual static width;
restricting its audit to actual mesh x support yielded .516787px distance but did
not make a production curve. These rule out direct normal-only and raw-native
replacement. FOLLOWUP-ANALYSIS.md records actual numerical controls and limitations.
A new acknowledged task traces mesh/UV support and predeclares one continuous
representation for offline feasibility; no further render/candidate is authorized
until its design evidence is reviewed. This is root analysis, not another blind
candidate search or a relaxation of the previous repair budget.

## Identity draft and fresh-binder evidence

After original Codex released locks, one retry created material batch
96cc6000a88d4277b246697d5e737fe3. Preview870e87b2a59140c68d8d89572eb96c96,
evidence9b40faf1184c4097be5bf9731df62573:17layers, mechanical readiness passed.
No material review/apply or formal checkout occurred. Existing rig history audit:
73 operations require review,0 safe auto-replay; keep this integration gap explicit.

The fixed fresh-binder assay completed84 actual renders:14poses x2material sets
x(actual/iris/white). Viewed all pose contact comparisons and a full combined-turn
image. Uniform irises preserve the native elongated shape and are clipped in the
viewed composites, while source palette/detail/highlight drift remains. Both fresh
baselines retain visibly detached lower-lid contours and imperfect closed lashes;
this assay does not replace the authored eye-repair baseline or certify eye quality.
Closed irises are fully invisible. Thirteen poses have no changes outside the
frozen eye ROI; turn-left has one changed pixel outside it. Even fully closed
actual images differ at95pixels (maxchannel57), requiring causal geometry analysis.

Raw counts of iris alpha outside isolated white-texture alpha were retained, but
are not clipping failures or pass criteria: pinned RigCanvasSupport clips to the
white mesh triangle union, not its texture alpha. New skill guidance makes this
observable mismatch explicit instead of adjusting a threshold after seeing data.

The first geometry re-open attempted no model edits and failed before rendering
because source.psd2live had not saved later in-memory component parameters. Do not
adopt partial geometry from that state. A separate audit now replays the exact
original blink/component steps in new copies and requires closed RGBA reproduction
before using its actual geometry. Original evidence remains intact.

## Checks so far

Current full suite:313passed,2existing deprecation warnings; Ruff passed. New
preparation CLI has11 tests, owner topology/transport has4; the latter is a
geometry diagnostic and not an automatic accepted repair. Claude follow-up's
first call failed DNS before response; the one allowed same consultation retry
uses normal approved network access, no tool execution or credential changes.

## Follow-up design review and geometry result

Claude follow-up retry completed successfully. Adopted: restored width changes the
painted profile; geometry and ink gates remain separate; per-key success does not
establish interpolation. Not adopted: a new spline/SOCP family with new epsilon,
radius and thickness constants, a blanket prohibition on native-domain evidence,
and an uncalibrated centroid approximation as a bound for the ridge extractor.
The existing predeclared quadratic family remained fixed. No raw consultation is
published or delegated as implementation.

Original Codex traced the closed-mouth loss to four-connected alpha islands and
minimum component area6: seven islands become one retained main region x9..45;
the discarded six endpoint islands have areas4/4/2/2/2/2. Actual Core UV/index
inspection and a pixel-identical atlas patch corroborate the36px domain. This is
not a downstream keyform squeeze. Fixing that separate source-coverage defect
would change the frozen baseline and is not mixed into the transition assay.

The fixed dual-quadratic offline field had maximum predicted ink distance.767091px,
no new visible-pose boundary crossings or triangle flips. After reviewing residuals,
curvature, source domain and Claude's cautions, authorized one fixed representation
render preflight in a new temporary directory: at most28images, unchanged five
cases plus literal full-open control. No automatic full-grid/candidate expansion.
This is an evidence-backed new representation after explicit root analysis, not
another parameter adjustment to the earlier failed B field.

Identity replays completed two closed renders, both RGBA-identical to prior evidence.
Actual closed lash geometry differs by at most.000153px, turn-left lashes/whites
by at most.129407px, eyebrows exactly unchanged. A texture/geometry cross-swap was
not done, so the closed95pixel residual is not assigned a single proven cause.
Three real deterministic repair regressions completed and were visually inspected;
arrival error for the injected4px attachment defect is.0176409px. All runtime locks
were released before handing the bounded mouth preflight to original Codex.

## Fixed quadratic actual preflight — independently passed, limited scope

Exactly26new actual images completed. Independent supervisor CLI replay confirmed
5/5 cases pass unchanged ink thresholds: .623863, .776418, .707222, .750556,
.754529px. Viewed the full composites and isolated upper/lower/cavity comparisons.
Normal interior widths are approximately1.4999..1.5046px, no longer the B field's
.5839px collapse. No new visible-pose crossings/flips or area collapses; static
isolated layers match B, neutral actual RGBA and literal full-open control match.
This accepts a fixed-pose preflight only, not a complete keyform representation.

Next acknowledged task: freeze this same representation and inspect complete
existing9x33grid/inter-key offline behavior, supported batch API and operation/
storage costs before actual consolidated authoring. Existing portable base is
about201MB; do not silently launch hundreds of history writes or edit its archive.
Original Codex owns reusable mouth math/tests; supervisor remains sole publisher.

Independent source-coverage visual: full54px prepared mouth and actual static
render at the same world rectangle show shortened ends.9.84826% of source alpha
mass and36/235 source pixels with alpha>=8 lie outside the proven x9..45 mesh
span. These are coverage diagnostics, not perceptual scores. Added the demonstrated
mesh/UV coverage gap to quality-checks.md without changing the frozen baseline.

Published stage69f9c3b8a2160f6836d9de287d3ef05b3850b207 successfully to the existing
origin/main; independent GitHub ref matches. Actions0, check-runs0, statuses[]:
no CI run is claimed. Full raw publication receipt remains local/ignored.
