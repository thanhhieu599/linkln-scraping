"""Quản lý phiên trình duyệt Playwright và đăng nhập LinkedIn.

Mặc định dùng MỘT HỒ SƠ CHROME RIÊNG (auth/browser-profile): người dùng đăng nhập tay một lần
trong cửa sổ đó, các lần sau dùng lại. Cách này ổn định hơn nhiều so với chép cookie li_at từ
Chrome đang dùng: cùng một phiên chạy song song ở 2 trình duyệt có "dấu vân tay" khác nhau
khiến LinkedIn huỷ phiên -> lỗi ERR_TOO_MANY_REDIRECTS.
"""
from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
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


class SessionError(NetworkError):
    """Phiên đăng nhập bị LinkedIn huỷ (redirect vòng lặp / bị đá ra trang đăng nhập)."""


class BrowserClosedError(NetworkError):
    """Người dùng đóng cửa sổ trình duyệt khi tool đang chạy."""


NETWORK_HELP = """
Không kết nối được tới LinkedIn ({detail}).
Lỗi này do MẠNG / PHẦN MỀM trên máy, không phải do đăng nhập. Kiểm tra lần lượt:
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

SESSION_HELP = """
Phiên đăng nhập LinkedIn đã bị huỷ ({detail}).
LinkedIn thường làm vậy khi cùng một phiên được dùng ở 2 trình duyệt khác nhau
(ví dụ chép cookie li_at từ Chrome của bạn sang tool) hoặc khi truy cập quá nhanh.
Cách xử lý:
  1. Đăng nhập lại bằng tay trong cửa sổ riêng của tool (chỉ cần làm 1 lần):
       python -m linkedin_scraper login --headful
     -> Cửa sổ Chrome mở ra, đăng nhập LinkedIn như bình thường (kể cả mã xác minh),
        tool tự nhận khi bạn vào được trang Feed.
  2. Nên XOÁ dòng LINKEDIN_LI_AT trong .env (để trống) — không cần cookie nữa.
  3. Nghỉ 15–30 phút trước khi chạy lại nếu vừa chạy nhiều.
