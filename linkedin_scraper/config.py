"""Cấu hình đọc từ biến môi trường / file .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    li_at: str = os.getenv("LINKEDIN_LI_AT", "")
    email: str = os.getenv("LINKEDIN_EMAIL", "")
    password: str = os.getenv("LINKEDIN_PASSWORD", "")
    min_delay: float = float(os.getenv("MIN_DELAY_SECONDS", "8"))
    max_delay: float = float(os.getenv("MAX_DELAY_SECONDS", "20"))
    max_profiles_per_run: int = int(os.getenv("MAX_PROFILES_PER_RUN", "80"))
    headless: bool = _bool(os.getenv("HEADLESS"), True)
    # Tuỳ chọn: đường dẫn Chrome/Chromium có sẵn trên máy (bỏ trống = dùng bản của Playwright)
    chromium_executable: str = os.getenv("CHROMIUM_EXECUTABLE", "")
    # Dùng trình duyệt có sẵn trên máy: "chrome" hoặc "msedge" (khi Chromium bị mạng/antivirus chặn)
    browser_channel: str = os.getenv("BROWSER_CHANNEL", "")
    proxy_server: str = os.getenv("PROXY_SERVER", "")
    proxy_username: str = os.getenv("PROXY_USERNAME", "")
    proxy_password: str = os.getenv("PROXY_PASSWORD", "")
    state_path: Path = ROOT / "auth" / "state.json"
    data_dir: Path = ROOT / "data"
    output_jsonl: Path = ROOT / "data" / "profiles.jsonl"
    contacts_jsonl: Path = ROOT / "data" / "contacts.jsonl"
    leads_jsonl: Path = ROOT / "data" / "leads.jsonl"
    phone_region: str = os.getenv("PHONE_REGION", "VN")
    html_dir: Path = ROOT / "data" / "html"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    )
