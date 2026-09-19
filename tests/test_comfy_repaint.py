from contextlib import contextmanager
from pathlib import Path

import pytest
import httpx
from PIL import Image, ImageChops, ImageDraw

from live2d_pipeline import comfy


def source_and_mask(tmp_path, size=(193, 131), mode="RGBA"):
    source = Image.new(mode, size, (23, 54, 89, 127) if mode == "RGBA" else (23, 54, 89))
    ImageDraw.Draw(source).rectangle((20, 10, 110, 90), fill=(95, 125, 200, 230) if mode == "RGBA"
                                     else (95, 125, 200))
    mask = Image.new("L", size)
    draw = ImageDraw.Draw(mask)
    draw.rectangle((27, 30, 44, 80), fill=255)
    draw.rectangle((65, 35, 90, 60), fill=255)
    draw.rectangle((71, 40, 81, 52), fill=0)
    source_path, mask_path = tmp_path / "source.png", tmp_path / "mask.png"
    source.save(source_path)
    mask.save(mask_path)
    return source_path, mask_path, source, mask


def test_repaint_crop_aligns_image_mask_and_retains_holes(tmp_path):
    source, mask, _, selected = source_and_mask(tmp_path)
    image_path, mask_path, _, _, geometry = comfy.prepare_inpaint(source, mask, tmp_path / "out.png", 10)
    assert geometry["crop_box"] == [17, 20, 101, 91]
    assert geometry["content_size"] == [336, 284]
    assert geometry["inference_size"] == [336, 288]
    assert geometry["offset"] == [0, 2]
    with Image.open(image_path) as prepared, Image.open(mask_path) as prepared_mask:
        assert prepared.size == prepared_mask.size
        # Compare the full mask, not only its bounding rectangle: two islands and the hole survive.
        recovered = prepared_mask.crop((0, 2, 336, 286)).resize((84, 71), Image.Resampling.NEAREST)
        assert ImageChops.difference(recovered, selected.crop((17, 20, 101, 91))).getbbox() is None
        assert prepared_mask.getpixel((0, 0)) == 0


@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_repaint_composite_preserves_every_outside_pixel_and_alpha(tmp_path, mode):
    image, mask_path, source, mask = source_and_mask(tmp_path, mode=mode)
    _, _, original, selected, geometry = comfy.prepare_inpaint(image, mask_path, tmp_path / "out.png", 25)
    generated = tmp_path / "generated.png"
    Image.new("RGB", geometry["inference_size"], (255, 1, 3)).save(generated)
    comfy.composite_inpaint(generated, original, selected, geometry, tmp_path / "out.png")
    with Image.open(tmp_path / "out.png") as result:
        assert result.size == source.size
        assert result.mode == source.mode
        for y in range(source.height):
            for x in range(source.width):
                if mask.getpixel((x, y)) == 0:
                    assert result.getpixel((x, y)) == source.getpixel((x, y))
        assert result.getpixel((30, 40))[:3] == (255, 1, 3)
        if mode == "RGBA":
            assert ImageChops.difference(result.getchannel("A"), source.getchannel("A")).getbbox() is None


def test_repaint_rejects_invalid_mask_or_misaligned_output(tmp_path):
    image, mask, _, _ = source_and_mask(tmp_path)
    for size, color, expected in [((193, 131), 0, "empty"), ((1, 1), 255, "dimensions")]:
        bad = tmp_path / "bad.png"
        Image.new("L", size, color).save(bad)
        with pytest.raises(ValueError, match=expected):
            comfy.prepare_inpaint(image, bad, tmp_path / "out.png")
    _, _, original, selected, geometry = comfy.prepare_inpaint(image, mask, tmp_path / "out.png")
    generated = tmp_path / "generated.png"
    Image.new("RGB", (64, 64)).save(generated)
    with pytest.raises(RuntimeError, match="misaligned"):
        comfy.composite_inpaint(generated, original, selected, geometry, tmp_path / "out.png")
    assert not (tmp_path / "out.png").exists()


