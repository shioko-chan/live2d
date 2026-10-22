"""General authoring retains ordering, actual object IDs and replay dependencies."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
from types import SimpleNamespace

import jsonschema
from PIL import Image
import pytest

from live2d_pipeline import assets, rig
from live2d_pipeline.repair_rig import history_operations, safe_replay
from live2d_pipeline.schema import Operation, Part, Parts, Plan
from live2d_pipeline.store import Store, write_json


def operation(tool, **arguments):
    return Operation(kind="rig", tool=tool, arguments=arguments)


def test_create_then_keyform_refreshes_objects_and_uses_each_current_history_head():
    class Session(rig.RigSession):
        def __init__(self):
            super().__init__(None)
            self.objects_now = [{"kind": "mesh", "id": "Mesh"}]
            self.writes, self.reads = [], 0

        async def call(self, tool, args=None):
            if tool == "project_get_state":
                return {"historyHeadNodeId": f"head-{len(self.writes)}"}
            if tool == "rig_list_objects":
                self.reads += 1
                return {"objects": list(self.objects_now)}
            if tool == "warp_create":
                self.objects_now.append({"kind": "warp", "id": args["id"]})
            self.writes.append((tool, args))
            return {}

    operations = [
        operation(
            "keyform_set",
            target={"kind": "mesh", "id": "Mesh"},
            coordinate={"ParamAngleX": 0},
            channels={"opacity": 1},
        ),
        operation("parameter_create", parameter_id="ParamSmile", name="Smile", min=0, max=1, default=0),
        operation("warp_create", id="Smile", name="Smile", parent_id="Head", mesh_ids=["Mesh"]),
        operation(
            "keyform_set",
            target={"kind": "warp", "id": "Smile"},
            coordinate={"ParamSmile": 1},
            channels={"opacity": 1},
        ),
        operation(
            "object_edit",
            edits=[{"action": "rename", "kind": "warp", "id": "Smile", "name": "Smile control"}],
        ),
    ]
    session = Session()
    asyncio.run(session.apply(operations))
    assert [tool for tool, _ in session.writes] == [op.tool for op in operations]
    assert [args["expected_history_head_node_id"] for _, args in session.writes] == [
        f"head-{index}" for index in range(len(operations))
    ]
    assert session.reads == 2
    assert all("expected_history_head_node_id" not in op.arguments for op in operations)


def test_authoring_uses_discovered_schema_before_write_and_never_retries_errors():
    class Protocol:
        def __init__(self):
            self.calls = []

        async def call_tool(self, name, args):
            self.calls.append((name, args))
            return SimpleNamespace(isError=True, content=["Rejected dependency"], structuredContent=None)

    transport = Protocol()
    session = rig.RigSession(transport)
    session.tools = {
        "warp_create": {
            "type": "object",
            "properties": {"mesh_ids": {"type": "array", "minItems": 1}},
            "required": ["mesh_ids"],
        }
    }
    with pytest.raises(jsonschema.ValidationError):
        asyncio.run(session.call("warp_create", {"mesh_ids": []}))
    assert transport.calls == []
    with pytest.raises(RuntimeError, match="Rejected dependency"):
        asyncio.run(session.call("warp_create", {"mesh_ids": ["Mesh"]}))
    assert len(transport.calls) == 1


def test_failed_authoring_keeps_parent_workspace_unchanged_and_candidate_recoverable(tmp_path, monkeypatch):
    parent = tmp_path / "parent.psd2live"
    parent.write_bytes(b"original source")
    output = tmp_path / "candidate"

    @asynccontextmanager
    async def open_rig(config, psd, workspace, control, log):
        class Session:
            async def apply(self, operations):
                workspace.write_bytes(b"recoverable partial candidate")
                raise ValueError("Bad parent")

        yield Session(), None

    monkeypatch.setattr(rig, "open_rig", open_rig)
    with pytest.raises(ValueError, match="Bad parent"):
        asyncio.run(rig.build(None, tmp_path / "source.psd", output, [], parent))
    assert parent.read_bytes() == b"original source"
    assert (output / "source.psd2live").read_bytes() == b"recoverable partial candidate"


@pytest.fixture
def replay_setup(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    revision = store.create(Plan(request="test", prompt="test"))
    materials = store.revision_dir(revision.id) / "materials"
    materials.mkdir()
    parts, objects = [], []
    for name in ("left", "right"):
        Image.new("RGBA", (16, 16), "#aa8899").save(materials / f"{name}.png")
        parts.append(
            Part(
                id=name,
                name=name,
                semantic="objects",
                order=len(parts),
                bounds=(0, 0, 16, 16),
                image=f"{name}.png",
            )
        )
        token = str(int.from_bytes(hashlib.sha256(name.encode()).digest()[:4], "big") & 0x7FFFFFFF)
        objects.extend(
            [
                {"kind": "mesh", "id": name, "parentId": name + "Warp", "layerId": "lyid:" + token},
                {"kind": "warp", "id": name + "Warp", "parentId": name + "Root"},
                {"kind": "warp", "id": name + "Root"},
            ]
        )
    assets.save_parts(materials, Parts(width=32, height=32, parts=parts))
    write_json(materials.parent / "rig/rig-context.json", {"objects": objects})
    from live2d_pipeline.repair import copy_materials

    candidate = tmp_path / "candidate"
    copy_materials(materials, candidate)
    return store, revision, candidate


def authoring_group():
    return [
        operation("parameter_create", parameter_id="ParamStrand", name="Strand"),
        operation("warp_create", id="leftWarp", name="Strand", parent_id="leftRoot", mesh_ids=["left"]),
        operation(
            "keyform_set",
            target={"kind": "warp", "id": "leftWarp"},
            coordinate={"ParamStrand": 1},
            channels={"opacity": 0.5},
        ),
        operation(
            "object_edit", edits=[{"action": "rename", "kind": "warp", "id": "leftWarp", "name": "Left"}]
        ),
        operation(
            "physics_put",
            id="StrandPhysics",
            name="Strand",
            input_parameter="ParamAngleX",
            output_parameter="ParamStrand",
        ),
    ]


def test_replay_preserves_complete_parameter_warp_keyform_physics_group_in_order(replay_setup):
    store, revision, candidate = replay_setup
    operations = authoring_group()
    safe, stale = safe_replay(store, revision, candidate, operations)
    assert safe == operations
    assert stale == []


def test_replay_discards_whole_dependency_group_but_keeps_independent_controls(replay_setup):
    store, revision, candidate = replay_setup
    Image.new("RGBA", (16, 16), "#112233").save(candidate / "left.png")
    dependent = authoring_group()
    # This target's pixels are unchanged, but it uses the same authored parameter.
    dependent.append(
        operation(
            "keyform_set",
            target={"kind": "mesh", "id": "right"},
            coordinate={"ParamStrand": 1},
            channels={"opacity": 0.8},
        )
    )
    independent = operation("parameter_update", parameter_id="ParamAngleX", name="Head turn")
    safe, stale = safe_replay(store, revision, candidate, dependent + [independent])
    assert safe == [independent]
    assert len(stale) == len(dependent)
    assert all(row["dependency_group"] == list(range(len(dependent))) for row in stale)


def test_replay_rejects_unknown_structural_targets_and_their_consumers(replay_setup):
    store, revision, candidate = replay_setup
    operations = [
        operation("parameter_create", parameter_id="ParamUnknown", name="Unknown"),
        operation("warp_create", id="Missing", name="Unknown", parent_id="leftRoot", mesh_ids=["absent"]),
        operation(
            "keyform_set",
            target={"kind": "warp", "id": "Missing"},
            coordinate={"ParamUnknown": 1},
            channels={"opacity": 1},
        ),
    ]
    safe, stale = safe_replay(store, revision, candidate, operations)
    assert safe == []
    assert len(stale) == len(operations)


def test_replay_added_layer_cannot_silently_resize_old_parent_frames(replay_setup):
    store, revision, candidate = replay_setup
    manifest = assets.load_parts(candidate)
    Image.new("RGBA", (16, 16), "#778899").save(candidate / "extra.png")
    manifest.parts.append(
        Part(
            id="extra", name="extra", semantic="objects", order=2, bounds=(16, 16, 16, 16), image="extra.png"
        )
    )
    assets.save_parts(candidate, manifest)
    operations = authoring_group()
    safe, stale = safe_replay(store, revision, candidate, operations)
    assert safe == []
    assert len(stale) == len(operations)


def test_three_generation_rebuild_never_resurrects_discarded_geometry(replay_setup):
    from live2d_pipeline.repair import copy_materials

    store, first, changed_materials = replay_setup
    historical = authoring_group()
    retained = operation("parameter_update", parameter_id="ParamAngleX", name="Head turn")
    first.plan.operations = historical + [retained]
    store.save(first)
    Image.new("RGBA", (16, 16), "#112233").save(changed_materials / "left.png")
    surviving, rejected = safe_replay(store, first, changed_materials, history_operations(store, first))
    assert surviving == [retained]
    second = store.create(Plan(request="new art", prompt="test"))
    second_dir = store.revision_dir(second.id)
    copy_materials(changed_materials, second_dir / "materials")
    write_json(second_dir / "rig-replay.json", {"needs_review": rejected})
    # Rebuilding unchanged R2 art cannot make the old R1 coordinates valid again.
    second_history = history_operations(store, second)
    assert second_history == [retained]
    current_materials = store.root / "third-materials"
    copy_materials(second_dir / "materials", current_materials)
    surviving, rejected = safe_replay(store, second, current_materials, second_history)
    assert surviving == [retained]
    assert rejected == []
    third = store.create(Plan(request="rebuild", prompt="test"))
    write_json(store.revision_dir(third.id) / "rig-replay.json", {"needs_review": rejected})
    assert history_operations(store, third) == [retained]


def test_history_allows_explicit_reauthor_after_omitting_identical_old_operation(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    authored = operation(
        "keyform_set",
        target={"kind": "mesh", "id": "Mesh"},
        coordinate={"ParamAngleX": 30},
        channels={"opacity": 0.5},
    )
    first = store.create(Plan(request="first", prompt="test", operations=[authored]))
    second = store.create(Plan(request="reauthor", prompt="test", operations=[authored]))
    write_json(
        store.revision_dir(second.id) / "rig-replay.json",
        {
            "needs_review": [{"operation": authored.model_dump(), "reason": "old coordinates"}],
        },
    )
    assert history_operations(store, first) == [authored]
    assert history_operations(store, second) == [authored]
    third = store.create(Plan(request="later", prompt="test"))
    assert history_operations(store, third) == [authored]


def test_history_respects_old_receipts_that_filtered_current_plan_too(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    new = operation("parameter_create", parameter_id="ParamLost", name="Discarded own operation")
    first = store.create(Plan(request="legacy rebuild", prompt="test", operations=[new]))
    write_json(
        store.revision_dir(first.id) / "rig-replay.json",
        {
            "needs_review": [{"operation": new.model_dump(), "reason": "legacy target missing"}],
        },
    )
    second = store.create(Plan(request="next rebuild", prompt="test"))
    assert history_operations(store, second) == []


def test_history_replay_preserves_occurrence_count_and_stops_at_new_source(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    transform = operation(
        "rig_transform",
        target={"kind": "mesh", "id": "Mesh"},
        coordinate={"ParamAngleX": 30},
        operations=[{"type": "translate", "delta": [0.1, 0]}],
    )
    store.create(Plan(request="twice", prompt="test", operations=[transform, transform]))
    second = store.create(Plan(request="drop one", prompt="test"))
    write_json(
        store.revision_dir(second.id) / "rig-replay.json",
        {
            "needs_review": [{"operation": transform.model_dump(), "reason": "one occurrence"}],
        },
    )
    assert history_operations(store, second) == [transform]
    third = store.create(Plan(request="new input", prompt="test"))
    third.source = "source.psd"
    store.save(third)
    assert history_operations(store, third) == []


def test_history_rejects_malformed_replay_receipt_instead_of_reviving_operations(tmp_path):
    store = Store(tmp_path / "project")
    store.init()
    revision = store.create(Plan(request="test", prompt="test", operations=authoring_group()))
    write_json(store.revision_dir(revision.id) / "rig-replay.json", {"needs_review": {}})
    with pytest.raises(ValueError, match="Invalid rig replay"):
        history_operations(store, revision)
