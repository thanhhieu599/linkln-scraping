"""Điều phối quá trình thu thập: mở hồ sơ, cuộn trang, lấy trang chi tiết, lưu kết quả."""
from __future__ import annotations

import logging
import re
import random
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from . import storage
from .browser import BlockedError, has_session, is_blocked_url, open_context, safe_goto
from .config import Settings
from .models import Profile
from .parser import fill_current_position, normalize_profile_url, parse_details_html, parse_profile_html

log = logging.getLogger(__name__)

LOGIN_FORM = "input[name=session_key], input#username, form.login__form, .authwall-join-form"
# Dừng cả lượt khi lỗi liên tiếp — thường là dấu hiệu LinkedIn đang chặn
MAX_CONSECUTIVE_ERRORS = 3

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

    def _goto(self, page: Page, url: str) -> int:
        """Mở trang, trả về HTTP status. Ném BlockedError nếu bị chặn / mất đăng nhập."""
        response = safe_goto(page, url)
        status = response.status if response is not None else 0
        if status in (429, 999):  # 999 = mã LinkedIn dùng khi nghi ngờ bot
            raise BlockedError(f"LinkedIn trả về {status} (quá nhiều request / nghi ngờ bot)")
        if is_blocked_url(page.url):
            raise BlockedError(f"Bị chuyển hướng tới {page.url} — session hết hạn hoặc bị xác minh")
        # Một số trường hợp LinkedIn trả form đăng nhập mà không đổi URL
        if page.locator(LOGIN_FORM).count() and not page.locator("main h1").count():
            raise BlockedError("LinkedIn hiển thị form đăng nhập — session hết hạn")
        return status

    def _is_not_found(self, page: Page, status: int) -> bool:
        return status == 404 or "/404" in page.url or page.locator(
            "text=/This page doesn.t exist|Trang này không tồn tại/"
        ).count() > 0

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
        status = self._goto(page, url)
        if self._is_not_found(page, status):
            return Profile(url=url, error="Hồ sơ không tồn tại")
        page.wait_for_selector("main h1", timeout=20_000)
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

        if not has_session(self.settings):
            raise SystemExit("Chưa đăng nhập. Chạy trước: python -m linkedin_scraper login")

        results: list[Profile] = []
        with open_context(self.settings) as context:
            page = context.new_page()
            errors_in_row = 0
            for i, url in enumerate(urls, 1):
                log.info("[%d/%d] %s", i, len(urls), url)
                try:
                    profile = self.scrape_profile(page, url)
                except BlockedError as exc:
                    log.error("DỪNG: %s\n(Đã lưu tiến độ — xử lý xong thì chạy lại lệnh để tiếp tục.)", exc)
                    break
                except Exception as exc:  # lỗi một hồ sơ không làm dừng cả lượt
                    log.exception("Lỗi khi xử lý %s", url)
                    profile = Profile(url=url, error=f"{type(exc).__name__}: {exc}")

                storage.append_profile(out_path, profile)
                results.append(profile)
                if profile.error and profile.error != "Hồ sơ không tồn tại":
                    errors_in_row += 1
                else:
                    errors_in_row = 0
                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    log.error("DỪNG: %d lỗi liên tiếp — có thể LinkedIn đang chặn. Kiểm tra lại "
                              "bằng --headful trước khi chạy tiếp.", errors_in_row)
                    break
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


