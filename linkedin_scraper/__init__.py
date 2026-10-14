"""Hệ thống thu thập thông tin hồ sơ LinkedIn."""
from .models import Education, Experience, Profile
from .parser import parse_profile_html

__all__ = ["Education", "Experience", "Profile", "parse_profile_html"]
__version__ = "1.0.0"
