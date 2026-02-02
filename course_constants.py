import os
from pathlib import Path

from dotenv import load_dotenv
import yaml

# Load environment variables from .env file
load_dotenv()

# Access variables (shared across courses; kept in .env)
COURSE_ID = os.getenv("COURSE_ID")
GS_USERNAME = os.getenv("GS_USERNAME")
GS_PASSWORD = os.getenv("GS_PASSWORD")
GRADE_THRESHOLD = float(os.getenv("GRADE_THRESHOLD", 0))

# Per-course config loaded from credentials.yml: credentials.courses.<course_key>
_CREDENTIALS_PATH = Path(__file__).resolve().parent / "config" / "credentials.yml"
try:
    with open(_CREDENTIALS_PATH, "r") as f:
        _credentials = yaml.safe_load(f)
    COURSES = _credentials.get("credentials", {}).get("courses", {}) or {}
except Exception:
    COURSES = {}


def get_course_config(team_domain: str) -> dict:
    """
    Return the per-course configuration for a given Slack team_domain.

    Reads from config/credentials.yml under credentials.courses.<team_domain>.
    Each course block can contain: slack, edstem, canvas, gradescope_id.
    """
    if not team_domain:
        raise ValueError("Missing team_domain for course configuration lookup.")

    config = COURSES.get(team_domain)
    if config is None:
        raise KeyError(
            f"No course configuration found for team_domain '{team_domain}'. "
            "Add a block under credentials.courses in config/credentials.yml."
        )
    return config


def get_all_course_keys():
    """Return all configured course keys from credentials.courses."""
    return list(COURSES.keys())