class ContactScraper(LinkedInScraper):
    """Chỉ lấy tên + email + SĐT + vài thông tin quan trọng (chức danh, công ty, địa điểm).

    Mỗi hồ sơ = 2 lượt tải trang: trang hồ sơ + hộp Contact info.
    """

    def __init__(self, settings: Settings, save_html: bool = False, region: str = "VN"):
        super().__init__(settings, fetch_details=False, save_html=save_html)
        self.region = region

    def scrape_contact(self, page: Page, url: str) -> "Contact":
        from .contacts import (Contact, clean_name, contact_from_text, merge_contact_info,
                               parse_contact_info_html)

        url = normalize_profile_url(url)
        status = self._goto(page, url)
        if self._is_not_found(page, status):
            return Contact(linkedin_url=url, error="Hồ sơ không tồn tại")
        page.wait_for_selector("main h1", timeout=20_000)
        self._scroll_to_bottom(page, max_rounds=4)  # đủ để tải phần About + Experience
        html = page.content()
        profile = parse_profile_html(html, url)
        self._dump_html(f"{profile.public_id}__profile", html)

        contact = Contact(
            linkedin_url=url,
            full_name=clean_name(profile.full_name),
            title=profile.current_title,
            company=profile.current_company,
            headline=profile.headline,
            location=profile.location,
        )
        # Email/SĐT người dùng tự công khai ở headline / About
        merge_contact_info(contact, contact_from_text(f"{profile.headline}\n{profile.about}",
                                                      self.region), "about")

        self._sleep(page, 2, 5)
        overlay_html = self._open_contact_info(page, url)
        if overlay_html:
            self._dump_html(f"{profile.public_id}__contact", overlay_html)
            merge_contact_info(contact, parse_contact_info_html(overlay_html, self.region),
                               "contact_info")
        return contact

    def _open_contact_info(self, page: Page, url: str) -> str:
        dialog = "div[role=dialog], .artdeco-modal, section.pv-contact-info"
        self._goto(page, f"{url.rstrip('/')}/overlay/contact-info/")
        try:
            page.wait_for_selector(dialog, timeout=12_000)
        except PlaywrightTimeout:
            # Dự phòng: quay lại hồ sơ và bấm link "Contact info"
            link = page.locator("a#top-card-text-details-contact-info, a[href*='overlay/contact-info']")
            if link.count() == 0:
                log.warning("  - không mở được hộp Contact info")
                return ""
            link.first.click()
            try:
                page.wait_for_selector(dialog, timeout=12_000)
            except PlaywrightTimeout:
                log.warning("  - không mở được hộp Contact info")
                return ""
        page.wait_for_timeout(1200)
        return page.content()

    def run_contacts(self, urls: list[str], limit: int | None = None, resume: bool = True) -> list:
        from .contacts import Contact

        out_path = self.settings.contacts_jsonl
        if resume:
            done = storage.done_contact_urls(out_path)
            skipped = [u for u in urls if u in done]
            urls = [u for u in urls if u not in done]
            if skipped:
                log.info("Bỏ qua %d hồ sơ đã lấy liên hệ trước đó", len(skipped))

        cap = min(limit or self.settings.max_profiles_per_run, self.settings.max_profiles_per_run)
        if len(urls) > cap:
            log.warning("Giới hạn %d hồ sơ/lần chạy (còn %d để lần sau)", cap, len(urls) - cap)
            urls = urls[:cap]
        if not urls:
            return []
        if not has_session(self.settings):
            raise SystemExit("Chưa đăng nhập. Chạy trước: python -m linkedin_scraper login")

        results: list[Contact] = []
        with open_context(self.settings) as context:
            page = context.new_page()
            errors_in_row = 0
            for i, url in enumerate(urls, 1):
                log.info("[%d/%d] %s", i, len(urls), url)
                try:
                    contact = self.scrape_contact(page, url)
                except BlockedError as exc:
                    log.error("DỪNG: %s\n(Đã lưu tiến độ — xử lý xong thì chạy lại lệnh để tiếp tục.)", exc)
                    break
                except Exception as exc:
                    log.exception("Lỗi khi xử lý %s", url)
                    contact = Contact(linkedin_url=url, error=f"{type(exc).__name__}: {exc}")

                storage.append_contact(out_path, contact)
                results.append(contact)
                if contact.error and contact.error != "Hồ sơ không tồn tại":
                    errors_in_row += 1
                else:
                    errors_in_row = 0
                if errors_in_row >= MAX_CONSECUTIVE_ERRORS:
                    log.error("DỪNG: %d lỗi liên tiếp — có thể LinkedIn đang chặn. Kiểm tra lại "
                              "bằng --headful trước khi chạy tiếp.", errors_in_row)
                    break
                if contact.error:
                    log.warning("  -> lỗi: %s", contact.error)
                else:
                    log.info("  -> %s | email: %s | SĐT: %s", contact.full_name,
                             ", ".join(contact.emails) or "-", ", ".join(contact.phones) or "-")
                if i < len(urls):
                    self._sleep(page)
            context.storage_state(path=str(self.settings.state_path))
        found = sum(1 for c in results if c.has_contact)
        log.info("Có email/SĐT: %d/%d hồ sơ", found, len(results))
        return results


