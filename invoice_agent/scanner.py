from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable, List


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".ofd",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".tiff",
    ".tif",
    ".bmp",
}


def scan_documents(folder: Path) -> List[Path]:
    folder = folder.expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    return sorted(
        [
            path
            for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        ],
        key=lambda path: str(path.relative_to(folder)).lower(),
    )


def is_inside(path: Path, maybe_parent: Path) -> bool:
    try:
        path.resolve().relative_to(maybe_parent.resolve())
        return True
    except ValueError:
        return False


def is_strict_child(path: Path, maybe_parent: Path) -> bool:
    return path.resolve() != maybe_parent.resolve() and is_inside(path, maybe_parent)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()
