# Independent supervision — 2026-10-22 UTC

## Authorization and ownership

User delegated supervision, real visual inspection, coordination with the existing Codex implementer, and project-only conceptual consultation with installed Claude Code. Original scope prohibited publishing/pushing; later explicit user instruction “记得阶段性提交推送git” authorized milestone commits and pushes to the existing remote. No force-push, merging, deployment, purchases, new accounts, permission changes or approval bypass. No questions while the user is asleep; continue authorized reversible work.

Starting commit: `cb1e217`. Working tree was clean. No applicable AGENTS.md found at repo/ancestor paths; `.agents` is empty in this environment. Read installed live2d-production skill and repository tools/components/quality references. No session files or personal memories read.

Existing implementer confirmed using supported app tools: `01a139f0-52ea-7a52-b28a-2cad58253583`, title “查找 Live2D 模型下载方式”, local host, same cwd. It was idle after a 16-image Mio defect regression. Dispatched one implementation task: visibility/render consistency, generated lip outlines, independently moving upper/lower eye contours. Implementer owns source/skill/tests and its repair directory. Supervisor writes only this directory and `projects/mio-starlibrary-20261015/production/independent-supervision-20261022/`.

Claude Code 2.1.284 installed and logged in; supported `claude agents --json --cwd ...` returned no active project session. Will use a bounded print-mode consultation with tools disabled, no session persistence, no implementation delegation. `tmux list-sessions` could not access the socket (Operation not permitted); no terminal injection attempted and no bypass/retry planned.

## Acceptance criteria fixed before repairs

- Identity: compare original reference, assembled materials, neutral rig at equal viewing scale; document changed face/hair/costume/detail instead of claiming fidelity from alpha or hashes.
- Coverage: every promised motion has correct material content and mesh ownership; include generated meshes, not just named layers. Count is descriptive only.
- Occlusion: inspect isolated skin/hair/clothing beneath moving parts and displacement renders for holes, duplicated contours, orphan attachments.
- Alpha: real RGBA, nonempty layers, light/dark previews; distinguish legitimate clipped waist from accidental hair/crown clipping or halos.
- Hierarchy/naming/alignment: unique stable IDs and draw order, native dimensions versus placement, semantic and parent ownership, material composite versus neutral render.
- Face: blink 0/.25/.5/.75/1, both sides, turn combination; mouth 0/intermediate/1. No separated lower eye contour, vanishing lash seam or extra closed-mouth outline. Failed basic closure blocks polished-expression acceptance.
- Export: actual model3 references, MOC3, textures, sources and exact performance snapshot; run official Core on final candidate trace. Keep structural/Core/visual/application outcomes separate.
- Reproducibility: project-required lint/tests, saved-input regression and safe real-sample execution; preserve baseline hashes and head. No local AI or new paid image generation under this scope.

## Round 1: baseline

Viewed `skill-gate-validation-20261023/comparison.png`: original closed eyes visibly retain detached lower contours; disabling generated lips removes a second dark mouth contour; whole-lash compression leaves broken thin lines. Independently confirms rejection of the prior trial. This is not a new candidate or an independent rerun yet.

Current official head: `358614b7f88146f5`, accepted null. Prior candidate `eeab75a07433487c` is experimental; official head and immutable assets must remain intact.

Implementation and fresh test results pending. Record each later round below.

### Independent baseline results

- Full pytest: **282 passed**, 2 deprecation warnings, 33.81s; Ruff: **passed**. Logs here. Initial sandbox calls failed because Nix attempted a normal cache write outside the writable roots; normal `require_escalated` requests were approved and succeeded. No permission or configuration changed.
- Repository saved-input regression: 6 available, 2 deliberately marked not_fixture; this verifies stored inputs, not new repair execution.
- Fresh official Core check on existing delivered model + 18s trace: **passed**, 325 frames, 20 drawables, 23 parameters, 3,223,350 vertex checks. No rendered pixel / target-application / SDK physics claim.
- Independent 17-layer structural audit: actual RGBA/nonempty images, all dimensions match placement, bounds in canvas, unique IDs/orders, neutral reassembly pixel-identical to saved composite (closed-mouth state excludes alternate mouth_open layer). Initial diagnostic reassembly wrongly overlaid both mouth states; corrected audit state selection, not production data.
- Viewed original reference, assembly, equal-scale face comparison, hair crops on light/dark backgrounds, complete dark composite, face_base, lashes_left. Skin underlayer is complete in inspected region; isolated lashes visibly contain upper and lower contours. Alpha edge suspicion from raw transparent display was **not reproduced** on flattened light/dark backgrounds; do not report it as a defect.
- Identity fidelity: **failed for close-source fidelity**. Iris shape/hue/highlights and eyelash shape changed; hair silhouette and garment ornament layout differ. Broad character palette/theme preserved. Current brief prioritizes original artwork quality; these observations are not negated by clean alpha/structure. No fresh image generation was performed in this bounded inspection/repair round; this is a recorded scope limit, not a claim that the user's authorization inherently forbids all future image-tool use.
- `l2d inspect` encountered the implementer's live project lock. It stopped without changing data; postpone until the implementer releases it. Immutable source files were read directly for the independent audit.

