from __future__ import annotations

import shutil
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
OUTPUT_DIR = DOCS_DIR / "pure-packages"

PACKAGE_FILES: list[tuple[Path, str]] = [
    (DOCS_DIR / "系统文档" / "角色权限矩阵.md", "系统文档/角色权限矩阵.md"),
    (DOCS_DIR / "manuals" / "USER_MANUAL.md", "系统文档/USER_MANUAL.md"),
    (DOCS_DIR / "manuals" / "USER_MANUAL_PRINT.md", "系统文档/USER_MANUAL_PRINT.md"),
    (DOCS_DIR / "数据库脚本" / "crm_init_postgres_schema.sql", "数据库脚本/crm_init_postgres_schema.sql"),
]


def build_one(zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        for src, arcname in PACKAGE_FILES:
            if not src.exists():
                raise FileNotFoundError(f"missing source file: {src}")
            archive.write(src, arcname)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    packages = {
        "windows": OUTPUT_DIR / "windows-doc-package.zip",
        "linux": OUTPUT_DIR / "linux-doc-package.zip",
    }
    for path in packages.values():
        if path.exists():
            path.unlink()
    for path in packages.values():
        build_one(path)
    for name, path in packages.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
