from pathlib import Path
import math
import pytest
from PIL import Image
from live2d_pipeline.mouth_closure import (
    Curve,
    Frame,
    dark_ridge,
    ribbon_curve,
    static_displacement,
    dynamic_displacement,
    smooth_weight,
    compare_curves,
)

FIXTURE = Path(__file__).parent / "fixtures/mouth_closure"


def test_real_isolated_contours_reproduce_width_mismatch_without_peak_counting():
    closed = dark_ridge(Image.open(FIXTURE / "closed.png"), (464, 448, 120, 80))
    generated = dark_ridge(Image.open(FIXTURE / "upper.png"), (464, 448, 120, 80))
    assert closed.width == pytest.approx(35.75)
    assert generated.width / closed.width > 1.3
    # Same-u correction actually maps the generated centre to the painted centre.
    for i in range(101):
        u = i / 100
        point = generated.sample(u)
        delta = dynamic_displacement(
            closed, generated, (generated.points[0][0], generated.points[-1][0]), point, 0
        )
        assert tuple(a + b for a, b in zip(point, delta)) == pytest.approx(closed.sample(u))
    assert compare_curves(closed, closed)["max_correspondence_distance"] == 0


def test_neutral_static_and_full_open_are_preserved_for_every_form():
    c = Curve(((0.0, 0.0), (1.0, 0.2), (2.0, 0.0)))
    g = Curve(((-1.0, 1.0), (1.0, 1.5), (3.0, 1.0)))
    for u in [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]:
        assert static_displacement(g, g, u) == (0, 0)
        for form in [-1, -0.5, 0, 0.5, 1]:
            point = (u * 4 - 1, 2 + form)
            assert dynamic_displacement(c, g, (-1, 3), point, 1) == (0, 0)


def test_common_field_preserves_upper_lower_aperture_and_is_continuous():
    c = Curve(((0.0, 0.0), (1.0, 0.25), (2.0, 0.0)))
    g = Curve(((-1.0, 2.0), (1.0, 3.0), (3.0, 2.0)))
    previous = None
    for i in range(1001):
        opening = i / 1000
        upper, lower = (0.7, 2 - opening), (0.7, 2 + opening)
        du = dynamic_displacement(c, g, (-1, 3), upper, opening)
        dl = dynamic_displacement(c, g, (-1, 3), lower, opening)
        assert du == dl
        assert (lower[1] + dl[1]) - (upper[1] + du[1]) == pytest.approx(2 * opening)
        if previous is not None:
            assert math.dist(du, previous) < 0.01
        previous = du
    assert smooth_weight(0) == 1 and smooth_weight(1) == 0


def test_frame_mapping_rejects_wrong_correspondence_and_nonaffine_neutral():
    local = [(0, 0), (1, 0), (0, 1), (1, 1)]
    canvas = [(10, 20), (110, 20), (10, 70), (110, 70)]
    frame = Frame.fit(local, canvas)
    assert frame.local((60, 45)) == pytest.approx((0.5, 0.5))
    with pytest.raises(ValueError, match="axis aligned"):
        Frame.fit(local, canvas[:-1] + [(115, 70)])
    with pytest.raises(ValueError):
        Frame.fit(local[:1], canvas[:1])


def test_ribbon_excludes_caps_and_rejects_an_open_two_branch_curve():
    points = [(0, -0.1), (0, 0.1), (1, 0.9), (1, 1.1), (2, -0.1), (2, 0.1), (999, 999)]
    assert ribbon_curve(points, 6).points == ((0, 0), (1, 1), (2, 0))
    with pytest.raises(ValueError, match="single closed"):
        ribbon_curve(points[:4] + [(0, 3), (0, 4)], 6)


def test_missing_or_invalid_input_cannot_pass_by_hiding():
    with pytest.raises(ValueError, match="No visible"):
        dark_ridge(Image.new("RGBA", (16, 16)), (0, 0, 16, 16))
    with pytest.raises(ValueError):
        Curve(((1.0, 0.0), (0.0, 0.0)))
    with pytest.raises(ValueError):
        Curve(((0.0, 0.0), (1.0, float("nan"))))
    with pytest.raises(ValueError):
        smooth_weight(1.01)


