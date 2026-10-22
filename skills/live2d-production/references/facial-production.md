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
