"""Giao diện dòng lệnh.

    python -m linkedin_scraper login [--headful]
    python -m linkedin_scraper scrape -i data/profiles.txt [--details] [--limit 20]
    python -m linkedin_scraper scrape -u https://www.linkedin.com/in/xxx/
    python -m linkedin_scraper export -o data/profiles.xlsx
    python -m linkedin_scraper parse-html data/html/xxx.html --url https://www.linkedin.com/in/xxx/
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import storage
from .config import Settings


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_login(args, settings: Settings) -> int:
    from .browser import login

    headless = False if args.headful else settings.headless
    return 0 if login(settings, headless=headless) else 1


def cmd_scrape(args, settings: Settings) -> int:
    from .scraper import LinkedInScraper, read_urls

    urls: list[str] = list(args.url or [])
    if args.input:
        urls += read_urls(Path(args.input))
    if not urls:
        print("Cần --input <file> hoặc --url <link hồ sơ>", file=sys.stderr)
        return 2
    if args.headful:
        settings.headless = False

    scraper = LinkedInScraper(settings, fetch_details=args.details, save_html=args.save_html)
    results = scraper.run(urls, limit=args.limit, resume=not args.no_resume)
    ok = sum(1 for p in results if not p.error)
    print(f"Hoàn tất: {ok}/{len(results)} hồ sơ thành công -> {settings.output_jsonl}")
    if args.output:
        out = storage.export(storage.load_profiles(settings.output_jsonl), Path(args.output))
        print(f"Đã xuất: {out}")
    return 0


def cmd_export(args, settings: Settings) -> int:
    profiles = storage.load_profiles(Path(args.source) if args.source else settings.output_jsonl)
    if not args.include_errors:
        profiles = [p for p in profiles if not p.error]
    out = storage.export(profiles, Path(args.output))
    print(f"Đã xuất {len(profiles)} hồ sơ -> {out}")
    return 0


def cmd_parse_html(args, settings: Settings) -> int:
    from .parser import parse_profile_html

    html = Path(args.file).read_text(encoding="utf-8")
    profile = parse_profile_html(html, args.url or "")
    print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="linkedin_scraper", description="LinkedIn profile scraper")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", help="Đăng nhập và lưu session")
    s.add_argument("--headful", action="store_true", help="Mở trình duyệt để tự xử lý captcha/2FA")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("scrape", help="Thu thập hồ sơ")
    s.add_argument("-i", "--input", help="File chứa danh sách URL (mỗi dòng 1 URL)")
    s.add_argument("-u", "--url", action="append", help="URL hồ sơ (dùng nhiều lần được)")
    s.add_argument("--details", action="store_true",
                   help="Lấy thêm trang chi tiết (đầy đủ kinh nghiệm/kỹ năng) – chậm hơn")
    s.add_argument("--limit", type=int, help="Số hồ sơ tối đa lần chạy này")
    s.add_argument("--no-resume", action="store_true", help="Thu thập lại cả hồ sơ đã có")
    s.add_argument("--save-html", action="store_true", help="Lưu HTML gốc vào data/html để debug")
    s.add_argument("--headful", action="store_true", help="Hiện cửa sổ trình duyệt")
    s.add_argument("-o", "--output", help="Xuất luôn ra file .csv/.xlsx/.json sau khi chạy")
    s.set_defaults(func=cmd_scrape)

    s = sub.add_parser("export", help="Xuất dữ liệu đã thu thập")
    s.add_argument("-o", "--output", required=True, help="File đích: .csv / .xlsx / .json")
    s.add_argument("--source", help="File JSONL nguồn (mặc định data/profiles.jsonl)")
    s.add_argument("--include-errors", action="store_true")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("parse-html", help="Phân tích file HTML đã lưu (offline)")
    s.add_argument("file")
    s.add_argument("--url", default="")
    s.set_defaults(func=cmd_parse_html)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    return args.func(args, Settings())


if __name__ == "__main__":
    raise SystemExit(main())
