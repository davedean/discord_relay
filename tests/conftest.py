import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[[Dict[str, Any]], Path]:
    """Helper to write YAML config files for tests."""

    def _writer(data: Dict[str, Any]) -> Path:
        path = tmp_path / "config.yaml"
        with path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    return _writer
