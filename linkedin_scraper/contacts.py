"""Trích xuất thông tin liên hệ: email, số điện thoại, tên + vài thông tin quan trọng.

Nguồn dữ liệu (chỉ những gì tài khoản đăng nhập ĐƯỢC PHÉP xem):
  1. Hộp "Contact info" của hồ sơ (/in/<id>/overlay/contact-info/) — LinkedIn chỉ hiện
     email/SĐT khi người đó chủ động chia sẻ, thường là với kết nối cấp 1.
  2. Email/SĐT người đó tự công khai trong phần About / headline.
  3. File xuất dữ liệu chính thức của LinkedIn (Settings > Data privacy >
     Get a copy of your data > Connections) — không cần scrape, không rủi ro khóa tài khoản.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import phonenumbers
from bs4 import BeautifulSoup, Tag

from .parser import normalize_profile_url, public_id_from_url

# --------------------------------------------------------------------------- model
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.com.vn", "ymail.com", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "icloud.com", "me.com", "aol.com", "proton.me",
    "protonmail.com", "zoho.com", "gmx.com", "mail.com", "yandex.com", "qq.com", "163.com",
    "126.com", "naver.com", "daum.net", "hanmail.net",
}

CONTACT_FIELDS = [
    "full_name", "first_name", "last_name", "email", "email_type", "emails_all",
    "phone", "phone_type", "phones_all", "title", "company", "headline", "location",
    "websites", "twitter", "address", "connected_on", "linkedin_url", "source",
    "scraped_at", "error",
]


@dataclass
class Contact:
    linkedin_url: str = ""
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)       # chuẩn E.164: +84912345678
    phone_types: list[str] = field(default_factory=list)  # Mobile / Work / Home ...
    title: str = ""
    company: str = ""
    headline: str = ""
    location: str = ""
    websites: list[str] = field(default_factory=list)
    twitter: str = ""
    address: str = ""
    connected_on: str = ""
    sources: list[str] = field(default_factory=list)      # contact_info / about / connections_export
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    error: str = ""

    @property
    def has_contact(self) -> bool:
        return bool(self.emails or self.phones)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Contact":
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_row(self) -> dict:
        first, last = self.first_name, self.last_name
        if self.full_name and not (first or last):
            first, last = split_name(self.full_name)
        email = best_email(self.emails)
        return {
            "full_name": self.full_name or " ".join(p for p in (first, last) if p),
            "first_name": first,
            "last_name": last,
            "email": email,
            "email_type": email_type(email) if email else "",
            "emails_all": "; ".join(self.emails),
            "phone": self.phones[0] if self.phones else "",
            "phone_type": self.phone_types[0] if self.phone_types else "",
            "phones_all": "; ".join(self.phones),
            "title": self.title,
            "company": self.company,
            "headline": self.headline,
            "location": self.location,
            "websites": "; ".join(self.websites),
            "twitter": self.twitter,
            "address": self.address,
            "connected_on": self.connected_on,
            "linkedin_url": self.linkedin_url,
            "source": ", ".join(self.sources),
            "scraped_at": self.scraped_at,
            "error": self.error,
        }


# --------------------------------------------------------------------------- email
EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}(?![\w-])"
)
# Người dùng hay viết né bot: "abc [at] company [dot] com", "abc(at)company.com"
_OBFUSCATED_AT = re.compile(r"\s*[\[(\{]\s*(?:at|a còng)\s*[\])\}]\s*|\s+at\s+(?=[\w-]+\s*(?:\.|\[dot\]|\(dot\)))", re.I)
_OBFUSCATED_DOT = re.compile(r"\s*[\[(\{]\s*(?:dot|chấm)\s*[\])\}]\s*", re.I)
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def is_valid_email(email: str) -> bool:
    email = email.strip()
    if not EMAIL_RE.fullmatch(email):
        return False
    local, domain = email.rsplit("@", 1)
    if ".." in email or local.endswith(".") or domain.lower().endswith(_IMAGE_EXT):
        return False
    return True


def normalize_email(email: str) -> str:
    email = email.strip().strip(".,;:()[]<>\"'").replace("mailto:", "")
    email = email.split("?")[0]
    local, _, domain = email.partition("@")
    return f"{local}@{domain.lower()}" if domain else email


def extract_emails(text: str) -> list[str]:
    if not text:
        return []
    deobf = _OBFUSCATED_DOT.sub(".", _OBFUSCATED_AT.sub("@", text))
    out: list[str] = []
    for candidate in EMAIL_RE.findall(deobf):
        email = normalize_email(candidate)
        if is_valid_email(email) and email.lower() not in (e.lower() for e in out):
            out.append(email)
    return out


def email_type(email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower()
    return "personal" if domain in FREE_EMAIL_DOMAINS else "business"


def best_email(emails: list[str]) -> str:
    """Ưu tiên email doanh nghiệp (B2B) hơn gmail/yahoo."""
    business = [e for e in emails if email_type(e) == "business"]
    return (business or emails or [""])[0]


# --------------------------------------------------------------------------- phone
_PHONE_CANDIDATE = re.compile(r"(?<![\w])(?:\+|00)?\d[\d\s().\-]{7,18}\d(?![\w])")
_DATE_LIKE = re.compile(r"^\d{1,4}[./-]\d{1,2}[./-]\d{1,4}$")
_YEAR_RANGE = re.compile(r"^(19|20)\d{2}\s*[-–]\s*(19|20)\d{2}$")


def normalize_phone(raw: str, region: str = "VN") -> str:
    """Trả về số chuẩn E.164 (+84...) hoặc '' nếu không hợp lệ."""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    try:
        num = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(num):
        return ""
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def format_phone_local(e164: str) -> str:
    """+84912345678 -> 0912 345 678 (dễ đọc khi gọi điện)."""
    try:
        num = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return e164
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)


def extract_phones(text: str, region: str = "VN") -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for m in _PHONE_CANDIDATE.finditer(text):
        raw = m.group(0).strip()
        compact = re.sub(r"\s", "", raw)
        if _DATE_LIKE.match(compact) or _YEAR_RANGE.match(raw):
            continue
        digits = re.sub(r"\D", "", raw)
        if len(digits) < 9 or len(digits) > 15:
            continue
        phone = normalize_phone(raw, region)
        if phone and phone not in out:
            out.append(phone)
    return out


# --------------------------------------------------------------------------- name
def split_name(full_name: str) -> tuple[str, str]:
    """Tách tên. Tên Việt "Nguyễn Văn An" -> first='An', last='Nguyễn Văn' (gọi 'anh An')."""
    parts = full_name.split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def clean_name(name: str) -> str:
    """Bỏ danh xưng/hậu tố hay gặp: 'Nguyễn Văn A, MBA', 'John Doe (He/Him)'."""
    name = re.sub(r"\s*\((?:he|she|they)/\w+\)", "", name, flags=re.I)
    name = re.sub(r"\s*[,|].*$", "", name)
    return re.sub(r"\s+", " ", name).strip()


# --------------------------------------------------------------------------- contact-info overlay
def _text(node: Tag) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _contact_root(soup: BeautifulSoup) -> Tag:
    for selector in ("div[role=dialog]", ".artdeco-modal", "section.pv-contact-info", "main"):
        node = soup.select_one(selector)
        if node is not None and node.find(["h3", "h2"]):
            return node
    return soup


def _sections(root: Tag) -> list[tuple[str, Tag]]:
    """Trả về [(tiêu đề, khối)] cho từng mục: Email, Phone, Website, Connected..."""
    out: list[tuple[str, Tag]] = []
    for header in root.find_all("h3"):
        title = _text(header).lower()
        block = header.find_parent("section") or header.parent
        out.append((title, block))
    return out


def _first_value(block: Tag, header_text: str) -> str:
    """Giá trị đầu tiên trong khối (bỏ dòng tiêu đề)."""
    for s in block.stripped_strings:
        s = re.sub(r"\s+", " ", s).strip()
        if s and s.lower() != header_text:
            return s
    return ""


def parse_contact_info_html(html: str, region: str = "VN") -> dict:
    soup = BeautifulSoup(html, "lxml")
    root = _contact_root(soup)
    result = {"emails": [], "phones": [], "phone_types": [], "websites": [],
              "twitter": "", "address": "", "connected_on": ""}

    def add_email(e: str) -> None:
        e = normalize_email(e)
        if is_valid_email(e) and e.lower() not in (x.lower() for x in result["emails"]):
            result["emails"].append(e)

    def add_phone(raw: str, ptype: str = "") -> None:
        p = normalize_phone(raw, region)
        if p and p not in result["phones"]:
            result["phones"].append(p)
            result["phone_types"].append(ptype)

    for a in root.select('a[href^="mailto:"]'):
        add_email(a["href"])
    for a in root.select('a[href^="tel:"]'):
        add_phone(a["href"][4:])

    for title, block in _sections(root):
        if "email" in title:
            for e in extract_emails(_text(block)):
                add_email(e)
        elif "phone" in title or "điện thoại" in title:
            items = block.find_all("li") or [block]
            for li in items:
                text = _text(li)
                # Loại số nằm trong ngoặc có chữ: "(Mobile)"; "(+84)" là mã vùng, giữ lại
                labels = [g.strip() for g in re.findall(r"\(([^)]*[A-Za-zÀ-ỹ][^)]*)\)", text)]
                ptype = labels[-1] if labels else ""
                number_text = re.sub(r"\([^)]*[A-Za-zÀ-ỹ][^)]*\)", " ", text)
                for raw in _PHONE_CANDIDATE.findall(number_text):
                    add_phone(raw, ptype)
        elif "website" in title or "trang web" in title:
            for a in block.find_all("a", href=True):
                href = a["href"]
                if href.startswith("http") and href not in result["websites"]:
                    result["websites"].append(href)
        elif "twitter" in title or title == "x":
            result["twitter"] = _first_value(block, title)
        elif "address" in title or "địa chỉ" in title:
            result["address"] = _first_value(block, title)
        elif "connected" in title or "kết nối" in title:
            result["connected_on"] = _first_value(block, title)

    # Dự phòng khi LinkedIn đổi cấu trúc: quét toàn bộ text của hộp liên hệ
    if not result["emails"]:
        for e in extract_emails(_text(root)):
            add_email(e)
    return result


# --------------------------------------------------------------------------- merge
def merge_contact_info(contact: Contact, info: dict, source: str) -> None:
    before = (len(contact.emails), len(contact.phones))
    for e in info.get("emails", []):
        if e.lower() not in (x.lower() for x in contact.emails):
            contact.emails.append(e)
    types = info.get("phone_types") or [""] * len(info.get("phones", []))
    for p, t in zip(info.get("phones", []), types):
        if p not in contact.phones:
            contact.phones.append(p)
            contact.phone_types.append(t)
    for w in info.get("websites", []):
        if w not in contact.websites:
            contact.websites.append(w)
    for key in ("twitter", "address", "connected_on"):
        if info.get(key) and not getattr(contact, key):
            setattr(contact, key, info[key])
    if (len(contact.emails), len(contact.phones)) != before and source not in contact.sources:
        contact.sources.append(source)


def contact_from_text(text: str, region: str = "VN") -> dict:
    return {"emails": extract_emails(text), "phones": extract_phones(text, region)}


# --------------------------------------------------------------------------- LinkedIn data export
def read_connections_export(path: Path) -> list[Contact]:
    """Đọc Connections.csv từ file xuất dữ liệu chính thức của LinkedIn.

    File có vài dòng 'Notes:' ở đầu trước dòng tiêu đề; cột:
    First Name, Last Name, URL, Email Address, Company, Position, Connected On
    """
    raw = path.read_bytes().decode("utf-8-sig", errors="replace")
    lines = raw.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.lower().startswith("first name")), None
    )
    if start is None:
        raise ValueError("Không tìm thấy dòng tiêu đề 'First Name,...' — có đúng file Connections.csv?")
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    contacts: list[Contact] = []
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        first, last = row.get("first name", ""), row.get("last name", "")
        url = row.get("url", "")
        c = Contact(
            linkedin_url=normalize_profile_url(url) if url else "",
            first_name=first,
            last_name=last,
            full_name=" ".join(p for p in (last, first) if p) if _looks_vietnamese(last) else
            " ".join(p for p in (first, last) if p),
            company=row.get("company", ""),
            title=row.get("position", ""),
            connected_on=row.get("connected on", ""),
        )
        email = row.get("email address", "")
        if email:
            merge_contact_info(c, {"emails": [email] if is_valid_email(normalize_email(email)) else []},
                               "connections_export")
        if c.full_name or c.linkedin_url:
            contacts.append(c)
    return contacts


_VN_SURNAMES = {
    "nguyễn", "trần", "lê", "phạm", "hoàng", "huỳnh", "phan", "vũ", "võ", "đặng", "bùi",
    "đỗ", "hồ", "ngô", "dương", "lý", "đinh", "đào", "trương", "lâm", "mai", "trịnh", "cao",
    "nguyen", "tran", "le", "pham", "hoang", "huynh", "vu", "vo", "dang", "bui", "do", "ho",
    "ngo", "duong", "ly", "dinh", "dao", "truong", "lam", "trinh",
}


def _looks_vietnamese(last_name: str) -> bool:
    return bool(last_name) and last_name.split()[0].lower() in _VN_SURNAMES


def contact_key(c: Contact) -> str:
    pid = public_id_from_url(c.linkedin_url) if c.linkedin_url else ""
    return pid or (c.full_name or f"{c.first_name} {c.last_name}").strip().lower()
