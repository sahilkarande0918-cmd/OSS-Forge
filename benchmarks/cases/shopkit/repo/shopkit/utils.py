"""Small generic helpers."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterable, Iterator, TypeVar

T = TypeVar("T")


def slugify(text: str) -> str:
    """URL slug: lowercase words joined by single hyphens."""
    words = re.sub(r"[^a-z]+", " ", text.lower()).split()
    return "-".join(words)


def chunked(items: Iterable[T], size: int) -> Iterator[list[T]]:
    if size <= 0:
        raise ValueError("size must be positive")
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def parse_date(value: str) -> date:
    """Accept ISO dates (2024-03-01) and US dates (03/01/2024)."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {value!r}")


def mask_email(email: str) -> str:
    user, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{user[:1]}***@{domain}"
