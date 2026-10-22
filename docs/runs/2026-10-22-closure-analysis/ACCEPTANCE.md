# Frozen acceptance before the structural candidate

Diagnosis: `production/closure-geometry-analysis-20261022/RESULT.md` and
`STRUCTURE.md` in the Mio project. One structural candidate starts from
`f83489945b5448c1`, preserving the eye work and excluding the later translation
compensation. Formal head stays `358614b7f88146f5`.

## Geometry and visible result

- Use one parameterized closure curve and shared correspondence for static mouth,
  generated upper/lower lip and cavity. Neutral static displacement is exactly
  zero. Dynamic correction vanishes at full opening. Apply head transforms once.
- Keep the existing opacity curves during this test. Shortening the fade or hiding
  an offending component cannot pass the forced-visible geometry check.
- The fixed failure is Open .05/Form -1/turn 0. Also check the full previously
  recorded Form -1/0/1, turn 0/14 set, and fade interiors .025/.05/.075.
- Nominal maximum static-to-dynamic-midline distance: **at most 1 canvas pixel**.
  Relative visible-stroke width disagreement: **at most 10%**. Measure each
  component independently, with fixed darkness threshold and viewport. Report
  raster uncertainty separately; do not relax limits after seeing the candidate.
- At Open 0 inspect forced-visible closure too. At larger openings preserve normal
  upper/lower boundaries and cavity coverage; do not require all dark lines to
  merge. Actual composites must have no extra static seam during the crossfade.
- These are case-specific engineering gates, not universal artistic metrics.

## Preservation and interpolation

- Compare Form 0/Open 0 and full opening at each Form against the previous
  baseline; expected RGBA identity at these preserved endpoints.
- Preserve eye/half-blink/left-wink/right-wink and turn combinations. Record any
  intended change rather than silently accepting a pixel difference.
- Inspect middle Forms -.5/.5 and Open .075/.15/.25/.5/.75, plus the existing
  modest head-turn brief (0 and ±14°, paired with recorded Y values).
- Dense deterministic geometry sampling and a bounded ordered real-render sweep
  must show no new discontinuity. Numerical continuity is not natural-motion
  acceptance. No audio/tracking or target-application claim follows from a sweep.
- Validate the exact new export with official Core, recheck all bundle references,
  run relevant tests and the existing real regression cases, and inspect images.

## Scope and budget

This is a new evidence-backed structural step authorized by the user's continued
request, not a fourth blind threshold trial. Build one candidate after fixing the
data flow; technical authoring errors may be repaired before its evaluation.
If it fails, retain the result and identify the actual failed assumption. No
automatic parameter search, new paid generation, or promotion to formal head.

Update the reusable procedure/tests with the known failure and the measured cause.
Identity provenance must separately describe source generation and preparation;
the 37% iris anisotropy is established, while reference identity remains failed.
All Git publication stays with the supervisor after implementation releases files.
