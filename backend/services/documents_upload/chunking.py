"""Chunk plain text into overlapping token windows for embedding."""
from typing import List

from langchain_text_splitters import TokenTextSplitter

from services.documents_upload.constants import CHUNK_SIZE, CHUNK_OVERLAP


def chunk_text(text: str) -> List[str]:
    """
    Split text into 512-token chunks with 64-token overlap (matches the offline
    pipeline in data_processing.py). Empty and duplicate chunks are dropped.
    """
    if not text or not text.strip():
        return []

    splitter = TokenTextSplitter(
        chunk_size = CHUNK_SIZE,
        chunk_overlap = CHUNK_OVERLAP,
    )

    seen = set()
    chunks: List[str] = []
    for chunk in splitter.split_text(text):
        c = chunk.strip()
        if not c or c in seen:
            continue
        seen.add(c)
        chunks.append(c)
    return chunks
