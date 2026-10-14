import os
import sys
from contextlib import contextmanager

import pytest

import linkedin_scraper.browser as browser_mod
from linkedin_scraper import cli
from linkedin_scraper.config import Settings


@pytest.fixture
def chromium_ok():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch(executable_path=Settings().chromium_executable or None).close()
    except Exception as exc:
        pytest.skip(f"Chromium không khởi động được: {exc}")


def _settings(tmp_path) -> Settings:
    s = Settings()
    s.li_at = "fake-cookie"
    s.state_path = tmp_path / "auth" / "state.json"
    s.data_dir = tmp_path
    return s


def _patch_network(monkeypatch, handler):
    real_open = browser_mod.open_context

    @contextmanager
    def mocked(s, **kw):
        with real_open(s, **kw) as ctx:
            ctx.route("**/*", handler)
            yield ctx

    monkeypatch.setattr(browser_mod, "open_context", mocked)
    monkeypatch.setattr(browser_mod.time, "sleep", lambda s: None)


def test_login_connection_reset_shows_vietnamese_help(tmp_path, monkeypatch, capsys, chromium_ok):
    calls = []

    def reset(route):
        calls.append(route.request.url)
        route.abort("connectionreset")

    _patch_network(monkeypatch, reset)
    s = _settings(tmp_path)
    monkeypatch.setattr(cli, "Settings", lambda: s)

    assert cli.main(["login", "--headless"]) == 3
    err = capsys.readouterr().err
    assert "ERR_CONNECTION_RESET" in err
    assert "BROWSER_CHANNEL=chrome" in err and "Không kết nối được tới LinkedIn" in err
    assert len(calls) >= 3  # thử lại 3 lần (Chrome có thể tự tải lại trang lỗi thêm)
    assert not s.state_path.exists()


def test_login_succeeds_after_transient_reset(tmp_path, monkeypatch, chromium_ok):
    left = {"n": 1}

    def handler(route):
        if left["n"]:
            left["n"] -= 1
            return route.abort("connectionreset")
        if "/feed/" in route.request.url:
            return route.fulfill(status=200, content_type="text/html", body="<main>feed</main>")
        route.abort()

    _patch_network(monkeypatch, handler)
    s = _settings(tmp_path)
    assert browser_mod.login(s, headless=True) is True
    assert s.state_path.exists()


def test_launch_options_channel_and_proxy(monkeypatch, tmp_path):
    captured = {}

    class FakeBrowser:
        def new_context(self, **kw):
            raise RuntimeError("stop")

        def close(self):
            pass

    class FakePW:
        class chromium:
            @staticmethod
            def launch(**kw):
                captured.update(kw)
                return FakeBrowser()

    @contextmanager
    def fake_sync():
        yield FakePW()

    monkeypatch.setattr(browser_mod, "sync_playwright", fake_sync)
    s = _settings(tmp_path)
    s.chromium_executable = ""
    s.browser_channel = "msedge"
    s.proxy_server = "http://10.0.0.1:8080"
    s.proxy_username, s.proxy_password = "u", "p"
    with pytest.raises(RuntimeError):
        with browser_mod.open_context(s):
            pass
    assert captured["channel"] == "msedge"
    assert captured["proxy"] == {"server": "http://10.0.0.1:8080", "username": "u", "password": "p"}
    assert "executable_path" not in captured


# --------------------------------------------------------------------------- safe_goto (không cần trình duyệt)
class _FakePage:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def goto(self, url, wait_until=None):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"

    def wait_for_timeout(self, ms):
        pass


def _pw_error(msg):
    from playwright.sync_api import Error
    return Error(msg)


def test_too_many_redirects_is_session_error_without_retry():
    page = _FakePage([_pw_error("Page.goto: net::ERR_TOO_MANY_REDIRECTS at https://x")] * 3)
    with pytest.raises(browser_mod.SessionError) as exc:
        browser_mod.safe_goto(page, "https://www.linkedin.com/search/results/people/")
    assert page.calls == 1  # không thử lại -> không làm tài khoản bị nghi thêm
    assert "login --headful" in str(exc.value) and "LINKEDIN_LI_AT" in str(exc.value)


