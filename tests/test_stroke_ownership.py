import math
import pytest
from live2d_pipeline.mouth_closure import (
    Curve,
    StrokeOwnership,
    owned_stroke_displacement,
    owned_cavity_displacement,
    stroke_transport_diagnostics,
)


def fixture():
    t = StrokeOwnership(5, 1)
    cavity = [(float(c), float(row)) for c in range(5) for row in range(3)]
    centers = [cavity[3 * c + 2 * h] for c, h in zip(t.path_columns, t.path_halves(0))]
    points = []
    for x, y in centers:
        points.extend([(x, y - 0.1), (x, y + 0.1)])
    for owner, segments in [(0, 24), (len(centers) - 1, 24), (1, 24), (5, 24)]:
        x, y = centers[owner]
        points.append((x, y))
        points.extend(
            (x + 0.1 * math.cos(2 * math.pi * i / segments), y + 0.1 * math.sin(2 * math.pi * i / segments))
            for i in range(segments + 1)
        )
    return t, points, cavity


def test_crossed_path_uses_source_column_not_path_index_or_current_edge_x():
    t = StrokeOwnership(257, 10)
    assert len(t.path_columns) == 277 and len(t.vertex_owners) == 658 and len(t.triangles()) == 648
    assert t.path_columns[:11] == tuple(range(10, -1, -1))
    assert t.path_columns[-10:] == tuple(range(255, 245, -1))
    assert t.path_columns[t.vertex_owners[554]] == 10
    assert t.path_columns[t.vertex_owners[580]] == 246
    assert t.path_columns[t.vertex_owners[606]] == 0
    assert t.path_columns[t.vertex_owners[632]] == 256
    assert t.triangles()[:4] == ((0, 1, 2), (1, 3, 2), (2, 3, 4), (3, 5, 4))
    assert max(max(x) for x in t.triangles()) == 657


def test_actual_ownership_requires_centres_to_match_not_only_counts():
    t, points, cavity = fixture()
    assert t.ownership_errors(points, cavity, 0) == {"path_to_cavity_max": 0, "patch_to_owner_max": 0}
    bad = list(points)
    bad[2 * len(t.path_columns)] = (999, 999)
    assert t.ownership_errors(bad, cavity, 0)["patch_to_owner_max"] > 100
    with pytest.raises(ValueError):
        t.ownership_errors(points, cavity[:-1], 0)
    with pytest.raises(ValueError):
        t.centers(points[:-1])
    with pytest.raises(ValueError):
        StrokeOwnership(5, 4)


def test_owner_translation_preserves_pairs_caps_and_join_offsets_but_reports_shear():
    t, before, cavity = fixture()
    painted = Curve(((0.0, 0.0), (2.0, 1.0), (4.0, 0.0)))
    ink = Curve(((-1.0, 2.0), (2.0, 2.0), (5.0, 2.0)))
    ds = owned_stroke_displacement(t, painted, ink, 0.1)
    after = [tuple(a + b for a, b in zip(p, d)) for p, d in zip(before, ds)]
    cs = owned_cavity_displacement(5, painted, ink, 0.1)
    fill = [tuple(a + b for a, b in zip(p, d)) for p, d in zip(cavity, cs)]
    assert max(t.ownership_errors(after, fill, 0).values()) < 1e-12
    for i in range(len(t.path_columns)):
        assert ds[2 * i] == ds[2 * i + 1]
    for i, owner in enumerate(t.vertex_owners):
        assert ds[i] == ds[2 * owner]
    report = stroke_transport_diagnostics(t, before, after)
    assert report["max_owner_offset_change"] < 1e-12
    assert report["max_pair_length_change"] < 1e-12
    assert report["max_tangent_rotation_degrees"] > 1
    assert report["max_abs_normal_width_change"] > 0.001
    assert any(
        abs(r["tangential_shear_after"] - r["tangential_shear_before"]) > 0.001
        for r in report["path_samples"]
    )


def test_full_open_field_zero_and_invalid_opening_rejected():
    t, _, _ = fixture()
    c = Curve(((0.0, 0.0), (1.0, 1.0)))
    i = Curve(((0.0, 2.0), (1.0, 3.0)))
    assert all(d == (0, 0) for d in owned_stroke_displacement(t, c, i, 1))
    assert all(d == (0, 0) for d in owned_cavity_displacement(5, c, i, 1))
    with pytest.raises(ValueError):
        owned_stroke_displacement(t, c, i, float("nan"))
    with pytest.raises(ValueError):
        owned_cavity_displacement(1, c, i, 0)
