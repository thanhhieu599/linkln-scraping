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

    assert cli.main(["login"]) == 3
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
