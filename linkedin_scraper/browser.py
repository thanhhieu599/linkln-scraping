"""Quản lý phiên trình duyệt Playwright và đăng nhập LinkedIn."""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .config import Settings

log = logging.getLogger(__name__)

BLOCKED_MARKERS = ("/authwall", "/checkpoint", "/login", "/uas/login", "/challenge")


class BlockedError(RuntimeError):
    """LinkedIn yêu cầu đăng nhập lại / xác minh / giới hạn truy cập."""


def is_blocked_url(url: str) -> bool:
    return any(marker in url for marker in BLOCKED_MARKERS)


@contextmanager
def open_context(settings: Settings, headless: bool | None = None,
                 use_saved_state: bool = True) -> Iterator[BrowserContext]:
    headless = settings.headless if headless is None else headless
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            executable_path=settings.chromium_executable or None,
        )
        kwargs = dict(
            user_agent=settings.user_agent,
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        if use_saved_state and settings.state_path.exists():
            kwargs["storage_state"] = str(settings.state_path)
        context = browser.new_context(**kwargs)
        context.set_default_timeout(30_000)
        try:
            yield context
        finally:
            context.close()
            browser.close()


def is_logged_in(page: Page) -> bool:
    page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
    page.wait_for_timeout(2500)
    return "/feed" in page.url and not is_blocked_url(page.url)


def login(settings: Settings, headless: bool | None = None, wait_manual: int = 180) -> bool:
    """Đăng nhập và lưu session vào auth/state.json để các lần scrape sau dùng lại."""
    with open_context(settings, headless=headless, use_saved_state=False) as context:
        page = context.new_page()

        if settings.li_at:
            log.info("Đăng nhập bằng cookie li_at")
            context.add_cookies([{
                "name": "li_at", "value": settings.li_at,
                "domain": ".linkedin.com", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "None",
            }])
            ok = is_logged_in(page)
        elif settings.email and settings.password:
            log.info("Đăng nhập bằng email/mật khẩu")
            page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
            page.fill("#username", settings.email)
            page.fill("#password", settings.password)
            page.click("button[type=submit]")
            page.wait_for_timeout(4000)
            ok = "/feed" in page.url
        else:
            ok = False
            if headless is False:
                page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")

        # Captcha / 2FA / không có thông tin đăng nhập: cho người dùng tự thao tác (chế độ --headful)
        if not ok and headless is False:
            log.warning("Hãy hoàn tất đăng nhập/xác minh trong cửa sổ trình duyệt (tối đa %ss)...",
                        wait_manual)
            deadline = time.time() + wait_manual
            while time.time() < deadline:
                if "/feed" in page.url:
                    ok = True
                    break
                page.wait_for_timeout(2000)

        if not ok:
            log.error("Đăng nhập thất bại (url hiện tại: %s)", page.url)
            return False

        settings.state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(settings.state_path))
        log.info("Đã lưu session vào %s", settings.state_path)
        return True