def test_browser_closed_by_user_is_friendly():
    page = _FakePage([_pw_error("Page.goto: Target page, context or browser has been closed")])
    with pytest.raises(browser_mod.BrowserClosedError):
        browser_mod.safe_goto(page, "https://www.linkedin.com/feed/")


def test_other_network_error_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(browser_mod.time, "sleep", lambda s: None)
    page = _FakePage([_pw_error("net::ERR_CONNECTION_RESET")] * 2)
    assert browser_mod.safe_goto(page, "https://x") == "ok" and page.calls == 3


def test_non_network_error_is_not_swallowed():
    page = _FakePage([_pw_error("Some other playwright error")])
    with pytest.raises(Exception) as exc:
        browser_mod.safe_goto(page, "https://x")
    assert not isinstance(exc.value, browser_mod.BlockedError)


def test_cli_prints_session_help(tmp_path, monkeypatch, capsys):
    def boom(args, settings):
        raise browser_mod.SessionError(browser_mod.SESSION_HELP.format(detail="ERR_TOO_MANY_REDIRECTS"))

    monkeypatch.setattr(cli, "cmd_login", boom)
    parser = cli.build_parser
    monkeypatch.setattr(cli, "build_parser", lambda: _with_func(parser(), boom))
    assert cli.main(["login"]) == 3
    assert "Phiên đăng nhập LinkedIn đã bị huỷ" in capsys.readouterr().err


def _with_func(p, func):
    p.set_defaults(func=func)
    for action in p._subparsers._group_actions:
        for sub in action.choices.values():
            sub.set_defaults(func=func)
    return p


# --------------------------------------------------------------------------- hồ sơ trình duyệt riêng
def test_has_session_requires_profile_marker(tmp_path):
    s = _settings(tmp_path)
    s.profile_dir = str(tmp_path / "profile")
    s.state_path.parent.mkdir(parents=True)
    s.state_path.write_text("{}")  # phiên cũ kiểu cookie
    assert not browser_mod.has_session(s)  # không được coi là đã đăng nhập hồ sơ mới
    s.profile_dir = ""
    assert browser_mod.has_session(s)


needs_display = pytest.mark.skipif(
    sys.platform.startswith("linux") and not os.environ.get("DISPLAY"),
    reason="Cần màn hình để mở cửa sổ trình duyệt (chạy dưới xvfb-run)")


@needs_display
def test_persistent_profile_keeps_login_between_runs(tmp_path, monkeypatch, chromium_ok):
    """Đăng nhập 1 lần trong hồ sơ riêng -> lần chạy sau vẫn còn cookie, không cần li_at."""
    def handler(route):
        url = route.request.url
        if "/feed/" in url:
            cookie = route.request.headers.get("cookie", "")
            if "sess=1" in cookie:
                return route.fulfill(status=200, content_type="text/html", body="<main>feed</main>")
            return route.fulfill(status=200, content_type="text/html",
                                 body="<form class='login__form'><input name='session_key'></form>")
        if "/login" in url:  # giả lập người dùng đăng nhập xong: server đặt cookie + về feed
            return route.fulfill(status=200, content_type="text/html",
                                 headers={"set-cookie": "sess=1; Path=/; Max-Age=86400; Secure"},
                                 body="<script>location.href='https://www.linkedin.com/feed/'</script>")
        route.abort()

    _patch_network(monkeypatch, handler)
    s = _settings(tmp_path)
    s.li_at = ""
    s.profile_dir = str(tmp_path / "profile")

    assert not browser_mod.has_session(s)
    assert browser_mod.login(s, headless=False, wait_manual=20) is True
    assert browser_mod.has_session(s)

    with browser_mod.open_context(s, headless=True) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        assert browser_mod.is_logged_in(page)  # cookie còn nguyên trong hồ sơ
