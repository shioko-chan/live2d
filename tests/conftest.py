import pytest

from live2d_pipeline.config import Config


@pytest.fixture
def local_config(tmp_path):
    """Explicit opt-in for tests of local backend protocols; no real models start."""
    path = tmp_path / "full-local.toml"
    path.write_text(Config().path.read_text().replace('mode = "agent"', 'mode = "local"'))
    return Config(path)
