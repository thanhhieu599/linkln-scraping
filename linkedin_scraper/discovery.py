"""Tự động tìm người dùng LinkedIn tại Việt Nam theo chủ đề bảo hộ lao động.

Không cần nhập sẵn danh sách hồ sơ. Lấy TẤT CẢ kết quả, không chấm điểm, không lọc:
  1. Tìm người (People search) theo bộ từ khoá + bộ lọc quốc gia Việt Nam, qua nhiều trang.
  2. (Tuỳ chọn) Tìm bài viết (Posts search) về bảo hộ lao động và lấy tác giả bài viết.
  3. Lưu data/leads.jsonl (gộp trùng), xuất Excel/CSV và ghi data/profiles.txt
     để lệnh `contacts` dùng tiếp.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .parser import normalize_profile_url, public_id_from_url

VIETNAM_GEO_URN = "104195383"  # geoUrn của "Vietnam" trên LinkedIn

PRESETS: dict[str, dict[str, list[str]]] = {
    "ppe-vn": {
        "people": [
            "bảo hộ lao động", "an toàn lao động", "ATVSLĐ", "PPE", "EHS", "HSE",
            "Safety Officer", "Safety Manager", "EHS Manager", "HSE Manager",
            "Purchasing Manager", "Procurement", "Buyer", "trưởng phòng mua hàng",
            "GA Manager", "Admin Manager", "Warehouse Manager", "Plant Manager",
        ],
        "posts": [
            "bảo hộ lao động", "an toàn lao động", "PPE Vietnam", "EHS Vietnam",
            "găng tay bảo hộ", "giày bảo hộ",
        ],
    },
}

LEAD_FIELDS = ["full_name", "headline", "location", "current", "degree", "linkedin_url",
               "sources", "post_snippets", "found_at"]


@dataclass
class Lead:
    linkedin_url: str
    public_id: str = ""
    full_name: str = ""
    headline: str = ""
    location: str = ""
    current: str = ""            # dòng "Current: ..." trong kết quả tìm kiếm
    degree: str = ""             # 1st / 2nd / 3rd+
    sources: list[str] = field(default_factory=list)       # people:<từ khoá> / posts:<từ khoá>
    post_snippets: list[str] = field(default_factory=list)
    found_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_row(self) -> dict:
        d = self.to_dict()
        return {k: "; ".join(d[k]) if isinstance(d[k], list) else d[k] for k in LEAD_FIELDS}

    @classmethod
    def from_dict(cls, data: dict) -> "Lead":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})


# --------------------------------------------------------------------------- URLs
def people_search_url(keyword: str, page: int = 1, geo_urn: str = VIETNAM_GEO_URN) -> str:
    url = f"https://www.linkedin.com/search/results/people/?keywords={quote(keyword)}&origin=FACETED_SEARCH"
    if geo_urn:
        url += "&geoUrn=" + quote(json.dumps([geo_urn], separators=(",", ":")))
    return url + (f"&page={page}" if page > 1 else "")


def posts_search_url(keyword: str) -> str:
    return (f"https://www.linkedin.com/search/results/content/?keywords={quote(keyword)}"
            "&origin=GLOBAL_SEARCH_HEADER&sortBy=%22date_posted%22")


# --------------------------------------------------------------------------- parsing
_NOISE = re.compile(
    r"^(?:[•·]\s*)?(?:1st|2nd|3rd\+?|connect|message|follow|following|pending|kết nối|nhắn tin|"
    r"theo dõi|view .*profile|xem hồ sơ.*|status is .*|premium.*|open to work|"
    r".*mutual connections?.*|.*kết nối chung.*|[\d.,]+[kK]?\s*followers?|[\d.,]+\s*người theo dõi|"
    r"provides services.*|.*degree connection)\s*$",
    re.IGNORECASE,
)
_DEGREE = re.compile(r"\b(1st|2nd|3rd\+|3rd)(?!\w)", re.IGNORECASE)
_CURRENT = re.compile(r"^(current|hiện tại|past|trước đây)\s*:\s*", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _profile_href(a: Tag) -> str:
    href = urljoin("https://www.linkedin.com", a.get("href", ""))
    if not re.match(r"^/in/[^/]+/?$", urlparse(href).path):
        return ""
    return normalize_profile_url(href)


def _result_container(a: Tag) -> Tag:
    """Khối chứa trọn 1 kết quả: li gần nhất, div có data-chameleon-result-urn,
    hoặc leo lên cho tới trước khối chứa hồ sơ khác."""
    li = a.find_parent("li")
    if li is not None:
        return li
    urn = a.find_parent(attrs={"data-chameleon-result-urn": True})
    if urn is not None:
        return urn
    own, node = _profile_href(a), a
    for parent in a.parents:
        others = {_profile_href(x) for x in parent.find_all("a", href=True)} - {"", own}
        if others or parent.name in ("main", "body", "[document]"):
            return node
        node = parent
    return node


def _lines(node: Tag) -> list[str]:
    """Các dòng chữ hiển thị trong khối (bỏ text trùng dành cho screen reader)."""
    node = BeautifulSoup(str(node), "lxml")
    for hidden in node.select(".visually-hidden, .a11y-text, script, style, button"):
        hidden.decompose()
    raw = []
    for el in node.find_all(string=True):
        text = _clean(el)
        if text:
            raw.append(text)
    out: list[str] = []
    for line in map(_clean, raw):
        if line and line not in out:
            out.append(line)
    return out


def parse_people_search_html(html: str) -> list[Lead]:
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("main") or soup
    leads: dict[str, Lead] = {}
    for a in root.find_all("a", href=True):
        url = _profile_href(a)
        pid = public_id_from_url(url) if url else ""
        if not pid or pid in leads:
            continue
        name_node = a.select_one('span[aria-hidden="true"]') or a
        name = _clean(name_node.get_text(" "))
        if not name or name.lower() in ("linkedin member", "thành viên linkedin"):
            continue  # link ảnh đại diện (không có chữ) -> chờ link tên phía sau
        box = _result_container(a)
        degree = _DEGREE.search(box.get_text(" "))
        rest, current = [], ""
        for line in _lines(box):
            if line == name or line.startswith(name + " ") or _NOISE.match(line):
                continue
            if _CURRENT.match(line):
                if not current and line.lower().startswith(("current", "hiện tại")):
                    current = _CURRENT.sub("", line)
                continue
            rest.append(line)
        leads[pid] = Lead(
            linkedin_url=url, public_id=pid, full_name=name,
            headline=rest[0] if rest else "", location=rest[1] if len(rest) > 1 else "",
            current=current, degree=degree.group(1) if degree else "",
        )
    return list(leads.values())


def parse_posts_search_html(html: str) -> list[Lead]:
    """Tác giả các bài viết (chỉ cá nhân /in/, bỏ trang công ty)."""
    soup = BeautifulSoup(html, "lxml")
    root = soup.find("main") or soup
    leads: dict[str, Lead] = {}
    for post in root.select("div.feed-shared-update-v2, div[data-urn*='activity']"):
        actor = post.select_one(".update-components-actor, .feed-shared-actor") or post
        link = next((a for a in actor.find_all("a", href=True) if _profile_href(a)), None)
        if link is None:
            continue
        url = _profile_href(link)
        pid = public_id_from_url(url)
        title = actor.select_one(".update-components-actor__title, .feed-shared-actor__title")
        name_node = (title or link).select_one('span[aria-hidden="true"]') or title or link
        name = re.sub(r"\s*[•·].*$", "", _clean(name_node.get_text(" ")))
        desc = actor.select_one(".update-components-actor__description, .feed-shared-actor__description")
        headline = _clean((desc.select_one('span[aria-hidden="true"]') or desc).get_text(" ")) if desc else ""
        body = post.select_one(".update-components-text, .feed-shared-text")
        snippet = _clean(body.get_text(" "))[:200] if body else ""
        if not name:
            continue
        lead = leads.setdefault(pid, Lead(linkedin_url=url, public_id=pid, full_name=name,
                                          headline=headline))
        if snippet and snippet not in lead.post_snippets:
            lead.post_snippets.append(snippet)
    return list(leads.values())


def detect_search_limit(html: str) -> bool:
    """Tài khoản miễn phí bị giới hạn lượt tìm người mỗi tháng (commercial use limit)."""
    text = BeautifulSoup(html, "lxml").get_text(" ").lower()
    return any(k in text for k in (
        "commercial use limit", "reached the monthly limit", "monthly limit for profile searches",
        "giới hạn sử dụng cho mục đích thương mại", "đã đạt đến giới hạn",
    ))


def has_no_results(html: str) -> bool:
    text = BeautifulSoup(html, "lxml").get_text(" ").lower()
    return any(k in text for k in ("no results found", "không tìm thấy kết quả"))


# --------------------------------------------------------------------------- storage
def merge_lead(store: dict[str, Lead], lead: Lead, source: str) -> bool:
    """Gộp lead vào store. Trả về True nếu là người mới."""
    existing = store.get(lead.public_id)
    is_new = existing is None
    if is_new:
        store[lead.public_id] = existing = lead
    else:
        for attr in ("full_name", "headline", "location", "current", "degree"):
            if not getattr(existing, attr) and getattr(lead, attr):
                setattr(existing, attr, getattr(lead, attr))
        for s in lead.post_snippets:
            if s not in existing.post_snippets:
                existing.post_snippets.append(s)
    if source not in existing.sources:
        existing.sources.append(source)
    return is_new


def load_leads(path: Path) -> dict[str, Lead]:
    store: dict[str, Lead] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lead = Lead.from_dict(json.loads(line))
                store[lead.public_id] = lead
    return store


def save_leads(path: Path, store: dict[str, Lead]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for lead in store.values():
            f.write(json.dumps(lead.to_dict(), ensure_ascii=False) + "\n")
    tmp.replace(path)


def write_profiles_txt(path: Path, store: dict[str, Lead]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    urls = [l.linkedin_url for l in store.values()]
    path.write_text("# Tự động tạo bởi lệnh discover\n" + "\n".join(urls) + "\n", encoding="utf-8")
    return len(urls)


def export_leads(store: dict[str, Lead], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [l.to_row() for l in store.values()]
    if out.suffix.lower() == ".csv":
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=LEAD_FIELDS)
            w.writeheader()
            w.writerows(rows)
        return out
    if out.suffix.lower() != ".xlsx":
        raise ValueError("Chỉ hỗ trợ .xlsx hoặc .csv")
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(LEAD_FIELDS)
    for r in rows:
        ws.append([r[k] for k in LEAD_FIELDS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    for col, width in zip("ABCDEF", (26, 50, 24, 36, 7, 45)):
        ws.column_dimensions[col].width = width
    wb.save(out)
    return out
