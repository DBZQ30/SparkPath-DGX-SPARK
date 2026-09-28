from enum import Enum
import os
from typing import ClassVar

class FileType(Enum):
    DOCUMENT = "document"
    STRUCTURED_TABLE = "structured_table"
    UNKNOWN = "unknown"

class ExtractorRouter:
    # Common image extensions that may appear as .bin from WeCom cache.
    _IMAGE_MAGIC: ClassVar[dict[bytes, str]] = {
        b"\x89PNG\r\n\x1a\n": ".png",
        b"\xff\xd8\xff": ".jpg",
        b"GIF87a": ".gif",
        b"GIF89a": ".gif",
        b"RIFF": ".webp",  # RIFF....WEBP
    }

    def __init__(self):
        self.doc_extensions = {".pdf", ".docx", ".doc", ".txt", ".md", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".pptx"}
        self.table_extensions = {".xlsx", ".xls", ".csv"}

    def detect_image_extension(self, file_path: str) -> str | None:
        """Return the correct image extension for *file_path* based on magic bytes.

        Returns ``None`` when the file doesn't exist or isn't a recognized
        image format.  Used by the orchestrator to rename mislabeled files
        (e.g. WeCom .bin → .jpg) before passing them to MinerU.
        """
        if not os.path.isfile(file_path):
            return None
        try:
            with open(file_path, "rb") as fh:
                header = fh.read(12)
            for magic, ext in self._IMAGE_MAGIC.items():
                if header.startswith(magic):
                    # RIFF is a generic container (WebP/WAV/AVI/ANI).
                    # Only return ".webp" when sub-format at offset 8 is "WEBP".
                    if magic == b"RIFF":
                        if header[8:12] == b"WEBP":
                            return ext
                        continue
                    return ext
        except OSError:
            pass
        return None

    def route(self, filename: str) -> FileType:
        ext = os.path.splitext(filename)[1].lower()
        if ext in self.table_extensions:
            return FileType.STRUCTURED_TABLE
        if ext in self.doc_extensions:
            return FileType.DOCUMENT
        # Unknown extension — try content-based detection for images
        # mislabeled as .bin by WeCom adapter (URL path wins over magic bytes).
        if os.path.isfile(filename):
            try:
                with open(filename, "rb") as fh:
                    header = fh.read(12)
                for magic, _img_ext in self._IMAGE_MAGIC.items():
                    if header.startswith(magic):
                        return FileType.DOCUMENT
            except OSError:
                pass
        return FileType.UNKNOWN
