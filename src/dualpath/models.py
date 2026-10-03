from __future__ import annotations

from pydantic import BaseModel, Field

class ColumnInfo(BaseModel):
    name: str
    data_type: str
    description: str = ""

    def render(self) -> str:
        base = f"{self.name} {self.data_type}"
        return f"{base} -- {self.description}" if self.description else base


class TableInfo(BaseModel):
    name: str
    description: str = ""
    columns: list[ColumnInfo] = Field(default_factory=list)
    row_count: int = 0

    def render(self, include_descriptions: bool = True) -> str:
        lines = [f"TABLE {self.name}  ({self.row_count:,} rows)"]
        if include_descriptions and self.description:
            for para in self.description.split("\n"):
                lines.append(f"  # {para.strip()}")
        for col in self.columns:
            lines.append(f"  - {col.render() if include_descriptions else col.name}")
        return "\n".join(lines)

class GuardViolation(BaseModel):
    rule: str
    detail: str


class GuardVerdict(BaseModel):
    allowed: bool
    sql: str = ""
    violations: list[GuardViolation] = Field(default_factory=list)
    limit_injected: bool = False

    @property
    def reason(self) -> str:
        if self.allowed:
            return "allowed"
        return "; ".join(f"{v.rule}: {v.detail}" for v in self.violations)

class Passage(BaseModel):
    text: str
    source: str
    section: str = ""
    score: float = 0.0

    def citation(self) -> str:
        return f"{self.source} > {self.section}" if self.section else self.source

    @classmethod
    def from_document(cls, document: Any, score: float = 0.0) -> Passage:
        return cls(
            text=document.page_content,
            source=document.metadata.get("source", ""),
            section=document.metadata.get("section", ""),
            score=score,
        )
