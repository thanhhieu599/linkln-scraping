"""Phân tích HTML hồ sơ LinkedIn thành dữ liệu có cấu trúc.

Parser tách biệt khỏi trình duyệt để có thể test offline bằng file HTML mẫu.
LinkedIn thay đổi class CSS thường xuyên, nên parser ưu tiên các mốc ổn định:
  - id của section (#about, #experience, #education, #skills ...)
  - span[aria-hidden="true"] (text hiển thị, LinkedIn nhân đôi text cho screen reader)
  - khối con .pvs-entity__sub-components (mô tả / các vị trí lồng nhau)
và luôn có phương án dự phòng dựa trên text thuần.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .models import Education, Experience, Profile

SUB = "pvs-entity__sub-components"
ITEM_SELECTOR = "li.artdeco-list__item, li.pvs-list__paged-list-item"

_YEAR = r"(?:19|20)\d{2}"
_MONTH = (
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
    r"|th(?:áng)?\s?\d{1,2}"
)
DATE_RE = re.compile(
    rf"\b{_YEAR}\b|\b(?:present|hiện tại|nay)\b|^(?:{_MONTH})\s",
    re.IGNORECASE,
)
SKILLS_LINE_RE = re.compile(r"^(skills|kỹ năng)\s*:", re.IGNORECASE)
NUMBER_LINE_RE = re.compile(r"[\d.,]+\+?\s*(followers|connections|người theo dõi|kết nối)", re.I)


# --------------------------------------------------------------------------- helpers
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _dedupe(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        line = _clean(line)
        if line and (not out or out[-1] != line):
            out.append(line)
    return out


def _is_date_line(text: str) -> bool:
    return bool(DATE_RE.search(text)) and len(text) < 80


def _own_sub_components(li: Tag) -> list[Tag]:
    return [sc for sc in li.select(f"div.{SUB}") if sc.find_parent("li") is li]


def _in_sub_of(span: Tag, li: Tag) -> bool:
    """True nếu span nằm trong khối sub-components (của li hoặc sâu hơn)."""
    for parent in span.parents:
        if parent is li:
            return False
        classes = parent.get("class") or []
        if SUB in classes:
            return True
    return False


def _texts(node: Tag) -> list[str]:
    spans = node.select('span[aria-hidden="true"]')
    if spans:
        return _dedupe([s.get_text(" ", strip=True) for s in spans])
    return _dedupe(list(node.stripped_strings))


def _split_item(li: Tag) -> tuple[list[str], list[Tag], list[str]]:
    """Tách một item thành (dòng tiêu đề, các item con, text của khối con)."""
    spans = li.select('span[aria-hidden="true"]')
    if spans:
        header = _dedupe([s.get_text(" ", strip=True) for s in spans if not _in_sub_of(s, li)])
    else:
        header = _dedupe(list(li.stripped_strings))

    sub_items: list[Tag] = []
    sub_texts: list[str] = []
    for sc in _own_sub_components(li):
        children = [c for c in sc.find_all("li") if c.find_parent("li") is li]
        sub_items.extend(children)
        sub_texts.extend(_texts(sc))
    return header, sub_items, _dedupe(sub_texts)


def top_level_items(container: Tag | None) -> list[Tag]:
    if container is None:
        return []
    items = []
    for li in container.select(ITEM_SELECTOR):
        if any(SUB in (p.get("class") or []) for p in li.parents):
            continue
        items.append(li)
    return items


def find_section(soup: BeautifulSoup, *anchor_ids: str) -> Tag | None:
    for anchor_id in anchor_ids:
        anchor = soup.find(id=anchor_id)
        if anchor is not None:
            section = anchor.find_parent("section")
            if section is not None:
                return section
    return None


def public_id_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "in":
        return parts[1]
    return ""


def normalize_profile_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    pid = public_id_from_url(url)
    return f"https://www.linkedin.com/in/{pid}/" if pid else url


# --------------------------------------------------------------------------- experience
def _experience_from_lines(
    lines: list[str], extra_desc: list[str] | None = None, in_group: bool = False
) -> Experience:
    exp = Experience(title=lines[0] if lines else "")
    seen_date = False
    desc: list[str] = []
    for line in lines[1:]:
        if not seen_date and _is_date_line(line):
            parts = [p.strip() for p in line.split("·")]
            exp.date_range = parts[0]
            exp.duration = parts[1] if len(parts) > 1 else ""
            seen_date = True
        elif not seen_date and in_group and not exp.employment_type:
            # Vị trí con trong nhóm công ty: dòng trước ngày tháng là loại hình (Full-time...)
            exp.employment_type = line.split("·")[0].strip()
        elif not seen_date and not exp.company:
            parts = [p.strip() for p in line.split("·")]
            exp.company = parts[0]
            exp.employment_type = parts[1] if len(parts) > 1 else ""
        elif seen_date and not exp.location and len(line) < 80 and not SKILLS_LINE_RE.match(line):
            exp.location = line.split("·")[0].strip()
        else:
            desc.append(line)
    desc.extend(extra_desc or [])
    exp.description = "\n".join(_dedupe(desc))
    return exp


def parse_experience_items(items: list[Tag]) -> list[Experience]:
    results: list[Experience] = []
    for li in items:
        header, sub_items, sub_texts = _split_item(li)
        if not header:
            continue
        grouped = [s for s in sub_items if any(_is_date_line(t) for t in _texts(s))]
        if grouped:
            # Một công ty – nhiều vị trí: header = [Công ty, "Full-time · 5 năm", địa điểm?]
            company = header[0]
            emp_type = ""
            group_location = ""
            for line in header[1:]:
                parts = [p.strip() for p in line.split("·")]
                if _is_date_line(line) or re.search(r"\d+\s*(yr|năm|mo|tháng)", line, re.I):
                    if len(parts) > 1:
                        emp_type = parts[0]
                elif not group_location:
                    group_location = parts[0]
            for sub in grouped:
                s_header, _, s_sub_texts = _split_item(sub)
                exp = _experience_from_lines(s_header, s_sub_texts, in_group=True)
                exp.company = company
                exp.employment_type = exp.employment_type or emp_type
                exp.location = exp.location or group_location
                results.append(exp)
        else:
            results.append(_experience_from_lines(header, sub_texts))
    return results


# --------------------------------------------------------------------------- education
def parse_education_items(items: list[Tag]) -> list[Education]:
    results: list[Education] = []
    for li in items:
        header, _, sub_texts = _split_item(li)
        if not header:
            continue
        edu = Education(school=header[0])
        desc: list[str] = []
        for line in header[1:]:
            if not edu.date_range and _is_date_line(line):
                edu.date_range = line
            elif not edu.degree and not edu.date_range:
                edu.degree = line
            else:
                desc.append(line)
        edu.description = "\n".join(_dedupe(desc + sub_texts))
        results.append(edu)
    return results


def parse_simple_items(items: list[Tag], with_subtitle: bool = False) -> list[str]:
    """Kỹ năng / ngôn ngữ / chứng chỉ: lấy dòng đầu (và dòng phụ nếu cần)."""
    out: list[str] = []
    for li in items:
        header, _, _ = _split_item(li)
        if not header:
            continue
        value = header[0]
        if with_subtitle and len(header) > 1:
            value = f"{value} — {header[1]}"
        if value not in out:
            out.append(value)
    return out


# --------------------------------------------------------------------------- main page
def parse_top_card(soup: BeautifulSoup, profile: Profile) -> None:
    main = soup.find("main") or soup
    h1 = main.find("h1")
    if h1:
        profile.full_name = _clean(h1.get_text(" "))

    headline = main.select_one("div.text-body-medium.break-words, div.text-body-medium")
    if headline:
        profile.headline = _clean(headline.get_text(" "))

    location = main.select_one("span.text-body-small.inline.t-black--light.break-words")
    if location:
        profile.location = _clean(location.get_text(" "))

    for node in main.select("li.text-body-small, span.t-bold, a[href*='followers'], a[href*='connections']"):
        text = _clean(node.get_text(" "))
        m = NUMBER_LINE_RE.search(text)
        if not m:
            continue
        kind = m.group(1).lower()
        if kind in ("followers", "người theo dõi") and not profile.followers:
            profile.followers = m.group(0)
        elif kind in ("connections", "kết nối") and not profile.connections:
            profile.connections = m.group(0)

    # Fallback: meta og:title "Tên - Headline | LinkedIn" (trang public)
    if not profile.full_name:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og and og.get("content"):
            profile.full_name = _clean(og["content"].split(" - ")[0].split("|")[0])


def parse_about(soup: BeautifulSoup) -> str:
    section = find_section(soup, "about")
    if section is None:
        return ""
    lines = [
        line for line in _texts(section)
        if line.lower() not in ("about", "giới thiệu")
    ]
    return "\n".join(lines)


def parse_profile_html(html: str, url: str) -> Profile:
    soup = BeautifulSoup(html, "lxml")
    profile = Profile(url=normalize_profile_url(url), public_id=public_id_from_url(url))
    parse_top_card(soup, profile)
    profile.about = parse_about(soup)
    profile.experiences = parse_experience_items(top_level_items(find_section(soup, "experience")))
    profile.educations = parse_education_items(top_level_items(find_section(soup, "education")))
    profile.skills = parse_simple_items(top_level_items(find_section(soup, "skills")))
    profile.languages = parse_simple_items(top_level_items(find_section(soup, "languages")))
    profile.certifications = parse_simple_items(
        top_level_items(find_section(soup, "licenses_and_certifications")), with_subtitle=True
    )
    fill_current_position(profile)
    return profile


def fill_current_position(profile: Profile) -> None:
    if profile.experiences:
        first = profile.experiences[0]
        profile.current_title = first.title
        profile.current_company = first.company


# --------------------------------------------------------------------------- detail pages
def parse_details_html(html: str, kind: str):
    """Trang /details/<kind>/ chứa danh sách đầy đủ (trang chính chỉ hiện 3–5 mục)."""
    soup = BeautifulSoup(html, "lxml")
    items = top_level_items(soup.find("main") or soup)
    if kind == "experience":
        return parse_experience_items(items)
    if kind == "education":
        return parse_education_items(items)
    if kind == "certifications":
        return parse_simple_items(items, with_subtitle=True)
    return parse_simple_items(items)
