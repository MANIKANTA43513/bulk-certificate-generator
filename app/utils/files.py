"""Helpers for building safe filenames and safe filesystem paths."""

import re
import unicodedata
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a path would escape the storage directory."""


def sanitize_filename(value: str, fallback: str = "certificate", max_length: int = 80) -> str:
    """Turn arbitrary text into a safe, ASCII-only filename stem."""
    ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_text).strip("._")
    return cleaned[:max_length] or fallback


def resolve_within(base_dir: Path, relative_path: str | Path) -> Path:
    """Resolve ``relative_path`` under ``base_dir``; reject anything that escapes it."""
    base = base_dir.resolve()
    candidate = (base / relative_path).resolve()
    if not candidate.is_relative_to(base):
        raise UnsafePathError("Path escapes the storage directory")
    return candidate
