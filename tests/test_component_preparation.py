import math
import subprocess
import sys
from pathlib import Path

from PIL import Image
import pytest

from live2d_pipeline.component_preparation import prepare_uniform


def test_iris_distortion_is_removed_without_cropping_hidden_extent():
    source = Image.new("RGBA", (506, 678), (40, 100, 140, 255))
    source.putpixel((253, 677), (255, 255, 255, 255))
    old = source.tobytes()
    output, record = prepare_uniform(source, (0, 0, 506, 678), (448, 372.5), width=44)
    assert output.size == (44, 59)
    assert record["placement"] == [426, 343, 44, 59]
    assert record["actual_canvas_anchor"] == [448, 372.5]
    assert abs(record["aspect_ratio_factor_after_integer_rounding"] - 1) < .001
    assert output.getchannel("A").getbbox() == (0, 0, 44, 59)
    assert source.tobytes() == old


def test_transparent_rgb_does_not_colour_filtered_edges():
    source = Image.new("RGBA", (40, 80), (0, 255, 0, 0))
    for y in range(10, 70):
        for x in range(10, 30):
            source.putpixel((x, y), (240, 0, 0, 255))
    output, _ = prepare_uniform(source, (0, 0, 40, 80), (10, 20), width=10)
    assert all(g == b == 0 for r, g, b, a in output.get_flattened_data() if a)


def test_no_resampling_preserves_low_alpha_rgb_exactly():
    source = Image.new("RGBA", (5, 10), (241, 13, 77, 7))
    output, record = prepare_uniform(source, (0, 0, 5, 10), (2.5, 5), width=5)
    assert output.tobytes() == source.tobytes()
    assert record["resampling"] == "none"


def test_refuses_a_feature_that_disappears_during_resampling():
    source = Image.new("RGBA", (100, 100))
    source.putpixel((50, 50), (255, 0, 0, 255))
    with pytest.raises(ValueError, match="removed all visible alpha"):
        prepare_uniform(source, (0, 0, 100, 100), (.5, .5), width=1)


def test_custom_anchor_reports_integer_rounding_instead_of_hiding_shift():
    _, record = prepare_uniform(Image.new("RGBA", (20, 30), "red"), (0, 0, 20, 30),
                                (100.2, 200.1), height=15, source_anchor=(.25, .2))
    assert record["actual_canvas_anchor"] == [100.5, 200.0]
    assert record["anchor_rounding_error"] == pytest.approx([.3, -.1])


@pytest.mark.parametrize("kwargs", [{}, {"width": 44, "height": 43}, {"width": 0}, {"width": True}])
def test_no_implicit_stretch_or_invalid_dimension(kwargs):
    with pytest.raises(ValueError):
        prepare_uniform(Image.new("RGBA", (506, 678), "red"), (0, 0, 506, 678), (0, 0), **kwargs)


def test_rejects_out_of_bounds_or_empty_crop_and_nonfinite_anchor():
    image = Image.new("RGBA", (10, 10))
    for crop, anchor in [((-1, 0, 10, 10), (0, 0)), ((0, 0, 10, 10), (0, 0)),
                         ((0, 0, 10, 10), (math.nan, 0))]:
        with pytest.raises(ValueError):
            prepare_uniform(image, crop, anchor, width=5)


def test_cli_writes_receipt_and_refuses_overwrite(tmp_path):
    source, dest = tmp_path / "source.png", tmp_path / "prepared.png"
    Image.new("RGBA", (20, 30), "red").save(source)
    script = Path(__file__).resolve().parents[1] / "scripts/prepare_component.py"
    command = [sys.executable, str(script), str(source), str(dest), "--crop", "0", "0", "20", "30",
               "--width", "10", "--canvas-anchor", "5", "7.5"]
    assert subprocess.run(command, capture_output=True).returncode == 0
    original = dest.read_bytes()
    assert dest.with_suffix(".json").is_file()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert dest.read_bytes() == original
