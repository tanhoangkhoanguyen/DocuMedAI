import docx
from io import BytesIO
from pypdf import PdfReader

from logger import get_logger

_LOGGER = get_logger(name = "doc_parsing", level = "INFO")


def extract_text(raw: bytes, mime: str, filename: str = "") -> str:
    """
    Turn raw upload bytes into plain text. Raises ValueError for unsupported
    types or unparseable content so the worker can mark the job failed.
    """
    if mime == "application/pdf":
        return _extract_pdf(raw)
    if mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return _extract_docx(raw)
    if mime == "text/plain":
        return raw.decode("utf-8", errors = "ignore")
    raise ValueError(f"Unsupported mime type '{mime}' for '{filename}'")


def _extract_pdf(raw: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(pages).strip()
    except Exception as e:
        _LOGGER.error(f"PDF parse failed\n\t{str(e)}")
        raise ValueError(f"Could not parse PDF: {e}") from e
    if not text:
        raise ValueError("PDF contained no extractable text (scanned image?)")
    return text


def _extract_docx(raw: bytes) -> str:
    try:
        document = docx.Document(BytesIO(raw))
        text = "\n".join(p.text for p in document.paragraphs).strip()
    except Exception as e:
        _LOGGER.error(f"DOCX parse failed\n\t{str(e)}")
        raise ValueError(f"Could not parse DOCX: {e}") from e
    if not text:
        raise ValueError("DOCX contained no extractable text")
    return text
