"""Slug generation shared by posts and tags."""

import re
import unicodedata


def slugify(value: str) -> str:
    """Return a lowercase, ASCII, hyphenated slug."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
