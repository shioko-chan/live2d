import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from live2d_pipeline.setup import prepare_rig_visibility


def test_visibility_patch_is_repeatable_and_rejects_drift(tmp_path):
    root = Path(__file__).resolve().parents[1]
    relative = "src/main/kotlin/io/github/psd2live/ui/RigCanvasSupport.kt"
    cached = root / ".runtime/sources/psd2live"
    if not (cached / ".git").exists():
        pytest.skip("pinned upstream checkout not prepared")
    # The pinned original, not the already patched working copy.
    baseline = subprocess.check_output(["git", "-C", str(cached), "show", "HEAD:" + relative])
    runtime = tmp_path / "runtime"
    repo = runtime / "sources/psd2live"
    target = repo / relative
    target.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    target.write_bytes(baseline)
    config = SimpleNamespace(root=root, runtime=runtime)
    prepare_rig_visibility(config)
    first = target.read_bytes()
    assert first != baseline
    prepare_rig_visibility(config)
    assert target.read_bytes() == first
    target.write_text("incompatible source\n")
    with pytest.raises(subprocess.CalledProcessError):
        prepare_rig_visibility(config)
    assert target.read_text() == "incompatible source\n"
