"""Chạy Playwright thật, chặn mọi request tới linkedin.com và trả về HTML mẫu."""
from contextlib import contextmanager
from pathlib import Path

import pytest

from linkedin_scraper import scraper as scraper_mod
from linkedin_scraper import storage
from linkedin_scraper.config import Settings
from linkedin_scraper.scraper import ContactScraper

pytest.importorskip("playwright")
FIXTURES = Path(__file__).parent / "fixtures"

PROFILE = (FIXTURES / "profile.html").read_text(encoding="utf-8").replace(
    "Focus on PPE, MRO and indirect materials.",
    "Focus on PPE. Contact: a.nguyen [at] abc-electronics [dot] vn / Zalo 0987 654 321",
)
CONTACT_CLASSIC = (FIXTURES / "contact_info_classic.html").read_text(encoding="utf-8")
CONTACT_EMPTY = (FIXTURES / "contact_info_empty.html").read_text(encoding="utf-8")
NO_DIALOG = "<html><body><main><h1>X</h1><p>loading...</p></main></body></html>"
NOT_FOUND = "<html><body><main><p>This page doesn’t exist</p></main></body></html>"
AUTHWALL = ('<html><body><form class="login__form"><input name="session_key">'
            '<input name="session_password"></form></body></html>')


def _settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.state_path = tmp_path / "auth" / "state.json"
    s.state_path.parent.mkdir(parents=True)
    s.state_path.write_text('{"cookies": [], "origins": []}')
    s.contacts_jsonl = tmp_path / "contacts.jsonl"
    s.html_dir = tmp_path / "html"
    s.min_delay = s.max_delay = 0
    s.headless = True
    return s


def _install(monkeypatch, routes: dict[str, str], calls: list[str]):
    """routes: public_id -> 'ok' | 'empty' | 'nodialog' | '404' | 'authwall'."""

    def handler(route):
        url = route.request.url
        calls.append(url)
        if "/authwall" in url:
            return route.fulfill(status=200, content_type="text/html", body=AUTHWALL)
        if "/in/" not in url:
            return route.abort()
        pid = url.split("/in/")[1].split("/")[0]
        mode = routes.get(pid, "ok")
        if mode == "authwall":  # session hết hạn: LinkedIn trả form đăng nhập
            return route.fulfill(status=200, content_type="text/html", body=AUTHWALL)
        if mode == "429":
            return route.fulfill(status=429, content_type="text/html", body="Too many requests")
        if mode == "boom":  # lỗi mạng
            return route.abort("connectionreset")
        if mode == "404":
            return route.fulfill(status=404, content_type="text/html", body=NOT_FOUND)
        if "/overlay/contact-info/" in url:
            body = {"ok": CONTACT_CLASSIC, "empty": CONTACT_EMPTY}.get(mode, NO_DIALOG)
        else:
            body = PROFILE
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)

    real_open = scraper_mod.open_context

    @contextmanager
    def mocked_open(s, **kw):
        with real_open(s, **kw) as ctx:
            ctx.route("**/*", handler)
            yield ctx

    monkeypatch.setattr(scraper_mod, "open_context", mocked_open)
    monkeypatch.setattr(ContactScraper, "_sleep", lambda *a, **k: None)
    monkeypatch.setattr(ContactScraper, "_scroll_to_bottom", lambda *a, **k: None)


@pytest.fixture
def chromium_ok():
    from playwright.sync_api import sync_playwright

    s = Settings()
    try:
        with sync_playwright() as pw:
            pw.chromium.launch(executable_path=s.chromium_executable or None).close()
    except Exception as exc:
        pytest.skip(f"Chromium không khởi động được: {exc}")


def test_full_contact_flow(tmp_path, monkeypatch, chromium_ok):
    calls: list[str] = []
    _install(monkeypatch, {"has-contact": "ok", "hidden": "empty", "nodialog": "nodialog",
                           "gone": "404"}, calls)
    settings = _settings(tmp_path)
    urls = [f"https://www.linkedin.com/in/{p}/" for p in ("has-contact", "hidden", "nodialog", "gone")]

    results = ContactScraper(settings, save_html=True).run_contacts(urls)
    by_id = {r.linkedin_url.split("/in/")[1].strip("/"): r for r in results}
    assert len(results) == 4

    c = by_id["has-contact"]
    assert c.error == ""
    assert c.full_name == "Nguyễn Văn A"
    assert c.title == "Purchasing Manager" and c.company == "ABC Electronics Vietnam"
    assert c.location == "Bac Ninh, Vietnam"
    # email trong About (viết né bot) + email trong Contact info
    assert c.emails == ["a.nguyen@abc-electronics.vn"]
    assert c.phones == ["+84987654321", "+84912345678", "+842438261234"]
    assert c.phone_types == ["", "Mobile", "Work"]
    assert c.sources == ["about", "contact_info"]
    assert c.connected_on == "March 5, 2021"
    assert (settings.html_dir / "has-contact__contact.html").exists()

    hidden = by_id["hidden"]
    assert hidden.error == "" and hidden.full_name == "Nguyễn Văn A"
    assert "contact_info" not in hidden.sources  # không chia sẻ -> chỉ có dữ liệu từ About

    assert by_id["nodialog"].error == ""  # không mở được hộp liên hệ vẫn giữ tên/công ty
    assert by_id["gone"].error == "Hồ sơ không tồn tại"

    # Lưu & chạy lại: bỏ qua hồ sơ đã xong, thử lại hồ sơ lỗi
    saved = storage.load_contacts(settings.contacts_jsonl)
    assert len(saved) == 4
    calls.clear()
    again = ContactScraper(settings).run_contacts(urls)
    assert [r.linkedin_url for r in again] == ["https://www.linkedin.com/in/gone/"]


