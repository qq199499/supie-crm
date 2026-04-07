from __future__ import annotations

import os
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "dist"
PACKAGE_ROOT_NAME = "crm"

EXCLUDE_DIRS = {
    ".cursor",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "logs",
    "terminals",
    "agent-transcripts",
    "dist",
    "tests",
}

EXCLUDE_FILES = {
    "crm.db",
}


def should_skip_dir(path: Path) -> bool:
    return any(part in EXCLUDE_DIRS for part in path.parts)


def should_skip_file(path: Path) -> bool:
    return path.name in EXCLUDE_FILES


def iter_package_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(ROOT)
        if should_skip_dir(rel):
            continue
        if should_skip_file(rel):
            continue
        files.append(path)
    return sorted(files)


def build_zip(zip_path: Path) -> None:
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for src in iter_package_files():
            archive.write(src, Path(PACKAGE_ROOT_NAME) / src.relative_to(ROOT))


def build_tar_gz(tar_path: Path) -> None:
    with tarfile.open(tar_path, "w:gz") as archive:
        for src in iter_package_files():
            archive.add(src, arcname=str(Path(PACKAGE_ROOT_NAME) / src.relative_to(ROOT)))


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    windows_zip = OUTPUT_DIR / "crm-install-windows.zip"
    linux_tar = OUTPUT_DIR / "crm-install-linux.tar.gz"

    for path in (windows_zip, linux_tar):
        if path.exists():
            path.unlink()

    build_zip(windows_zip)
    build_tar_gz(linux_tar)

    print(windows_zip)
    print(linux_tar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
