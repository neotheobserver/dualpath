from __future__ import annotations

from pathlib import Path

from langchain_core.documents import Document

from ..observability import get_logger

log = get_logger("loaders")

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt", ".pdf"}

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".ipynb_checkpoints"}


class UnsupportedDocument(RuntimeError):
    """The file is not a format this project reads."""


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        log.warning("%s is not valid UTF-8; falling back to latin-1", path.name)
        return path.read_text(encoding="latin-1")


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(f"[page {number}]\n{text}")
    return "\n\n".join(parts)


def load_document(path: str | Path) -> Document:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")

    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown", ".txt"}:
        text = _load_text(path)
    elif suffix == ".pdf":
        text = _load_pdf(path)
    else:
        raise UnsupportedDocument(
            f"{path.name}: {suffix or 'no extension'} is not supported; "
            f"this project reads {', '.join(sorted(SUPPORTED_SUFFIXES))}"
        )

    if not text.strip():
        raise UnsupportedDocument(f"{path.name}: no extractable text")

    return Document(
        page_content=text,
        metadata={"source": path.name, "format": suffix.lstrip(".")},
    )


def load_directory(root: str | Path) -> list[Document]:
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"no such directory: {root}")

    paths = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and not any(part in SKIP_DIRS for part in p.parts)
        and not p.name.startswith("_")
    )

    documents: list[Document] = []
    for path in paths:
        try:
            documents.append(load_document(path))
        except (UnsupportedDocument, OSError) as exc:
            log.warning("skipped %s: %s", path.name, exc)
    return documents

