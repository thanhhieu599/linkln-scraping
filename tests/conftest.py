import os
from pathlib import Path

# Phải đặt TRƯỚC khi import linkedin_scraper: test mặc định không dùng hồ sơ trình duyệt thật
os.environ["BROWSER_PROFILE_DIR"] = ""

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROTECTED = [ROOT / "data", ROOT / "auth"]


def _snapshot() -> dict[str, float]:
    snap: dict[str, float] = {}
    for base in PROTECTED:
        if base.exists():
            snap.update({str(p): p.stat().st_mtime for p in base.rglob("*") if p.is_file()})
    return snap


@pytest.fixture(autouse=True)
def _protect_real_user_dirs():
    """Không test nào được ghi vào data/ hay auth/ thật của người dùng."""
    before = _snapshot()
    yield
    assert _snapshot() == before, "Test đã ghi vào thư mục data/ hoặc auth/ thật!"
