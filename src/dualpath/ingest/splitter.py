from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

HEADERS = [("#", "h1"), ("##", "h2")]

_HEADER_KEYS = [key for _, key in HEADERS]

def _heading_trail(metadata: dict) -> str:
    """`{'h1': 'Appeals', 'h2': '2. Deadlines'}` -> `'Appeals > 2. Deadlines'`."""
    return " > ".join(str(metadata[key]) for key in _HEADER_KEYS if metadata.get(key))


def split_documents(
    documents: list[Document],
    *,
    chunk_tokens: int = 400,
    chunk_overlap: int = 60,
) -> list[Document]:

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADERS,
        strip_headers=False,
    )
    token_splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=chunk_tokens,
        chunk_overlap=chunk_overlap,
    )

    out: list[Document] = []
    for doc in documents:
        source = doc.metadata.get("source", "")
        fmt = doc.metadata.get("format", "")

        if fmt in {"md", "markdown"}:
            sections = header_splitter.split_text(doc.page_content)
        else:
            sections = [Document(page_content=doc.page_content, metadata={})]

        pieces: list[Document] = []
        for section in sections:
            trail = _heading_trail(section.metadata)
            for piece in token_splitter.split_text(section.page_content):
                pieces.append(
                    Document(
                        page_content=piece,
                        metadata={"source": source, "format": fmt, "section": trail},
                    )
                )

        for index, piece in enumerate(pieces):
            piece.metadata["chunk_index"] = index
        out.extend(pieces)

    return out

