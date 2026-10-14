from pathlib import Path

from linkedin_scraper.parser import (
    normalize_profile_url,
    parse_details_html,
    parse_profile_html,
    public_id_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://www.linkedin.com/in/nguyen-van-a/"


def _profile():
    return parse_profile_html((FIXTURES / "profile.html").read_text(encoding="utf-8"), URL)


def test_url_helpers():
    assert public_id_from_url("https://www.linkedin.com/in/abc-123/?trk=x") == "abc-123"
    assert normalize_profile_url("linkedin.com/in/abc-123") == "https://www.linkedin.com/in/abc-123/"
    assert normalize_profile_url("https://vn.linkedin.com/in/abc-123/details/") == \
        "https://www.linkedin.com/in/abc-123/"


def test_top_card():
    p = _profile()
    assert p.public_id == "nguyen-van-a"
    assert p.full_name == "Nguyễn Văn A"
    assert p.headline.startswith("Purchasing Manager at ABC Electronics")
    assert p.location == "Bac Ninh, Vietnam"
    assert p.followers == "2,345 followers"
    assert p.connections == "500+ connections"
    assert p.about.startswith("10+ years in procurement")


def test_grouped_and_single_experience():
    p = _profile()
    assert [e.title for e in p.experiences] == ["Purchasing Manager", "Senior Buyer", "Buyer"]

    first = p.experiences[0]
    assert first.company == "ABC Electronics Vietnam"
    assert first.employment_type == "Full-time"
    assert first.date_range == "Jan 2022 - Present"
    assert first.duration == "2 yrs 10 mos"
    assert first.location == "Bac Ninh, Vietnam"
    assert "3M USD" in first.description

    assert p.experiences[1].company == "ABC Electronics Vietnam"

    single = p.experiences[2]
    assert single.company == "XYZ Steel"
    assert single.employment_type == "Full-time"
    assert single.date_range == "2015 - 2018"
    assert single.location == "Hai Phong, Vietnam"
    assert "Sourcing PPE" in single.description
    assert "Skills: Negotiation" in single.description

    assert p.current_title == "Purchasing Manager"
    assert p.current_company == "ABC Electronics Vietnam"


def test_education_skills_languages_certs():
    p = _profile()
    assert len(p.educations) == 1
    edu = p.educations[0]
    assert edu.school == "Foreign Trade University"
    assert edu.degree == "Bachelor's degree, International Business"
    assert edu.date_range == "2010 - 2014"
    assert p.skills == ["Strategic Sourcing", "Negotiation", "SAP MM"]
    assert p.languages == ["English"]
    assert p.certifications == ["CIPS Level 4 — CIPS"]


def test_details_page_skills():
    skills = parse_details_html((FIXTURES / "details_skills.html").read_text(encoding="utf-8"), "skills")
    assert skills == ["Strategic Sourcing", "Negotiation", "SAP MM",
                      "Supplier Development", "Cost Reduction"]


def test_empty_page_does_not_crash():
    p = parse_profile_html("<html><body></body></html>", URL)
    assert p.full_name == "" and p.experiences == [] and p.skills == []
