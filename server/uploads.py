"""
uploads.py — In-memory file context storage for the current session.

Supported file types:
  .txt .md .csv .py .js .ts .html .css .json .yaml .yml .xml .sh .bat
  .pdf    — extracted via pypdf
  .docx   — extracted via python-docx

Uploaded text is stored in memory and prepended as context to every
chat request while the file is active.  Files are cleared when the
server restarts (they are not persisted to disk).
"""

import io
from pathlib import Path
from typing import Optional

# In-memory store:  {id -> {"filename": str, "text": str, "chars": int}}
_uploads: dict[int, dict] = {}
_next_id: int = 1


def extract_text(filename: str, data: bytes) -> str:
    """
    Extract plain text from an uploaded file.
    Raises ValueError for unsupported types or extraction failures.
    """
    ext = Path(filename).suffix.lower()

    # Plain-text types — read directly
    text_exts = {
        ".txt",".md",".csv",".py",".js",".ts",".jsx",".tsx",
        ".html",".htm",".css",".json",".yaml",".yml",".xml",
        ".sh",".bat",".ps1",".rb",".go",".java",".c",".cpp",
        ".h",".rs",".sql",".toml",".ini",".cfg",".env",
    }
    if ext in text_exts:
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        raise ValueError(f"Cannot decode {filename} as text")

    # PDF
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(p for p in pages if p.strip())
            if not text.strip():
                raise ValueError("PDF appears to be scanned (no extractable text)")
            return text
        except ImportError:
            raise ValueError("pypdf not installed — PDF parsing unavailable")

    # Word (.docx)
    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(io.BytesIO(data))
            return "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            raise ValueError("python-docx not installed — .docx parsing unavailable")

    raise ValueError(
        f"Unsupported file type: {ext}. "
        "Supported: .txt .md .pdf .docx .csv .py .js .ts .json .html and most text/code files."
    )


def add_upload(filename: str, data: bytes) -> dict:
    """Store an upload. Returns {id, filename, chars}."""
    global _next_id
    text = extract_text(filename, data)
    # Truncate very large files to avoid blowing out the context window
    max_chars = 60_000
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[File truncated — showing first {max_chars:,} characters]"

    upload_id = _next_id
    _next_id += 1
    _uploads[upload_id] = {"filename": filename, "text": text, "chars": len(text)}
    return {"id": upload_id, "filename": filename, "chars": len(text)}


def remove_upload(upload_id: int) -> bool:
    return _uploads.pop(upload_id, None) is not None


def list_uploads() -> list[dict]:
    return [
        {"id": uid, "filename": u["filename"], "chars": u["chars"]}
        for uid, u in _uploads.items()
    ]


def build_file_context() -> Optional[str]:
    """
    Build the file context block prepended to the chat prompt.
    Returns None if no files are active.
    """
    if not _uploads:
        return None
    parts = []
    for uid, u in _uploads.items():
        parts.append(f"=== File: {u['filename']} ===\n{u['text']}")
    return (
        "The user has provided the following document(s) for context. "
        "Refer to them when answering questions.\n\n"
        + "\n\n".join(parts)
    )
