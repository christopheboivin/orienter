from datetime import date
from typing import Protocol


class StepSource(Protocol):
    """Anything that can report daily step counts (Garmin today, maybe something else later)."""

    name: str

    def daily_steps(self, start: date, end: date) -> dict[date, int]:
        """Step totals for each day in [start, end]. Missing days may be omitted."""
        ...
