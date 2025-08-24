# tests/conftest.py
from __future__ import annotations
import os
from pathlib import Path
import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    # /app is the working dir in the container
    return Path(os.environ.get("REPO_ROOT", "/app"))


@pytest.fixture(scope="session")
def data_dir(repo_root: Path) -> Path:
    # Prefer /app/data (container mount), else local ./data
    d = repo_root / "data"
    if not d.exists():
        d = Path("data")
    return d
