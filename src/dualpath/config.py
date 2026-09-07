from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

SECRET_FIELDS: frozenset[str] = frozenset()

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]

class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    log_level: LogLevel = Field(default="INFO", validation_alias="DP_LOG_LEVEL")

    warehouse_csv_dir: Path = Field(
        default=REPO_ROOT / "data" / "warehouse", validation_alias="DP_WAREHOUSE_CSV_DIR"
    )
    docs_dir: Path = Field(default=REPO_ROOT / "data" / "docs", validation_alias="DP_DOCS_DIR")

    def redacted(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for name, value in self.model_dump().items():
            if name in SECRET_FIELDS and value:
                out[name] = f"{str(value)[:6]}...redacted"
            else:
                out[name] = value
        return out


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
