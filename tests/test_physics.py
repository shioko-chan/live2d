import copy
import math

import pytest

from live2d_pipeline.physics import PhysicsSampler, compose, validate_document


def parameters():
    return {k: {"min": low, "max": high, "default": 0}
            for k, low, high in [("head", -30, 30), ("middle", -3, 3), ("tip", -3, 3)]}


def chain():
    return compose([{
        "Id": "hair",
        "Input": [{"Source": {"Target": "Parameter", "Id": "head"},
                   "Type": "Angle", "Weight": 100, "Reflect": True}],
        "Output": [{"Destination": {"Target": "Parameter", "Id": key}, "Type": "Angle",
                    "Weight": 100, "Scale": 1, "Reflect": False, "VertexIndex": i}
                   for i, key in [(1, "middle"), (3, "tip")]],
        "Vertices": [{"Position": {"X": 0, "Y": i * 10}, "Mobility": 0.85,
                      "Delay": 0.9, "Acceleration": 1, "Radius": 10 if i else 0}
                     for i in range(4)],
        "Normalization": {key: {"Minimum": -30, "Default": 0, "Maximum": 30}
                          for key in ("Position", "Angle")},
    }])


def test_multisegment_chain_lags_settles_and_preserves_lengths():
    sampler = PhysicsSampler(parameters(), chain())
    rows = []
    for frame in range(601):
        t = frame / 60
        rows.append(sampler({"head": 20 if 0.1 <= t < 1.0 else 0}, t))
        points = sampler.positions[0]
        assert all(math.hypot(b[0] - a[0], b[1] - a[1]) == pytest.approx(10)
                   for a, b in zip(points, points[1:]))
    assert max(abs(r["middle"]) for r in rows) > 0.01
    assert max(abs(r["tip"]) for r in rows) > 0.001
    assert any(abs(r["middle"] - r["tip"]) > 0.01 for r in rows)
    assert abs(rows[-1]["middle"]) < max(abs(r["middle"]) for r in rows) / 10
    assert sampler.report()["official_solver_parity"] is False
    assert sampler.report()["steps"] == 600


def test_fixed_physics_step_independent_of_linear_input_sample_rate():
    def sample(rate):
        solver = PhysicsSampler(parameters(), chain())
        for f in range(rate + 1):
            result = solver({"head": f / rate * 20}, f / rate)
        return result
    assert sample(24) == pytest.approx(sample(60), abs=1e-10)


def test_reset_reproduces_results_and_rejects_seeking():
    solver = PhysicsSampler(parameters(), chain())
    def run():
        return [solver({"head": 15 * math.sin(f / 10)}, f / 60) for f in range(60)]
    original = run()
    with pytest.raises(ValueError, match="increasing"):
        solver({}, 0)
    solver.reset()
    assert run() == original


@pytest.mark.parametrize("change,match", [
    (lambda d: d["PhysicsSettings"][0]["Output"][0]["Destination"].update(Id="missing"), "Unknown physics"),
    (lambda d: d["PhysicsSettings"][0]["Output"][0].update(VertexIndex=99), "VertexIndex"),
    (lambda d: d["PhysicsSettings"][0]["Vertices"][1].update(Radius=0), "radius"),
    (lambda d: d["Meta"].update(VertexCount=5), "counts"),
    (lambda d: d["PhysicsSettings"][0]["Input"][0].update(Weight=float("nan")), "finite"),
    (lambda d: d["Meta"]["PhysicsDictionary"].append({"Id": "hair", "Name": "duplicate"}), "unique"),
])
def test_invalid_physics_is_rejected(change, match):
    document = chain()
    change(document)
    with pytest.raises(ValueError, match=match):
        validate_document(document, parameters())


def test_reflect_and_weight_apply_to_output_without_altering_input():
    regular = chain()
    reflected = copy.deepcopy(regular)
    reflected["PhysicsSettings"][0]["Output"][0].update(Reflect=True)
    a, b = PhysicsSampler(parameters(), regular), PhysicsSampler(parameters(), reflected)
    for f in range(40):
        values = {"head": 20 if f else 0}
        left, right = a(values, f / 60), b(values, f / 60)
        assert values == {"head": 20 if f else 0}
        assert left["middle"] == pytest.approx(-right["middle"])


def test_known_project_physics_and_reference_document():
    # The test fixture does not depend on downloaded production files.
    document = chain()
    document["Meta"]["TotalVertexCount"] = document["Meta"]["VertexCount"]
    assert validate_document(document, parameters())["Meta"]["TotalVertexCount"] == 4
    document["Meta"]["TotalVertexCount"] += 1
    with pytest.raises(ValueError, match="TotalVertexCount"):
        validate_document(document, parameters())
