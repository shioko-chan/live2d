# Validate runtime early and after changes

Use for new production, model improvement and compatibility questions. A production
check has four separate outcomes: export structure, Core geometry, rendered visual
behavior, and target-application behavior. Record them separately.

## Find the supplied library

The project devShell packages Cubism Core from a fixed SDK version and source hash
and exports `CUBISM_CORE_LIBRARY`; `.envrc` loads this flake. Enter the development
shell before discovery. For GC retention outside direnv, use
`nix develop --inputs-from . --profile .direnv/live2d-devshell`; the profile retains the shell closure
while it exists. The pinned derivation can rebuild the package after collection.
Do not use a manually discovered store path as the project dependency.
`--inputs-from .` also resolves auxiliary nixpkgs lookups from the project lock
instead of an unrelated global registry; use it for reproducible offline commands.

1. Check the user's explicit path and `CUBISM_CORE_LIBRARY` in the execution
   environment, including the configured development shell. A variable missing in
   the current shell does not imply the library is absent from the machine.
2. Inspect the project's Nix/configuration references or the user-specified package
   location. On NixOS, a focused `/nix/store/*cubism*/lib/libLive2DCubismCore.so`
   lookup can resolve an installed package; avoid a recursive whole-machine scan.
3. Verify the selected file exists. If several versions are present, choose the
   configured one or record the version/path selected for the intended export.
   Do not hardcode a past machine's store hash into the skill or shared config.
4. If no usable library is found, list the locations checked and report “not found
   in the checked locations.” Do not equate that with “not installed.” No automatic
   download or licensing action is implied.

`l2d verify-playback` accepts either explicit `--core PATH` (takes precedence) or
`CUBISM_CORE_LIBRARY`. The latter must be set in the shell running the command.

## Run on the actual artifact

For an existing model use its export before reworking art. For new production use
the first usable export before detailed keyform/physics polish. A test on another
model establishes tool operability only. Use a trace containing defaults, intended
control extremes/intermediates and representative combinations. Once authored
playback exists, use its trace; it need not exist for the first compatibility check.

```sh
# Use the project's prepared Nix environment on NixOS.
l2d verify-playback /absolute/export/model /absolute/trace.json \
  --core /absolute/libLive2DCubismCore.so > /absolute/production/core-check.json
```

The trace contract is `{"frames":[{"time":0,"parameters":{"ParamAngleX":0}}]}`:
finite parameter values, nonnegative strictly increasing times, real parameter IDs
and ranges. Unspecified controls use model defaults. Retain the exact trace and
report with model/Core hashes. A Core rejection is an export/compatibility defect:
resolve it or explicitly stop model polish at that blocker; independent preparation
can continue. Do not replace failed checks with a structural-pass claim.

Repeat against the final delivery and final trace after structure/keyform changes.
A passed check establishes loading and finite geometry for those sampled updates.
It does not run SDK expression/motion/physics managers, render pixels, or prove
that interpolated shapes look good. Preserve `runtime_verified: false` where the
checker reports this narrower scope.

## Application evidence

When the target application is available, inspect its actual loaded model early:
textures/masks/order, neutral face, eye/mouth controls and imported physics. For
editable-source delivery, separately open the cmo3 in an appropriate editor;
Core loading moc3 does not test cmo3. Later inspect tracking plus blink/lip sync,
expression activation/release, action transitions, loop seam and settling.
Record app/version, model revision and observed failures. If unavailable, state the
specific remaining check and continue supported work; do not present an unavailable
app as a failed model. Never infer target-app success from CPU preview footage.