def test_repaint_canvas_is_bounded_without_stretching_large_crop(tmp_path):
    image, mask, _, _ = source_and_mask(tmp_path, size=(1800, 600))
    # A wide selection must be downsampled proportionally; alignment is padding, never distortion.
    Image.new("L", (1800, 600), 255).save(mask)
    _, _, _, _, geometry = comfy.prepare_inpaint(image, mask, tmp_path / "out.png", 0)
    assert geometry["content_size"] == [768, 256]
    assert geometry["inference_size"] == [768, 256]


def test_masked_image_job_sends_noise_mask_and_reference_and_restores_original(tmp_path, monkeypatch):
    source, mask, original, _ = source_and_mask(tmp_path)
    uploads, execution, settings = {}, {}, {}

    class FakeConfig(dict):
        root = Path(__file__).resolve().parents[1]

    config = FakeConfig(comfy={"unet": "local.gguf", "clip": "clip.gguf", "vae": "qwen.vae",
                               "edit_steps": 3, "seed": 42}, limits={})

    class Service:
        def upload(self, path):
            name = Path(path).name
            uploads[name] = Path(path)
            return name

        def execute(self, graph, output, node):
            execution.update(graph=graph, output=output, node=node)
            with Image.open(uploads[graph["4"]["inputs"]["image"]]) as image:
                # Produce a non-uniform fake model image, changing unselected context too.
                edited = Image.new("RGB", image.size, (200, 100, 5))
                ImageDraw.Draw(edited).rectangle((0, 0, 30, 30), fill=(9, 255, 123))
                edited.save(output)

    @contextmanager
    def fake_session(*args, **kwargs):
        settings.update(kwargs)
        yield Service()

    monkeypatch.setattr(comfy, "session", fake_session)
    output = tmp_path / "result.png"
    comfy.image_job(config, "repair folds", "stray lines", output, tmp_path / "log", image=source,
                    reference=source, mask=mask, seed=19, crop_padding=10, timeout=120)
    graph = execution["graph"]
    assert graph["15"]["class_type"] == "LoadImageMask"
    assert graph["15"]["inputs"]["channel"] == "red"
    assert graph["16"]["class_type"] == "SetLatentNoiseMask"
    assert graph["16"]["inputs"] == {"samples": ["7", 0], "mask": ["15", 0]}
    assert graph["12"]["inputs"]["latent_image"] == ["16", 0]
    assert graph["12"]["inputs"]["seed"] == 19
    assert graph["5"]["inputs"]["image2"] == ["10", 0]
    assert "original character reference" in graph["5"]["inputs"]["prompt"]
    assert settings == {"precision": "float32", "timeout": 120}
    with Image.open(output) as result:
        assert result.size == original.size
        assert result.getpixel((100, 50)) == original.getpixel((100, 50))
        assert result.getpixel((30, 40)) != original.getpixel((30, 40))
    receipt = comfy.read_json(output.with_suffix(".inpaint.json"))
    assert receipt["seed"] == 19
    assert receipt["outside_mask_preserved"] is True


def test_masked_job_invalid_inputs_do_not_start_model(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid inputs must be rejected before model startup")

    monkeypatch.setattr(comfy, "session", forbidden)
    for kwargs, error in [({"mask": tmp_path / "mask.png"}, "source image"),
                          ({"seed": -1}, "unsigned"), ({"seed": True}, "unsigned")]:
        with pytest.raises(ValueError, match=error):
            comfy.image_job(None, "", "", tmp_path / "out.png", None, **kwargs)


def test_comfy_timeout_budget_is_shared_across_upload_and_execution(tmp_path, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(comfy.time, "monotonic", lambda: now[0])
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 5
        now[0] = 16
        return httpx.Response(200, json={"name": "source.png"})

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    service = comfy.ComfyClient("http://test", 5, client)
    source = tmp_path / "source.png"
    Image.new("RGB", (8, 8)).save(source)
    assert service.upload(source) == "source.png"
    with pytest.raises(TimeoutError, match="time budget"):
        service.execute({}, tmp_path / "out.png", "9")
    assert len(calls) == 1
