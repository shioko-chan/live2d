# Expression presets, actions and physics

Use after the relevant artwork and parameter shapes exist. Read the actual model
parameters/ranges and the requested performance brief. Keep working authoring files
in `<project>/production/`. Store validated presets/actions in the performance
library so they survive model revisions and subsequent exports.

## Library workflow

```sh
l2d performance show PROJECT --revision REVISION
l2d performance schema expression
l2d performance schema motion
l2d performance schema library
l2d performance schema playback
l2d performance put-expression PROJECT smile smile.exp3.json --version 0
l2d performance put-motion PROJECT nod nod.motion3.json --group Custom --version 1
l2d performance history PROJECT
```

Use the returned library `version` for every next write, including physics changes;
do not assume the example versions match the current project. A stale write fails.
Each edit stores a content-addressed immutable snapshot tied to the built model
revision. A newly created model revision pins its parent's current snapshot,
including an empty library; later edits to the parent do not change the child.
Every use against a rebuilt rig validates the current parameter IDs/ranges.

`put-expression`/`put-motion` create or replace a named entry. `performance import`
replaces all library contents from JSON; `performance export PROJECT NEW_JSON`
writes a portable snapshot outside the project. `performance remove PROJECT
expression --name smile --version N` and the analogous `motion --group Custom`
remove entries in a new snapshot. Historical reads, preview and sampling accept
`--snapshot ID`. The normal `l2d export` automatically registers library assets in
the copied delivery; saved build files remain immutable.

## Expression design

Choose expressions that suit the character and request: for example a gentle
smile, broad smile, concern, anger or surprise. Record the intended eye/brow/mouth
appearance and any additional effect layer. Select values by rendered results;
copying slider numbers from a reference is not sufficient.

For facial shape design and driver-ownership checks, read
[facial-production.md](facial-production.md). Do not populate every preset with a
full default eye/mouth dictionary; write only intended channels, and explicitly
identify any expression that deliberately suppresses blinking or speech.

For each expression:

1. Render neutral and candidate values at the same scale, then inspect subtle/full
   intensity and at least the affected head-turn combinations.
2. If existing controls cannot produce the intended shape, fix layers/keyforms or
   record the missing control. Renaming a weak candidate does not fulfill the brief.
3. Choose how it combines with motion, blinking and lip sync. `Add` stores an
   offset, `Multiply` scales the current value, and `Overwrite` sets a value. An
   overwrite of eye opening or mouth opening can suppress other intended drivers.
4. Test switching into/out of the expression and returning to neutral in the
   target runtime when available. A neutral expression that adds zero does not
   itself clear an earlier expression; reset/removal belongs to the runtime.

Use `.exp3.json` with `performance put-expression`. Optional `Version: 3` is
accepted. The tool registers the name/file in `FileReferences.Expressions` during
export. For example, this is a candidate mouth-form offset, not a verified smile:

```json
{
  "Type": "Live2D Expression",
  "FadeInTime": 0.2,
  "FadeOutTime": 0.2,
  "Parameters": [{"Id": "ParamMouthForm", "Value": 0.3, "Blend": "Add"}]
}
```

