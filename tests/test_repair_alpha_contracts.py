"""Split compositing preserves antialiased edges across both hidden-paint backends."""

import runpy
import sys
from pathlib import Path
from types import ModuleType
import pytest
from PIL import Image, ImageChops
from live2d_pipeline import assets, comfy, repair
from live2d_pipeline.repair_schema import DraftCreate, DraftUpdate, Mark
from test_repair import setup as setup


def alpha_source(setup):
    config, store, revision = setup
    base = store.revision_dir(revision.id) / "materials"
    source = Image.open(base / "clothes.png").convert("RGBA")
    source.putpixel((8, 10), (50, 70, 90, 128))
    source.putpixel((13, 10), (80, 90, 100, 96))
    source.save(base / "clothes.png")
    assets.assemble(base, base / "composite.png")
    draft = repair.create(config, store, DraftCreate(revision=revision.id, part="clothes"))
    draft.edit.mask.marks = [Mark(kind="paint", points=[(8, 10)], radius=2)]
    draft.edit.cleanup.backend = "comfy"
    return config, store, draft


def fake_masked_comfy(_config, _prompt, _negative, output, _log, *, image, mask, **_kwargs):
    # Use the real crop/composite boundary, replacing only GPU inference with a solid RGB patch.
    _, _, original, selected, geometry = comfy.prepare_inpaint(image, mask, output)
    generated = output.parent / "test-generated.png"
    Image.new("RGB", geometry["inference_size"], (190, 170, 150)).save(generated)
    comfy.composite_inpaint(generated, original, selected, geometry, output)


def configure_paint(monkeypatch, backend):
    if backend == "comfy":
        monkeypatch.setattr(repair.comfy, "image_job", fake_masked_comfy)
        return

    def fake_lama(image, _mask):
        painted = image.copy()
        painted[:] = (190, 170, 150)
        return painted

    module = ModuleType("annotators.lama_inpainter")
    module.apply_inpaint = fake_lama
    monkeypatch.setitem(sys.modules, "annotators", ModuleType("annotators"))
    monkeypatch.setitem(sys.modules, "annotators.lama_inpainter", module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    worker = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/split_component.py"))
    monkeypatch.setattr(repair, "inference", lambda _config, _engine, source, work, _mode: worker["inpaint"](source, work))


@pytest.mark.parametrize("backend", ["comfy", "lama"])
def test_split_does_not_double_semitransparent_extracted_edge(setup, monkeypatch, backend):
    config, store, draft = alpha_source(setup)
    draft.edit.cleanup.backend = backend
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    configure_paint(monkeypatch, backend)
    result = repair.preview_materials(config, store, draft.id, draft.version).result["preview"]
    root = repair.directory(store, draft.id)
    before = Image.open(root / "base/composite.png").convert("RGBA")
    after = Image.open(root / result["path"] / "materials/composite.png").convert("RGBA")
    assert result["ready"]
    assert after.getpixel((12, 17))[3] == 128
    assert all(channel.getbbox() is None for channel in ImageChops.difference(before, after).split())


@pytest.mark.parametrize("backend", ["comfy", "lama"])
def test_expanded_cleanup_preserves_unextracted_semitransparent_edge(setup, monkeypatch, backend):
    config, store, draft = alpha_source(setup)
    draft.edit.cleanup.backend = backend
    draft.edit.cleanup.expansion = 4
    draft.edit.cleanup.allow_visible = True
    draft = repair.update(store, draft.id, DraftUpdate(version=draft.version, edit=draft.edit))
    configure_paint(monkeypatch, backend)
    result = repair.preview_materials(config, store, draft.id, draft.version).result["preview"]
    root = repair.directory(store, draft.id)
    assert repair.mask_image(root, draft).getpixel((13, 10)) == 0
    assert repair.cleanup_mask(root, draft).getpixel((13, 10)) == 255
    underpaint = Image.open(root / result["path"] / "underpaint.png").convert("RGBA")
    assert result["ready"]
    assert underpaint.getpixel((13, 10)) == (190, 170, 150, 96)
    assert underpaint.getpixel((8, 10))[3] == 0
