"""Điều phối quá trình thu thập: mở hồ sơ, cuộn trang, lấy trang chi tiết, lưu kết quả."""
from __future__ import annotations

import logging
import random
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import storage
from .browser import BlockedError, is_blocked_url, open_context
from .config import Settings
from .models import Profile
from .parser import fill_current_position, normalize_profile_url, parse_details_html, parse_profile_html

log = logging.getLogger(__name__)

# kind trên URL /details/<path>/  ->  thuộc tính của Profile
DETAIL_PAGES = {
    "experience": "experiences",
    "education": "educations",
    "skills": "skills",
    "languages": "languages",
    "certifications": "certifications",
}


def read_urls(path: Path) -> list[str]:
    urls: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url = normalize_profile_url(line)
        if url not in urls:
            urls.append(url)
    return urls


class LinkedInScraper:
    def __init__(self, settings: Settings, fetch_details: bool = False, save_html: bool = False):
        self.settings = settings
        self.fetch_details = fetch_details
        self.save_html = save_html

    # ------------------------------------------------------------------ page helpers
    def _sleep(self, page: Page, low: float | None = None, high: float | None = None) -> None:
        low = self.settings.min_delay if low is None else low
        high = self.settings.max_delay if high is None else high
        page.wait_for_timeout(random.uniform(low, high) * 1000)

    def _goto(self, page: Page, url: str) -> None:
        response = page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status == 429:
            raise BlockedError("LinkedIn trả về 429 (quá nhiều request)")
        if is_blocked_url(page.url):
            raise BlockedError(f"Bị chuyển hướng tới {page.url} — session hết hạn hoặc bị xác minh")

    def _scroll_to_bottom(self, page: Page, max_rounds: int = 12) -> None:
        """Cuộn từ từ như người dùng thật để LinkedIn lazy-load các section."""
        last_height = 0
        for _ in range(max_rounds):
            page.mouse.wheel(0, random.randint(600, 1000))
            page.wait_for_timeout(random.randint(600, 1400))
            height = page.evaluate("document.body.scrollHeight")
            at_bottom = page.evaluate("window.innerHeight + window.scrollY >= document.body.scrollHeight - 50")
            if at_bottom and height == last_height:
                break
            last_height = height

    def _click_show_more(self, page: Page, max_clicks: int = 10) -> None:
        """Bấm 'Show more results' trên trang chi tiết (kỹ năng có thể rất dài)."""
        for _ in range(max_clicks):
            button = page.locator(
                "button.scaffold-finite-scroll__load-button, "
                "button:has-text('Show more results'), button:has-text('Hiển thị thêm kết quả')"
            )
            if button.count() == 0 or not button.first.is_visible():
                return
            button.first.click()
            page.wait_for_timeout(random.randint(1200, 2200))

    def _dump_html(self, name: str, html: str) -> None:
        if not self.save_html:
            return
        self.settings.html_dir.mkdir(parents=True, exist_ok=True)
        (self.settings.html_dir / f"{name}.html").write_text(html, encoding="utf-8")

    # ------------------------------------------------------------------ scraping
    def scrape_profile(self, page: Page, url: str) -> Profile:
        url = normalize_profile_url(url)
        self._goto(page, url)
        try:
            page.wait_for_selector("main h1", timeout=20_000)
        except PlaywrightTimeout:
            if "/404" in page.url or page.locator("text=This page doesn’t exist").count():
                return Profile(url=url, error="Hồ sơ không tồn tại")
            raise
        self._scroll_to_bottom(page)
        html = page.content()
        profile = parse_profile_html(html, url)
        self._dump_html(profile.public_id or "profile", html)

        if self.fetch_details:
            for kind, attr in DETAIL_PAGES.items():
                self._sleep(page, 3, 7)
                try:
                    items = self._scrape_details(page, url, kind, profile.public_id)
                except PlaywrightTimeout:
                    log.warning("  - timeout khi tải /details/%s/", kind)
                    continue
                # Chỉ ghi đè khi trang chi tiết có nhiều dữ liệu hơn trang chính
                if items and len(items) >= len(getattr(profile, attr)):
                    setattr(profile, attr, items)
            fill_current_position(profile)
        return profile

    def _scrape_details(self, page: Page, profile_url: str, kind: str, public_id: str):
        self._goto(page, f"{profile_url.rstrip('/')}/details/{kind}/")
        page.wait_for_selector("main", timeout=20_000)
        page.wait_for_timeout(1500)
        self._scroll_to_bottom(page, max_rounds=20)
        self._click_show_more(page)
        html = page.content()
        self._dump_html(f"{public_id}__{kind}", html)
        return parse_details_html(html, kind)

    def run(self, urls: list[str], limit: int | None = None, resume: bool = True) -> list[Profile]:
        out_path = self.settings.output_jsonl
        if resume:
            done = storage.done_urls(out_path)
            skipped = [u for u in urls if u in done]
            urls = [u for u in urls if u not in done]
            if skipped:
                log.info("Bỏ qua %d hồ sơ đã thu thập trước đó", len(skipped))

        cap = min(limit or self.settings.max_profiles_per_run, self.settings.max_profiles_per_run)
        if len(urls) > cap:
            log.warning("Giới hạn %d hồ sơ/lần chạy (còn %d để lần sau)", cap, len(urls) - cap)
            urls = urls[:cap]

        if not self.settings.state_path.exists():
            raise SystemExit("Chưa có session. Chạy trước: python -m linkedin_scraper login")

        results: list[Profile] = []
        with open_context(self.settings) as context:
            page = context.new_page()
            for i, url in enumerate(urls, 1):
                log.info("[%d/%d] %s", i, len(urls), url)
                try:
                    profile = self.scrape_profile(page, url)
                except BlockedError as exc:
                    log.error("DỪNG: %s. Đăng nhập lại rồi chạy tiếp (đã lưu tiến độ).", exc)
                    break
                except Exception as exc:  # lỗi một hồ sơ không làm dừng cả lượt
                    log.exception("Lỗi khi xử lý %s", url)
                    profile = Profile(url=url, error=f"{type(exc).__name__}: {exc}")

                storage.append_profile(out_path, profile)
                results.append(profile)
                if profile.error:
                    log.warning("  -> lỗi: %s", profile.error)
                else:
                    log.info("  -> %s | %s | %d kinh nghiệm, %d kỹ năng",
                             profile.full_name, profile.headline[:60],
                             len(profile.experiences), len(profile.skills))
                if i < len(urls):
                    self._sleep(page)
            # Lưu lại cookie mới nhất để session sống lâu hơn
            context.storage_state(path=str(self.settings.state_path))
        return results