def test_stops_immediately_when_session_expired(tmp_path, monkeypatch, chromium_ok):
    calls: list[str] = []
    _install(monkeypatch, {"first": "ok", "expired": "authwall", "never": "ok"}, calls)
    settings = _settings(tmp_path)
    urls = [f"https://www.linkedin.com/in/{p}/" for p in ("first", "expired", "never")]

    results = ContactScraper(settings).run_contacts(urls)
    assert [r.linkedin_url for r in results] == ["https://www.linkedin.com/in/first/"]
    assert not any("/in/never/" in u for u in calls), "không được truy cập tiếp sau khi bị chặn"
    assert storage.done_contact_urls(settings.contacts_jsonl) == {"https://www.linkedin.com/in/first/"}


def test_stops_on_429(tmp_path, monkeypatch, chromium_ok):
    calls: list[str] = []
    _install(monkeypatch, {"a": "429", "b": "ok"}, calls)
    settings = _settings(tmp_path)
    results = ContactScraper(settings).run_contacts(
        ["https://www.linkedin.com/in/a/", "https://www.linkedin.com/in/b/"])
    assert results == []
    assert not any("/in/b/" in u for u in calls)


def test_stops_after_consecutive_errors(tmp_path, monkeypatch, chromium_ok):
    calls: list[str] = []
    _install(monkeypatch, {}, calls)
    seen: list[str] = []

    def broken(self, page, url):  # lỗi phân tích trang (không phải lỗi mạng)
        seen.append(url)
        raise ValueError("layout lạ")

    monkeypatch.setattr(ContactScraper, "scrape_contact", broken)
    settings = _settings(tmp_path)
    urls = [f"https://www.linkedin.com/in/e{i}/" for i in range(5)]
    results = ContactScraper(settings).run_contacts(urls)
    assert len(results) == 3 and all("layout lạ" in r.error for r in results)
    assert len(seen) == 3


def test_network_error_retries_then_recovers(tmp_path, monkeypatch, chromium_ok):
    """Mạng chập chờn: 2 lần đầu bị reset, lần 3 thành công -> vẫn lấy được dữ liệu."""
    import linkedin_scraper.browser as browser_mod

    monkeypatch.setattr(browser_mod.time, "sleep", lambda s: None)
    calls: list[str] = []
    _install(monkeypatch, {"flaky": "ok"}, calls)
    fails = {"left": 2}
    real_handler_install = scraper_mod.open_context

    @contextmanager
    def flaky_open(s, **kw):
        with real_handler_install(s, **kw) as ctx:
            def maybe_reset(route):
                if fails["left"] > 0:
                    fails["left"] -= 1
                    return route.abort("connectionreset")
                return route.fallback()
            ctx.route("**/*", maybe_reset)
            yield ctx

    monkeypatch.setattr(scraper_mod, "open_context", flaky_open)
    results = ContactScraper(_settings(tmp_path)).run_contacts(["https://www.linkedin.com/in/flaky/"])
    assert len(results) == 1 and results[0].error == ""
    assert results[0].full_name == "Nguyễn Văn A"


def test_network_down_stops_run_without_marking_profiles(tmp_path, monkeypatch, chromium_ok):
    import linkedin_scraper.browser as browser_mod

    monkeypatch.setattr(browser_mod.time, "sleep", lambda s: None)
    calls: list[str] = []
    _install(monkeypatch, {f"n{i}": "boom" for i in range(3)}, calls)
    settings = _settings(tmp_path)
    urls = [f"https://www.linkedin.com/in/n{i}/" for i in range(3)]
    assert ContactScraper(settings).run_contacts(urls) == []
    assert not any("/in/n1/" in u for u in calls)  # dừng ngay, không thử hồ sơ tiếp theo
    assert len([u for u in calls if "/in/n0/" in u]) >= 3  # đã thử lại 3 lần
    assert not settings.contacts_jsonl.exists()  # không ghi hồ sơ lỗi -> lần sau chạy lại đủ


def test_respects_limit_and_max_per_run(tmp_path, monkeypatch, chromium_ok):
    calls: list[str] = []
    _install(monkeypatch, {}, calls)
    settings = _settings(tmp_path)
    settings.max_profiles_per_run = 2
    urls = [f"https://www.linkedin.com/in/p{i}/" for i in range(5)]
    assert len(ContactScraper(settings).run_contacts(urls, limit=10)) == 2
    assert len(ContactScraper(settings).run_contacts(urls, limit=1)) == 1


def test_requires_login(tmp_path):
    settings = _settings(tmp_path)
    settings.state_path.unlink()
    with pytest.raises(SystemExit):
        ContactScraper(settings).run_contacts(["https://www.linkedin.com/in/x/"])