"""

CLOSED_HELP = """
Cửa sổ trình duyệt đã bị đóng khi tool đang chạy — đã dừng (dữ liệu đã lấy vẫn được lưu).
Khi chạy với --headful, hãy để cửa sổ mở cho tới khi lệnh chạy xong.
"""


def _is_closed_error(exc: Exception) -> bool:
    msg = str(exc)
    return ("Target page, context or browser has been closed" in msg
            or "Browser has been closed" in msg or "Connection closed" in msg
            or type(exc).__name__ == "TargetClosedError")


def safe_goto(page: Page, url: str, retries: int = 3, wait_until: str = "domcontentloaded",
              backoff: float = 3.0):
    """page.goto có thử lại khi lỗi mạng (net::ERR_*).

    - ERR_TOO_MANY_REDIRECTS = LinkedIn huỷ phiên -> SessionError ngay (thử lại vô ích,
      còn làm tài khoản bị nghi ngờ thêm).
    - Cửa sổ trình duyệt bị đóng -> BrowserClosedError.
    - Lỗi mạng khác: thử lại, hết lượt -> NetworkError.
    """
    for attempt in range(1, retries + 1):
        try:
            return page.goto(url, wait_until=wait_until)
        except PlaywrightError as exc:
            if _is_closed_error(exc):
                raise BrowserClosedError(CLOSED_HELP) from None
            msg = str(exc)
            m = re.search(r"net::(ERR_[A-Z_]+)", msg)
            if m and m.group(1) == "ERR_TOO_MANY_REDIRECTS":
                raise SessionError(SESSION_HELP.format(detail=m.group(1))) from None
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
            try:
                page.wait_for_timeout(1500)
            except PlaywrightError as exc2:
                if _is_closed_error(exc2):
                    raise BrowserClosedError(CLOSED_HELP) from None
                raise
            time.sleep(wait)


def is_blocked_url(url: str) -> bool:
    return any(marker in url for marker in BLOCKED_MARKERS)


def _launch_options(settings: Settings, headless: bool) -> dict:
    opts = dict(headless=headless, args=["--disable-blink-features=AutomationControlled"])
    if settings.chromium_executable:
        opts["executable_path"] = settings.chromium_executable
    elif settings.browser_channel:
        opts["channel"] = settings.browser_channel  # "chrome" / "msedge" có sẵn trên máy
    if settings.proxy_server:
        opts["proxy"] = {"server": settings.proxy_server}
        if settings.proxy_username:
            opts["proxy"].update(username=settings.proxy_username, password=settings.proxy_password)
    return opts


LOGIN_MARKER = ".tool_login_ok"


def has_session(settings: Settings) -> bool:
    """Đã đăng nhập qua lệnh login chưa (với hồ sơ trình duyệt: phải có dấu đánh dấu
    trong chính hồ sơ đó, để phiên cũ kiểu cookie không bị nhầm là hợp lệ)."""
    if settings.profile_dir:
        return (Path(settings.profile_dir) / LOGIN_MARKER).exists()
    return settings.state_path.exists()


@contextmanager
def open_context(settings: Settings, headless: bool | None = None,
                 use_saved_state: bool = True) -> Iterator[BrowserContext]:
    headless = settings.headless if headless is None else headless
    with sync_playwright() as pw:
        launch = _launch_options(settings, headless)
        ctx_opts = dict(viewport={"width": 1366, "height": 900}, locale="en-US")
        browser = None
        if settings.profile_dir:
            # Hồ sơ Chrome riêng, lưu lâu dài: giữ nguyên cookie + dấu vân tay giữa các lần chạy.
            # Không ép user_agent giả để khớp đúng trình duyệt thật.
            Path(settings.profile_dir).mkdir(parents=True, exist_ok=True)
            context = pw.chromium.launch_persistent_context(settings.profile_dir, **launch, **ctx_opts)
        else:
            browser = pw.chromium.launch(**launch)
            ctx_opts["user_agent"] = settings.user_agent
            if use_saved_state and settings.state_path.exists():
                ctx_opts["storage_state"] = str(settings.state_path)
            context = browser.new_context(**ctx_opts)
        context.set_default_timeout(30_000)
        try:
            yield context
        finally:
            for closer in (context.close, browser.close if browser else None):
                if closer is None:
                    continue
                try:
                    closer()
                except Exception:  # trình duyệt đã bị đóng tay -> bỏ qua
                    pass


def is_logged_in(page: Page) -> bool:
    safe_goto(page, "https://www.linkedin.com/feed/")
    page.wait_for_timeout(2500)
    return "/feed" in page.url and not is_blocked_url(page.url)


def save_session(context: BrowserContext, settings: Settings) -> None:
    settings.state_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(settings.state_path))
    if settings.profile_dir:
        (Path(settings.profile_dir) / LOGIN_MARKER).write_text("ok", encoding="utf-8")


def login(settings: Settings, headless: bool | None = None, wait_manual: int = 300) -> bool:
    """Đăng nhập LinkedIn trong hồ sơ trình duyệt của tool và lưu lại phiên.

    Thứ tự: phiên sẵn có trong hồ sơ -> email/mật khẩu trong .env -> cookie li_at (dự phòng)
    -> đăng nhập tay trong cửa sổ (--headful, tối đa 5 phút, hỗ trợ mã xác minh/captcha).
    """
    with open_context(settings, headless=headless, use_saved_state=False) as context:
        page = context.pages[0] if context.pages else context.new_page()

        ok = False
        if settings.profile_dir:
            try:
                ok = is_logged_in(page)
            except SessionError:
                ok = False
            if ok:
                log.info("Hồ sơ trình duyệt của tool đã đăng nhập sẵn")

        if not ok and settings.email and settings.password:
            log.info("Đăng nhập bằng email/mật khẩu")
            safe_goto(page, "https://www.linkedin.com/login")
            page.fill("#username", settings.email)
            page.fill("#password", settings.password)
            page.click("button[type=submit]")
            page.wait_for_timeout(4000)
            ok = "/feed" in page.url

        if not ok and settings.li_at:
            log.info("Đăng nhập bằng cookie li_at")
            context.add_cookies([{
                "name": "li_at", "value": settings.li_at,
                "domain": ".linkedin.com", "path": "/",
                "httpOnly": True, "secure": True, "sameSite": "None",
            }])
            try:
                ok = is_logged_in(page)
            except SessionError:
                log.warning("Cookie li_at không còn dùng được (LinkedIn đã huỷ phiên)")
                context.clear_cookies()
                ok = False

        # Captcha / 2FA / chưa có thông tin đăng nhập: người dùng tự đăng nhập trong cửa sổ
        if not ok and headless is False:
            try:
                safe_goto(page, "https://www.linkedin.com/login")
            except SessionError:
                context.clear_cookies()
                safe_goto(page, "https://www.linkedin.com/login")
            log.warning("Hãy ĐĂNG NHẬP LINKEDIN trong cửa sổ trình duyệt vừa mở "
                        "(tối đa %d phút). Tool tự nhận khi bạn vào được trang Feed.", wait_manual // 60)
            deadline = time.time() + wait_manual
            while time.time() < deadline:
                try:
                    if "/feed" in page.url:
                        ok = True
                        break
                    page.wait_for_timeout(2000)
                except PlaywrightError as exc:
                    if _is_closed_error(exc):
                        raise BrowserClosedError(CLOSED_HELP) from None
                    raise

        if not ok:
            log.error("Đăng nhập thất bại (url hiện tại: %s). Chạy lại với --headful để đăng nhập tay.",
                      page.url)
            return False

        save_session(context, settings)
        log.info("Đăng nhập thành công, đã lưu phiên (%s)",
                 settings.profile_dir or settings.state_path)
        return True
