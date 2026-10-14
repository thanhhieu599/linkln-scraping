from pathlib import Path

import pytest

REAL_DATA = Path(__file__).resolve().parent.parent / "data"


def _snapshot() -> dict[str, float]:
    if not REAL_DATA.exists():
        return {}
    return {str(p): p.stat().st_mtime for p in REAL_DATA.rglob("*") if p.is_file()}


@pytest.fixture(autouse=True)
def _protect_real_data_dir():
    """Không test nào được ghi vào thư mục data/ thật của người dùng."""
    before = _snapshot()
    yield
    assert _snapshot() == before, "Test đã ghi vào thư mục data/ thật!"
