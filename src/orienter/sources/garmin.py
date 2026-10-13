"""Garmin Connect step source.

Uses the unofficial `garminconnect` library (Garmin's official API is partner-only).
Your password is only used once, by `orienter garmin-login`; afterwards OAuth tokens
are reused from the token store. Expect this to break occasionally when Garmin changes
its login flow: upgrade `garminconnect` first.
"""

from datetime import date
from pathlib import Path

from garminconnect import Garmin
from garminconnect.client import token_file_path


class GarminStepSource:
    name = "garmin"

    def __init__(self, tokenstore: Path):
        self.tokenstore = tokenstore
        self._client: Garmin | None = None

    @property
    def is_logged_in(self) -> bool:
        return token_file_path(str(self.tokenstore)).exists()

    def _api(self) -> Garmin:
        if self._client is None:
            if not self.is_logged_in:
                raise RuntimeError("Not logged in to Garmin: run `uv run orienter garmin-login`.")
            client = Garmin()
            client.login(str(self.tokenstore))
            self._client = client
        return self._client

    def daily_steps(self, start: date, end: date) -> dict[date, int]:
        # the library splits ranges into the 28-day chunks Garmin accepts
        rows = self._api().get_daily_steps(start.isoformat(), end.isoformat())
        return {
            date.fromisoformat(row["calendarDate"]): int(row["totalSteps"])
            for row in rows or []
            if row.get("totalSteps") is not None
        }


def interactive_login(tokenstore: Path, email: str, password: str) -> str:
    """Log in once (prompts for an MFA code if enabled) and persist the tokens."""
    client = Garmin(email=email, password=password, prompt_mfa=lambda: input("Garmin MFA code: "))
    client.login(str(tokenstore))  # logs in with the password, then saves the tokens there
    if not token_file_path(str(tokenstore)).exists():
        raise RuntimeError("Login succeeded but the tokens could not be saved.")
    return client.get_full_name() or email