def _slug(text: str) -> str:
    return re.sub(r"\W+", "_", text).strip("_")


class DiscoveryScraper(LinkedInScraper):
    """Tự tìm người dùng qua thanh tìm kiếm của LinkedIn (người + bài viết), lấy tất cả kết quả."""

    def run_discover(self, people_keywords: list[str], post_keywords: list[str],
                     pages: int = 5, geo_urn: str | None = None, max_new: int | None = None):
        from . import discovery as d

        geo = d.VIETNAM_GEO_URN if geo_urn is None else geo_urn
        store = d.load_leads(self.settings.leads_jsonl)
        start_count = len(store)
        if not has_session(self.settings):
            raise SystemExit("Chưa đăng nhập. Chạy trước: python -m linkedin_scraper login")

        def reached_cap() -> bool:
            return max_new is not None and len(store) - start_count >= max_new

        with open_context(self.settings) as context:
            page = context.new_page()
            try:
                for kw in people_keywords:
                    for p in range(1, pages + 1):
                        if reached_cap():
                            break
                        url = d.people_search_url(kw, p, geo)
                        log.info("[người] '%s' trang %d", kw, p)
                        self._goto(page, url)
                        page.wait_for_timeout(2500)
                        self._scroll_to_bottom(page, max_rounds=4)
                        html = page.content()
                        self._dump_html(f"search_people_{_slug(kw)}_{p}", html)
                        if d.detect_search_limit(html):
                            raise BlockedError("Tài khoản đã hết lượt tìm người miễn phí trong tháng "
                                               "(commercial use limit)")
                        found = d.parse_people_search_html(html)
                        new = sum(d.merge_lead(store, lead, f"people:{kw}") for lead in found)
                        log.info("  -> %d kết quả, %d người mới (tổng %d)", len(found), new, len(store))
                        d.save_leads(self.settings.leads_jsonl, store)
                        if not found or d.has_no_results(html):
                            break  # hết kết quả cho từ khoá này
                        # Tìm kiếm bị LinkedIn theo dõi chặt hơn xem hồ sơ -> nghỉ lâu hơn
                        self._sleep(page, max(self.settings.min_delay, 15),
                                    max(self.settings.max_delay, 35))
                for kw in post_keywords:
                    if reached_cap():
                        break
                    log.info("[bài viết] '%s'", kw)
                    self._goto(page, d.posts_search_url(kw))
                    page.wait_for_timeout(2500)
                    self._scroll_to_bottom(page, max_rounds=8 * pages)
                    html = page.content()
                    self._dump_html(f"search_posts_{_slug(kw)}", html)
                    found = d.parse_posts_search_html(html)
                    new = sum(d.merge_lead(store, lead, f"posts:{kw}") for lead in found)
                    log.info("  -> %d tác giả, %d người mới (tổng %d)", len(found), new, len(store))
                    d.save_leads(self.settings.leads_jsonl, store)
                    self._sleep(page)
            except BlockedError as exc:
                log.error("DỪNG: %s. Dữ liệu đã tìm được vẫn được lưu.", exc)
            context.storage_state(path=str(self.settings.state_path))
        d.save_leads(self.settings.leads_jsonl, store)
        log.info("Tìm được %d người mới, tổng cộng %d", len(store) - start_count, len(store))
        return store
