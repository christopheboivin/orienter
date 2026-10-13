"""Daily steps from a Garmin Connect CSV export (Reports > Steps > Export).

The export looks like this (header localized, e.g. "Actual,Goal" or "Réel,Objectif"):

    ,Réel,Objectif
    09/16/2026,15663,12740

Only the 4-week view has one row per day; longer views are weekly/monthly averages and
are rejected. The date order (MM/DD vs DD/MM) depends on the account's locale, so we pick
the one under which the rows form consecutive days.
"""

import csv
import io
import re
from datetime import date, datetime, timedelta

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"]


class CsvImportError(ValueError):
    pass


def _parse_dates(raw: list[str]) -> list[date]:
    candidates: list[tuple[int, list[date]]] = []
    for fmt in DATE_FORMATS:
        try:
            days = [datetime.strptime(r, fmt).date() for r in raw]
        except ValueError:
            continue
        gaps = sum(1 for a, b in zip(days, days[1:], strict=False) if b - a != timedelta(days=1))
        candidates.append((gaps, days))
    if not candidates:
        raise CsvImportError(f"Unrecognized date format, e.g. {raw[0]!r}.")
    _, best = min(candidates, key=lambda c: c[0])
    if len(set(best)) != len(best):
        raise CsvImportError("Dates repeat: is this a daily (4-week) export?")
    deltas = {b - a for a, b in zip(best, best[1:], strict=False)}
    if deltas and min(deltas) >= timedelta(days=7):
        raise CsvImportError(
            "Rows are weekly or monthly, not daily: export the 4-week view instead."
        )
    return best


def _parse_steps(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value)  # "15,663" / "15 663" / "15663"
    if not digits:
        raise CsvImportError(f"Not a step count: {value!r}.")
    return int(digits)


def parse_steps_csv(text: str) -> dict[date, int]:
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    data = [r for r in rows if r and re.match(r"^\d", r[0].strip())]  # skip header lines
    if not data:
        raise CsvImportError("No daily rows found.")
    if any(len(r) < 2 for r in data):
        raise CsvImportError("Expected a date column followed by a steps column.")
    days = _parse_dates([r[0].strip() for r in data])
    return {d: _parse_steps(r[1]) for d, r in zip(days, data, strict=True)}