### Claude consultation and decisions

Initial restricted CLI consultation exited 1 with EAI_AGAIN, without a response. Normal escalation for the explicitly authorized, project-only conceptual consultation was approved; it succeeded. Full minimal prompt and response retained here; tools disabled, no session persistence, no implementation delegated.

Claude recommends upper/lower texture separation where clean, otherwise a local mesh solution preserving eyelash thickness and independently moving contours; real intermediate blinks determine acceptance. Generated lips should fade in after closed-mouth states with a MouthOpenY × MouthForm pose grid. Identity/alpha metrics are screening only. Shared these conclusions with implementer. Reject two over-broad suggestions as acceptance rules: nonzero bbox alpha does not by itself prove accidental clipping (waist/crop context matters), and official Core does not render pixels. Do not calibrate identity thresholds to excuse an already drifted baseline.

Implementer is actively fixing renderer visibility and authoring a geometry-based face candidate in `production/face-repair-20261023/`; supervisor does not modify those files.

## Round 2: implementation candidate and independent rejection

Implementer produced candidate `849ea6166ad045d1`, based on `eeab75a07433487c`. Renderer visibility patch uses the existing inherited visibility function; fresh real-image visibility test shows changed bbox (376,337,493,396) and exact restoration. Source/hash/head unchanged in its report. This is implementer-run evidence, independently read and later to be rerun.

Viewed actual full images `blink-0-turn-0`, `blink-0.5-turn-14`, `mouth-0.25-turn-0` and full blink/mouth contact sheets. Local mesh trajectories preserve continuous closed lashes and eliminate the detached lower arc in the displayed 0/.25/.5/.75/1 × -14/0/14 head-X grid. No entire-lash compression used. Facial identity remains inherited, not restored to original reference.

**Rejected mouth transition** at opening .25: a closed-mouth fine line remains above an extra dark pair of mouth contours. The 0 endpoint alone would conceal this failure. Sent exact image and pose to implementer; it is authoring second candidate `f83489945b5448c1` to release the static closed-mouth layer earlier. Eye improvement retained.

Attempted safe real-image cloned regressions for earrings and missing-mouth inputs; the global task lock was held by active implementation, so execution stopped before clones and did not interfere. Will retry only after the owner releases the lock. Direct PSD inspection outside the devShell lacked libstdc++; rerun through normal approved Nix shell, no dependency installation.

## Round 3 and final independent checks

Second Claude discussion agrees that crossfading separated curves cannot remove a double mouth, and proposes isolated static/lip/cavity rendering plus local y-position measurements. Its response is advisory, not image evidence. Implementer had already started a targeted alignment candidate; completed it without interruption, then stopped new candidates. Actual round count is three and is visible in both reports.

Third candidate `1bf0686147f84cca` adds a child mouth alignment warp with -6.5px initial shift decreasing to zero at full opening. Independently viewed all 54 mouth combinations, nearest-neighbour 3× mouth details, single-eye and smile sheets. Large separation decreases, but at .025/.05 with MouthForm -1 the two curves still differ in width/curvature. **Mouth transition remains failed.** Keep improved eyes and the experimental candidate; do not promote it.

After implementer explicitly released the task lock:

- Real deterministic repair regressions completed in fresh clones `4c89152a2fae4539a65c795677c38519` and `5e1d4080cab64f6fb6b0abb6740c6f95`, with no head changes. Viewed source layers, split earring layers, imported mouth and composites. This tests reuse of actual saved source PNGs, not new image generation or dynamic rig quality.
- Independent real visibility run on a different mesh (`ArtMeshMouthClose`) in the third candidate passed: changed bbox [502,474,538,486], exact RGBA restoration, source/head unchanged.
- Final full test suite **283 passed**, 2 existing warnings, 32.26s; full Ruff (including inspection scripts), skill format validation and diff whitespace checks pass.
- Fresh third-candidate Core run: **76 samples passed**, 753768 vertex checks. Actual exported model references all resolve; 17-layer PSD is readable, CMO3 and PSD2Live sources exist. No editor/target-app opening performed.
- `l2d inspect` now completed with material and rig stage integrity **pass**; quality remains visual_quality=not_evaluated, as appropriate.
- Independent comparison against initial snapshot: **96 baseline files unchanged**, head still 358614b7f88146f5, accepted null.

Git milestone: user explicitly authorized commit/push later in the task. Supervisor is sole committer; implementer confirms no stage/commit/push. Current branch main; configured origin is https://github.com/shioko-chan/live2d.git; remote main was independently read as the starting cb1e21728ff792e49de05d5c56815acbf33e14cc. Only this round's code/skill/test/inspection records will be included; generated production assets retain the existing ignore policy. Commit/push and CI confirmation follow; full outcome in RESULT.md and local publication.json.