@pytest.mark.parametrize("profile,width", [("gaussian", .35), ("plateau", .75), ("plateau", 1.5), ("plateau", 3)])
def test_ridge_centres_same_geometry_despite_different_stroke_profiles(profile, width):
    # All controls have the exact same physical centreline. The previous argmax
    # implementation reported y=18.75 for the thick plateau instead of y=20.
    image = Image.new("RGBA", (24, 160))
    for y in range(image.height):
        distance = abs((y + .5) / 4 - 20)
        opacity = math.exp(-.5 * (distance / width) ** 2) if profile == "gaussian" else float(distance <= width / 2)
        for x in range(image.width):
            image.putpixel((x, y), (90, 30, 20, round(255 * opacity)))
    curve = dark_ridge(image, (0, 0, 6, 40))
    assert curve.sample(.5)[1] == pytest.approx(20, abs=1e-10)


def test_ridge_rejects_disconnected_equal_peaks_instead_of_inventing_a_centre():
    image = Image.new("RGBA", (16, 16))
    for x in range(16):
        image.putpixel((x, 3), (40, 10, 10, 255))
        image.putpixel((x, 11), (40, 10, 10, 255))
    with pytest.raises(ValueError, match="separated equally dark ridges"):
        dark_ridge(image, (0, 0, 16, 16))


def test_measure_cli_records_ambiguous_input_as_unmeasurable(tmp_path):
    import json
    import subprocess
    import sys

    image = Image.new("RGBA", (16, 16))
    for x in range(16):
        for y in [3, 11]:
            image.putpixel((x, y), (40, 10, 10, 255))
    for name in ["closed", "upper", "lower"]:
        image.save(tmp_path / (name + ".png"))
    output = tmp_path / "report.json"
    script = Path(__file__).resolve().parents[1] / "scripts/measure_mouth_closure.py"
    result = subprocess.run([
        sys.executable, str(script), str(tmp_path), "--viewport", "0", "0", "16", "16",
        "--max-distance", "1", "--max-width-error", ".1", "--output", str(output),
    ], capture_output=True)
    assert result.returncode == 2
    report = json.loads(output.read_text())
    assert report["status"] == "unmeasurable" and report["passed"] is False
    assert report["measurement_version"] == "ridge-plateau-v2"


def test_collinear_rotated_frame_and_nonfinite_inputs_rejected():
    with pytest.raises(ValueError, match="rank"):
        Frame.fit([(0, 0), (1, 1), (2, 2)], [(0, 0), (0.5, 1.5), (1, 3)])
    for invalid in [float("nan"), float("inf"), 0, -1]:
        with pytest.raises(ValueError):
            Frame.fit([(0, 0), (1, 0), (0, 1)], [(0, 0), (1, 0), (0, 1)], invalid)
    with pytest.raises(ValueError):
        Curve(((0, 0), (1, 1))).coordinate(float("nan"))


def test_non_neutral_form_uses_same_u_once_for_width_and_curvature():
    c0 = Curve(((0, 0), (1, 0.3), (2, 0)))
    g0 = Curve(((-1, 2), (1, 2.5), (3, 2)))
    gf = Curve(((-2, 1), (1, 3.5), (4, 1)))
    for i in range(101):
        u = i / 100
        static = tuple(a + b for a, b in zip(c0.sample(u), static_displacement(g0, gf, u)))
        dynamic = tuple(
            a + b for a, b in zip(gf.sample(u), dynamic_displacement(c0, g0, (-2, 4), gf.sample(u), 0))
        )
        assert static == pytest.approx(dynamic)


def test_measure_cli_rejects_real_failure_and_accepts_identical_contours(tmp_path):
    import subprocess
    import sys
    import json
    import shutil

    root = Path(__file__).resolve().parents[1]
    for name in ["closed", "upper", "lower"]:
        shutil.copyfile(
            FIXTURE / ("closed.png" if name == "closed" else "upper.png"), tmp_path / (name + ".png")
        )
    command = [
        sys.executable,
        str(root / "scripts/measure_mouth_closure.py"),
        str(tmp_path),
        "--viewport",
        "464",
        "448",
        "120",
        "80",
        "--max-distance",
        "1",
        "--max-width-error",
        ".1",
        "--output",
        str(tmp_path / "result.json"),
    ]
    assert subprocess.run(command, capture_output=True).returncode == 1
    assert json.loads((tmp_path / "result.json").read_text())["passed"] is False
    for name in ["upper", "lower"]:
        shutil.copyfile(FIXTURE / "closed.png", tmp_path / (name + ".png"))
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert json.loads((tmp_path / "result.json").read_text())["max_distance"] == 0
