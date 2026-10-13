import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from eastwalk import __version__

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Config:
    data_dir: Path
    ors_api_key: str | None
    mapillary_token: str | None
    wiki_lang: str
    contact: str | None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "eastwalk.db"

    @property
    def garmin_tokenstore(self) -> Path:
        return self.data_dir / "garmin_tokens"

    @property
    def user_agent(self) -> str:
        contact = self.contact or "personal project"
        return f"eastwalk/{__version__} ({contact})"


def load_config() -> Config:
    data_dir = Path(os.environ.get("EASTWALK_DATA_DIR") or PROJECT_ROOT / "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return Config(
        data_dir=data_dir,
        ors_api_key=os.environ.get("ORS_API_KEY") or None,
        mapillary_token=os.environ.get("MAPILLARY_TOKEN") or None,
        wiki_lang=os.environ.get("WIKI_LANG") or "en",
        contact=os.environ.get("EASTWALK_CONTACT") or None,
    )
