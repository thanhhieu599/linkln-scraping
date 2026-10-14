"""Cấu trúc dữ liệu cho một hồ sơ LinkedIn."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class Experience:
    title: str = ""
    company: str = ""
    employment_type: str = ""
    date_range: str = ""
    duration: str = ""
    location: str = ""
    description: str = ""


@dataclass
class Education:
    school: str = ""
    degree: str = ""
    date_range: str = ""
    description: str = ""


@dataclass
class Profile:
    url: str
    public_id: str = ""
    full_name: str = ""
    headline: str = ""
    location: str = ""
    about: str = ""
    followers: str = ""
    connections: str = ""
    current_company: str = ""
    current_title: str = ""
    experiences: list[Experience] = field(default_factory=list)
    educations: list[Education] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    scraped_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_flat_row(self) -> dict:
        """Một dòng phẳng để xuất CSV/Excel (phù hợp import CRM)."""
        return {
            "url": self.url,
            "public_id": self.public_id,
            "full_name": self.full_name,
            "headline": self.headline,
            "current_title": self.current_title,
            "current_company": self.current_company,
            "location": self.location,
            "followers": self.followers,
            "connections": self.connections,
            "about": self.about,
            "experiences": " | ".join(
                " - ".join(p for p in (e.title, e.company, e.date_range) if p)
                for e in self.experiences
            ),
            "educations": " | ".join(
                " - ".join(p for p in (e.school, e.degree, e.date_range) if p)
                for e in self.educations
            ),
            "skills": ", ".join(self.skills),
            "languages": ", ".join(self.languages),
            "certifications": " | ".join(self.certifications),
            "scraped_at": self.scraped_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        data = dict(data)
        data["experiences"] = [Experience(**e) for e in data.get("experiences", [])]
        data["educations"] = [Education(**e) for e in data.get("educations", [])]
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in data.items() if k in known})
