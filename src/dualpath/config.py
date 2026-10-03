from __future__ import annotations

import functools
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

SECRET_FIELDS: frozenset[str] = frozenset()

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
ChromaMode = Literal["local", "server", "cloud", "memory"]
SECRET_FIELDS: frozenset[str] = frozenset({"chroma_api_key"})

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

    warehouse_path: Path = Field(
        default=REPO_ROOT / "storage" / "warehouse.duckdb",
        validation_alias="DP_WAREHOUSE_PATH",
    )

    sql_max_rows: int = Field(default=200, gt=0, le=100_000, validation_alias="DP_SQL_MAX_ROWS")
    sql_allow_multiple: bool = Field(default=False, validation_alias="DP_SQL_ALLOW_MULTIPLE")

    chunk_tokens: int = Field(default=400, gt=50, validation_alias="DP_CHUNK_TOKENS")
    chunk_overlap: int = Field(default=60, ge=0, validation_alias="DP_CHUNK_OVERLAP")

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_fits(cls, value: int, info: ValidationInfo) -> int:
        budget = info.data.get("chunk_tokens", 400)
        if value >= budget:
            raise ValueError(
                f"DP_CHUNK_OVERLAP ({value}) must be smaller than "
                f"DP_CHUNK_TOKENS ({budget}); otherwise the splitter cannot "
                "make forward progress."
            )
        return value
    
    embedding_model: str = Field(
        default="BAAI/bge-small-en-v1.5", validation_alias="DP_EMBEDDING_MODEL"
    )
    embedding_cache_dir: Path | None = Field(
        default=None, validation_alias="DP_EMBEDDING_CACHE_DIR"
    )

    chroma_mode: ChromaMode = Field(default="local", validation_alias="CHROMA_MODE")
    chroma_path: Path = Field(
        default=REPO_ROOT / "storage" / "chroma", validation_alias="CHROMA_PATH"
    )
    chroma_host: str = Field(default="localhost", validation_alias="CHROMA_HOST")
    chroma_port: int = Field(default=8001, validation_alias="CHROMA_PORT")
    chroma_ssl: bool = Field(default=False, validation_alias="CHROMA_SSL")
    chroma_api_key: str = Field(default="", validation_alias="CHROMA_API_KEY")
    chroma_tenant: str = Field(default="", validation_alias="CHROMA_TENANT")
    chroma_database: str = Field(default="", validation_alias="CHROMA_DATABASE")
    collection: str = Field(default="dualpath_docs", validation_alias="DP_COLLECTION")

    top_k_candidates: int = Field(default=20, gt=0, validation_alias="DP_TOP_K_CANDIDATES")
    top_k_context: int = Field(default=5, gt=0, validation_alias="DP_TOP_K_CONTEXT")
    mmr_lambda: float = Field(default=0.6, ge=0.0, le=1.0, validation_alias="DP_MMR_LAMBDA")
    score_floor: float = Field(default=0.60, ge=0.0, le=1.0, validation_alias="DP_SCORE_FLOOR")

    record_manager_path: Path = Field(
        default=REPO_ROOT / "storage" / "records.sqlite",
        validation_alias="DP_RECORD_MANAGER_PATH",
    )

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
