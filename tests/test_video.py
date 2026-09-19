import json
import subprocess

from PIL import Image
import pytest

from live2d_pipeline.video import DURATION, FPS, encode, generate, motion
from live2d_pipeline.schema import Plan
from live2d_pipeline.store import Store


def test_motion_is_bounded_continuous_loop_and_blinks():
    params = {
        key: {"min": low, "max": high, "default": default}
        for key, low, high, default in [
            ("ParamAngleX", -30, 30, 0),
            ("ParamEyeLOpen", 0, 1, 1),
            ("ParamMouthOpenY", 0, 1, 0),
            ("ParamComponent_bow", -1, 1, 0),
            ("ParamVisible_bow", 0, 1, 1),
        ]
    }
    poses = [motion(params, frame / FPS) for frame in range(DURATION * FPS + 1)]
    assert poses[0] == pytest.approx(poses[-1])
    assert motion(params, 1.5)["ParamEyeLOpen"] == 0
    assert any(p["ParamMouthOpenY"] > 0.5 for p in poses)
    for pose in poses:
        assert pose["ParamVisible_bow"] == 1
        assert all(params[key]["min"] <= value <= params[key]["max"] for key, value in pose.items())
    assert max(abs(a["ParamAngleX"] - b["ParamAngleX"]) for a, b in zip(poses, poses[1:])) < 1


def test_encode_browser_mp4_even_dimensions(tmp_path):
    for i in range(3):
        Image.new("RGB", (31, 47), (i * 100, 0, 0)).save(tmp_path / f"{i:04d}.png")
    target = tmp_path / "preview.mp4"
    encode(tmp_path, target, tmp_path / "encode.log")
    info = json.loads(
        subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target)])
    )["streams"][0]
    assert (info["codec_name"], info["pix_fmt"], info["width"], info["height"]) == ("h264", "yuv420p", 32, 48)
    assert info["nb_frames"] == "3"
    assert info["r_frame_rate"] == "24/1"


def test_unbuilt_revision_cannot_generate(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    store.create(Plan(request="draft", prompt="draft"))
    with pytest.raises(ValueError, match="完成模型构建"):
        generate(None, store)
