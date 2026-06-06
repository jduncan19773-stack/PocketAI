"""
uploads.py — In-memory upload storage for the current session.

Two kinds of upload:
  TEXT  — .txt .md .csv .pdf .docx and most code files. Text is extracted
          and prepended to the prompt as context for any model.
  IMAGE — .png .jpg .jpeg .gif .webp .bmp. Stored as a base64 data URI and
          sent to a vision model (Moondream) so it can actually see the image.

Uploads live in memory only and are cleared when the server restarts.
"""

import base64
import io
from pathlib import Path
from typing import Optional

# Text uploads:  {id -> {"kind":"text", "filename": str, "text": str, "chars": int}}
# Image uploads: {id -> {"kind":"image","filename": str, "data_uri": str, "bytes": int}}
_uploads: dict[int, dict] = {}
_next_id: int = 1

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_IMAGE_BYTES = 12 * 1024 * 1024   # 12 MB cap per image

_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


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
    """
    Store an upload. Routes images to image storage (base64) and everything
    else to text extraction. Returns a dict describing the stored upload.
    """
    global _next_id
    ext = Path(filename).suffix.lower()

    # ── Image upload ─────────────────────────────────────────────
    if ext in IMAGE_EXTS:
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(
                f"Image is too large ({len(data)//1_048_576} MB). "
                f"Maximum is {MAX_IMAGE_BYTES//1_048_576} MB."
            )
        mime     = _MIME.get(ext, "image/png")
        b64      = base64.b64encode(data).decode("ascii")
        data_uri = f"data:{mime};base64,{b64}"

        upload_id = _next_id
        _next_id += 1
        _uploads[upload_id] = {
            "kind":     "image",
            "filename": filename,
            "data_uri": data_uri,
            "bytes":    len(data),
        }
        return {"id": upload_id, "filename": filename, "kind": "image", "bytes": len(data)}

    # ── Text / document upload ───────────────────────────────────
    text = extract_text(filename, data)
    max_chars = 60_000
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[File truncated — showing first {max_chars:,} characters]"

    upload_id = _next_id
    _next_id += 1
    _uploads[upload_id] = {
        "kind":     "text",
        "filename": filename,
        "text":     text,
        "chars":    len(text),
    }
    return {"id": upload_id, "filename": filename, "kind": "text", "chars": len(text)}


def remove_upload(upload_id: int) -> bool:
    return _uploads.pop(upload_id, None) is not None


def list_uploads() -> list[dict]:
    out = []
    for uid, u in _uploads.items():
        if u["kind"] == "image":
            out.append({"id": uid, "filename": u["filename"], "kind": "image", "bytes": u["bytes"]})
        else:
            out.append({"id": uid, "filename": u["filename"], "kind": "text", "chars": u["chars"]})
    return out


def has_images() -> bool:
    """True if any image is currently uploaded."""
    return any(u["kind"] == "image" for u in _uploads.values())


def get_image_data_uris() -> list[str]:
    """Return all active image uploads as base64 data URIs (for the vision model)."""
    return [u["data_uri"] for u in _uploads.values() if u["kind"] == "image"]


def build_file_context() -> Optional[str]:
    """
    Build the TEXT file context block prepended to the chat prompt.
    Images are handled separately (sent to the vision model), not here.
    Returns None if no text files are active.
    """
    text_files = [u for u in _uploads.values() if u["kind"] == "text"]
    if not text_files:
        return None
    parts = [f"=== File: {u['filename']} ===\n{u['text']}" for u in text_files]
    return (
        "The user has provided the following document(s) for context. "
        "Refer to them when answering questions.\n\n"
        + "\n\n".join(parts)
    )
