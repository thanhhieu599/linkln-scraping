"""Quản lý phiên trình duyệt Playwright và đăng nhập LinkedIn."""
from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from typing import Iterator

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from .config import Settings

log = logging.getLogger(__name__)

BLOCKED_MARKERS = ("/authwall", "/checkpoint", "/login", "/uas/login", "/challenge")


class BlockedError(RuntimeError):
    """LinkedIn yêu cầu đăng nhập lại / xác minh / giới hạn truy cập."""


class NetworkError(BlockedError):
    """Không kết nối được tới LinkedIn (mạng, tường lửa, antivirus, VPN/proxy...)."""


NETWORK_HELP = """
Không kết nối được tới LinkedIn ({detail}).
Lỗi này do MẠNG / PHẦN MỀM trên máy, không phải do cookie. Kiểm tra lần lượt:
  1. Mở Chrome trên CÙNG máy, vào https://www.linkedin.com/feed/ — có vào được không?
     - Không vào được  -> lỗi mạng: đổi mạng (WiFi khác / 4G phát từ điện thoại), tắt VPN.
     - Vào được        -> sang bước 2.
  2. Chạy bằng Chrome/Edge có sẵn trên máy thay cho Chromium của Playwright:
       thêm vào file .env:   BROWSER_CHANNEL=chrome      (hoặc msedge)
  3. Antivirus (Kaspersky, ESET, Avast, Bitdefender...) hoặc tường lửa công ty có thể chặn
     trình duyệt tự động: tạm tắt "quét HTTPS/Web Shield" hoặc thêm ngoại lệ cho python.exe.
  4. Mạng công ty/proxy: thêm vào .env   PROXY_SERVER=http://ip:port
  Sau đó chạy lại lệnh. Thêm --headful để xem trình duyệt đang gặp gì.
"""


def safe_goto(page: Page, url: str, retries: int = 3, wait_until: str = "domcontentloaded",
              backoff: float = 3.0):
    """page.goto có thử lại khi lỗi mạng (net::ERR_*). Hết lượt thử -> NetworkError."""
    for attempt in range(1, retries + 1):
        try:
            return page.goto(url, wait_until=wait_until)
        except PlaywrightError as exc:
            msg = str(exc)
            m = re.search(r"net::(ERR_[A-Z_]+)", msg)
            if m:
                detail = m.group(1)
            elif "chrome-error://" in msg:  # trang lỗi mạng của Chrome chen ngang
                detail = "ERR_NETWORK (chrome-error)"
            else:
                raise
            if attempt == retries:
                raise NetworkError(NETWORK_HELP.format(detail=detail)) from None
            wait = backoff * 2 ** (attempt - 1)
            log.warning("Lỗi mạng %s khi mở %s — thử lại sau %.0fs (%d/%d)",
                        detail, url, wait, attempt, retries - 1)
            # Sau lỗi mạng, Chrome mở trang chrome-error:// chậm vài trăm ms; nếu thử lại
            # trước lúc đó, lần thử mới sẽ bị trang lỗi chen ngang -> đợi trang lỗi hiện xong.
            # (URL có thể vẫn là chrome-error của lần lỗi trước nên không dựa vào URL được)
            page.wait_for_timeout(1500)
            time.sleep(wait)


def is_blocked_url(url: str) -> bool:
    return any(marker in url for marker in BLOCKED_MARKERS)


@contextmanager
def open_context(settings: Settings, headless: bool | None = None,
                 use_saved_state: bool = True) -> Iterator[BrowserContext]:
    headless = settings.headless if headless is None else headless
    with sync_playwright() as pw:
        launch = dict(headless=headless, args=["--disable-blink-features=AutomationControlled"])
        if settings.chromium_executable:
            launch["executable_path"] = settings.chromium_executable
        elif settings.browser_channel:
            launch["channel"] = settings.browser_channel  # "chrome" / "msedge" có sẵn trên máy
        if settings.proxy_server:
            launch["proxy"] = {"server": settings.proxy_server}
            if settings.proxy_username:
                launch["proxy"].update(username=settings.proxy_username,
                                       password=settings.proxy_password)
        browser = pw.chromium.launch(**launch)
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
    safe_goto(page, "https://www.linkedin.com/feed/")
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
            safe_goto(page, "https://www.linkedin.com/login")
            page.fill("#username", settings.email)
            page.fill("#password", settings.password)
            page.click("button[type=submit]")
            page.wait_for_timeout(4000)
            ok = "/feed" in page.url
        else:
            ok = False
            if headless is False:
                safe_goto(page, "https://www.linkedin.com/login")

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
