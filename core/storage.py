"""
File helpers — port of uploadImage/deleteImage/deleteFolder/copyFolder/
zipFolder/runSql/backupDatabasePDO/extractUpdate (pp-functions.php:1456-1710).

Image conversion uses Pillow (mirrors Imagick WEBP-lossless-85 / JPEG-75 +
strip). Filenames are 30-char random + ext, stored under MEDIA_ROOT/storage/.
"""
from __future__ import annotations

import io
import os
import secrets
import shutil
import string
import zipfile
from pathlib import Path

from django.conf import settings

ALLOWED_IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp"}


def _random_filename(ext: str) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(30)) + "." + ext.lower()


def upload_image(uploaded_file, max_file_size: int) -> dict:
    """Mirror uploadImage(): error -> size -> ext checks, Imagick conversion,
    move_uploaded_file fallback. Returns {status, file?, note?/message?}."""
    if uploaded_file is None:
        return {"status": False, "message": "No file uploaded or upload failed."}
    try:
        data = uploaded_file.read()
    except Exception:
        return {"status": False, "message": "No file uploaded or upload failed."}
    if not data:
        return {"status": False, "message": "No file uploaded or upload failed."}
    if len(data) > max_file_size:
        return {"status": False, "message": "File size exceeds maximum allowed."}
    name = getattr(uploaded_file, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_IMAGE_EXTS:
        return {"status": False, "message": "Only JPG, PNG, GIF, and WEBP files are allowed."}

    storage = Path(settings.MEDIA_ROOT) / "storage"
    try:
        storage.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"status": False, "message": "Failed to create folder!"}

    filename = _random_filename(ext)
    try:
        from PIL import Image

        probe = Image.open(io.BytesIO(data))
        has_alpha = "A" in probe.getbands() or bool(probe.info.get("transparency"))
        img = Image.open(io.BytesIO(data))
        if has_alpha:
            # Mirror Imagick: alpha -> WEBP lossless q85.
            filename = filename.rsplit(".", 1)[0] + ".webp"
            img.save(storage / filename, "WEBP", quality=85, lossless=True, method=6)
        else:
            img = img.convert("RGB")
            filename = filename.rsplit(".", 1)[0] + ".jpg"
            img.save(storage / filename, "JPEG", quality=75, optimize=True)
        return {"status": True, "file": filename}
    except Exception:
        pass

    # Fallback: keep original name/ext like move_uploaded_file.
    try:
        with open(storage / filename, "wb") as fh:
            fh.write(data)
        return {"status": True, "file": filename,
                "note": "Imagick not used. File uploaded without processing."}
    except OSError as e:
        return {"status": False, "message": str(e)}


def delete_image(filename: str) -> bool:
    """Mirror deleteImage(): basename() traversal guard + unlink."""
    base = os.path.basename(filename or "")
    if not base:
        return False
    try:
        (Path(settings.MEDIA_ROOT) / "storage" / base).unlink()
        return True
    except OSError:
        return False


def delete_folder(path: str | Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def copy_folder(src: str | Path, dst: str | Path) -> None:
    shutil.copytree(src, dst, dirs_exist_ok=True)


def zip_folder(source: str | Path, zip_file: str | Path) -> None:
    source = Path(source)
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in source.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(source))


def extract_update(zip_file: str | Path, destination: str | Path) -> bool:
    """Mirror extractUpdate(): strip top-level folder, copy contents over destination."""
    destination = Path(destination)
    tmp = Path(settings.MEDIA_ROOT) / "storage" / "temp" / ("upd_" + secrets.token_hex(4))
    try:
        with zipfile.ZipFile(zip_file) as zf:
            names = zf.namelist()
            if not names:
                return False
            prefix = names[0].split("/")[0] + "/"
            zf.extractall(tmp)
        # Strip the first entry's top folder from every entry (PHP semantics:
        # multi-root zips merge at the destination root).
        for item in list(tmp.iterdir()):
            if item.is_dir() and item.name == prefix[:-1] and len(names) > 1:
                for child in item.iterdir():
                    target = destination / child.name
                    if child.is_dir():
                        copy_folder(child, target)
                    else:
                        shutil.copy2(child, target)
            else:
                target = destination / item.name
                if item.is_dir():
                    copy_folder(item, target)
                else:
                    shutil.copy2(item, target)
        return True
    except (zipfile.BadZipFile, OSError):
        return False
    finally:
        delete_folder(tmp)


def run_sql(statements: str | Path, using: str = "default") -> dict:
    """Mirror runSql(): file path or SQL string, split on ';\\n', run inside
    begin/commit with rollback (raises 'SQL file not found' for missing paths)."""
    from django.db import connections, transaction

    if isinstance(statements, Path):
        if not statements.exists():
            return {"status": False, "message": "SQL file not found"}
        statements = statements.read_text(encoding="utf-8", errors="replace")
    chunks = [c.strip() for c in statements.split(";\n") if c.strip()]
    conn = connections[using]
    try:
        with transaction.atomic(using=using):
            with conn.cursor() as cur:
                for chunk in chunks:
                    cur.execute(chunk)
        return {"status": True, "executed": len(chunks)}
    except Exception as e:
        return {"status": False, "message": f"Database update failed: {e}"}


def _quote(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def backup_database(backup_path: str | Path) -> dict:
    """Mirror backupDatabasePDO(): restorable dump — table schemas (SHOW CREATE
    TABLE on MySQL, sqlite_master SQL on SQLite) plus quoted INSERT INTO rows,
    wrapped in foreign-key guards, runnable through run_sql()."""
    from django.db import connection

    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        engine = connection.vendor
        with open(backup_path, "w", encoding="utf-8") as fh:
            fh.write("-- PipraPay Django backup\n")
            fh.write("SET FOREIGN_KEY_CHECKS=0;\n" if engine == "mysql" else "PRAGMA foreign_keys=OFF;\n")
            with connection.cursor() as cur:
                tables = connection.introspection.table_names(cur)
            for table in sorted(tables):
                if table.startswith(("django_", "sqlite_")):
                    continue
                qn = connection.ops.quote_name(table)
                with connection.cursor() as cur:
                    if engine == "mysql":
                        cur.execute(f"SHOW CREATE TABLE {qn}")
                        row = cur.fetchone()
                        fh.write((row[1] if row and len(row) > 1 else "") + ";\n")
                    else:
                        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=%s",
                                    [table])
                        row = cur.fetchone()
                        if row and row[0]:
                            fh.write(row[0] + ";\n")
                    cur.execute(f"SELECT * FROM {qn}")
                    cols = [connection.ops.quote_name(c[0]) for c in cur.description]
                    for db_row in cur.fetchall():
                        fh.write(f"INSERT INTO {qn} ({', '.join(cols)}) VALUES "
                                 f"({', '.join(_quote(v) for v in db_row)});\n")
            fh.write("SET FOREIGN_KEY_CHECKS=1;\n" if engine == "mysql" else "PRAGMA foreign_keys=ON;\n")
        return {"status": True, "file": str(backup_path)}
    except Exception as e:
        return {"status": False, "message": str(e)}