Use only IDs present in the actual rig. For `Add`/`Multiply`, check the resulting
values on the intended motion/pose inputs, not just whether the stored number lies
inside the parameter range. Detect duplicate IDs and unintended driver conflicts.
See the official [expression format](https://github.com/Live2D/CubismSpecs/blob/master/FileFormats/exp3.json.md)
and [expression blending](https://docs.live2d.com/en/cubism-sdk-manual/expression/).

## Action design

For each action, record intent, duration, loop/one-shot behavior, affected controls,
key beats and transitions. Head/body action may need anticipation, an emphasized
beat and settling. Keep asymmetry and timing appropriate to the character.
Waving requires a suitable arm/hand rig; head sway cannot substitute for it.

Author `.motion3.json` parameter curves against existing IDs. A minimal one-second
head-pitch test illustrates the file contract; polish timing/easing for delivery:

```json
{
  "Version": 3,
  "Meta": {
    "Duration": 1.0, "Fps": 30, "Loop": false,
    "FadeInTime": 0.2, "FadeOutTime": 0.2,
    "CurveCount": 1, "TotalSegmentCount": 3, "TotalPointCount": 4
  },
  "Curves": [{
    "Target": "Parameter", "Id": "ParamAngleY",
    "Segments": [0.0, 0.0, 0, 0.3, -4.0, 0, 0.65, 2.0, 0, 1.0, 0.0]
  }]
}
```

The first pair is time/value; each `0` introduces a linear segment ending at the
next time/value pair. For smoother actions use authored easing/cubic curves with
correct controls and counts. Recompute curve, segment and point totals from the
actual data. `performance put-motion` checks metadata counts and stores the group;
export registers the files under `FileReferences.Motions`.
Follow the official [motion format](https://github.com/Live2D/CubismSpecs/blob/master/FileFormats/motion3.json.md).

Check increasing segment endpoint times, finite values, duration and parameter
ranges, including interpolation. Loops need compatible start/end values and motion
direction/speed. One-shot actions need a deliberate handoff to idle. Decide which
driver owns each eye/mouth/physics parameter; avoid competing authored motion and
physics outputs unless the runtime combination is deliberately tested.

Supported motion curves target actual parameters and include linear (`0`), cubic
Bezier (`1`), stepped (`2`) and inverse-stepped (`3`) segments. Unrestricted Bezier
sampling solves for time; range checks include interpolated extrema. Restricted
Bezier controls must use one-third/two-thirds time spacing. Unsupported `Model`
effect curves, `PartOpacity` curves, event data and audio dispatch are rejected,
not silently skipped.

## Play the authored performance

Use `l2d render` for individual poses. Use a playback JSON for authored animation:

```json
{
  "motion": "nod", "group": "Custom", "expression": "smile",
  "duration": 4, "fps": 24, "loop": true, "physics": true, "long_edge": 768
}
```

```sh
l2d performance sample PROJECT playback.json
l2d performance preview PROJECT playback.json
```

Omit `motion` for expression-only playback. To check transitions, use `clips`
instead of `motion`, and an `expressions` event list instead of a single expression:

```json
{
  "clips": [{"name": "nod", "group": "Custom", "start": 0, "end": 2}],
  "expressions": [{"name": "smile", "start": 0.5, "end": 2.5, "weight": 1}],
  "duration": 3, "fps": 24
}
```

Clips blend in list order, then expressions blend in list order, then optional
physics is evaluated. Motion/curve and expression fades use sine easing; `end`
is the end of fade-out. Nonloop clips return to model defaults after their end;
`hold: true` retains the endpoint when no explicit end is supplied. Loop clips
wrap at exact `Duration` and do not add an SDK-specific correction frame. Inspect
the seam; looping does not repair an incompatible first/last shape.

Sampling returns `frames: [{time, parameters}]`, including the endpoint. Preview
renders those values on a disposable PSD2Live CPU workspace and saves an MP4,
`parameters.json` trace and `playback.json` receipt under a unique preview directory.
The receipt identifies the library snapshot and request. The old `preview-video`
command remains the eight-second diagnostic sequence.

These previews establish the implemented parameter composition and visible CPU
rendering. They do not claim numerical parity with the SDK expression/motion
manager or establish target-app behavior. In particular, inspect independently
overlapping expression layers before adopting the same schedule in an application.

## Physics tuning

First verify each output parameter's geometry by manually rendering its range.
Then connect the intended input/output and tune amplitude, delay and settling.
Component binding and rig `physics_put` retain their simple sway groups. Use the
versioned physics library to author multi-particle chains, multiple inputs/outputs,
normalization, forces, weights, reflection and output vertex indices:

```sh
l2d physics show PROJECT
l2d physics schema authoring
l2d physics schema document
l2d physics compose chain-authoring.json hair.physics3.json
l2d physics put PROJECT hair.physics3.json --version N
l2d performance preview PROJECT playback.json --physics
```

`compose` creates correct metadata counts from `groups`, optional names/forces and
FPS; `put` validates the complete document against actual parameters. Inspect the
schema before authoring. Existing generated physics is the starting point when
there is no authored physics snapshot. A library physics document replaces the
whole exported physics document, so retain any existing groups still required.

The CPU preview runs a stateful constrained-particle simulation with fixed time
steps after motion/expression sampling. Its receipt says `simulated` and identifies
the solver, particles, steps and clamping. It is an authoring preview, not the
official Cubism SDK physics solver; no numerical equivalence is claimed.

Inspect neutral, abrupt input changes, slow motion and settling under continuous
playback when available. Root attachment, overshoot, collision-like overlap and
cross-component synchronization matter. A valid `physics3.json` and endpoint
renders alone do not establish natural temporal response.

## Package and verify without changing revisions

1. Finish/reinspect the chosen built revision and export to a new destination
   outside the project store using `l2d export`.
2. The export includes the exact performance snapshot and standard exp3/motion3/
   physics3 sidecars. Expression entries merge by name; authored motion groups
   replace generated groups of the same name. Use a separate group such as
   `Custom` when preserving a generated group. Other references stay intact.
3. Recheck the exported structural report and review the saved motion trace/video.
   IDs, ranges, blend math and curve metadata validation do not establish that the
   corresponding rig shapes look good. Judge the pixels separately.
4. When an actual official Core library is available, run
   `l2d verify-playback BUNDLE TRACE_JSON --core LIB` with the saved frame trace.
   This drives every supplied frame through Core and checks parameter results and
   finite drawable vertices. Keep that report separate from CPU solver evidence.
   It does not run the SDK motion/expression/physics managers, render target-app
   pixels or certify application compatibility.
5. If Core is unavailable, report official continuous runtime verification as
   `not_evaluated`; the implemented checker is not evidence of a successful run.
   No local official Core continuous-playback result has been established by this
   tool implementation. Record a real run only after checking its report.

For entry registration use the official
[model file format](https://github.com/Live2D/CubismSpecs/blob/master/FileFormats/model3.json.md).
Deliver the requested usable presets/actions with their evidence and clearly mark
any remaining authoring candidate or unsupported behavior.
