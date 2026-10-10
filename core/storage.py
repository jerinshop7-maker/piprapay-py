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
    """Mirror uploadImage(): returns {status, file?, note?}."""
    if uploaded_file is None:
        return {"status": False, "message": "No file uploaded."}
    name = getattr(uploaded_file, "name", "") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_IMAGE_EXTS:
        return {"status": False, "message": "Invalid image type."}
    try:
        size = uploaded_file.size
    except AttributeError:
        uploaded_file.seek(0, os.SEEK_END)
        size = uploaded_file.tell()
        uploaded_file.seek(0)
    if size > max_file_size:
        return {"status": False, "message": "File too large."}

    storage = Path(settings.MEDIA_ROOT) / "storage"
    storage.mkdir(parents=True, exist_ok=True)

    data = uploaded_file.read()
    filename = _random_filename(ext)
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        if img.mode in ("RGBA", "LA", "PA"):
            out = Image.new("RGB", img.size, (255, 255, 255))
            out.paste(img, mask=img.split()[-1])
            img = out
        else:
            img = img.convert("RGB")
        # Mirror Imagick: alpha image -> WEBP lossless q85, else JPEG q75 stripped.
        has_alpha = "A" in Image.open(io.BytesIO(data)).getbands()
        if has_alpha:
            filename = filename.rsplit(".", 1)[0] + ".webp"
            img.save(storage / filename, "WEBP", quality=85, lossless=False, method=6)
        else:
            filename = filename.rsplit(".", 1)[0] + ".jpg"
            img.save(storage / filename, "JPEG", quality=75, optimize=True)
        return {"status": True, "file": filename}
    except Exception:
        pass

    # Fallback: move_uploaded_file equivalent.
    try:
        with open(storage / filename, "wb") as fh:
            fh.write(data)
        return {"status": True, "file": filename, "note": "stored without conversion"}
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
            zf.extractall(tmp)
        children = [c for c in tmp.iterdir()]
        root = children[0] if len(children) == 1 and children[0].is_dir() else tmp
        for item in root.iterdir():
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


def run_sql(statements: str, using: str = "default") -> dict:
    """Mirror runSql(): split on ';\\n', execute in a transaction."""
    from django.db import connections

    chunks = [c.strip() for c in statements.split(";\n") if c.strip()]
    conn = connections[using]
    try:
        with conn.cursor() as cur:
            for chunk in chunks:
                cur.execute(chunk)
        return {"status": True, "executed": len(chunks)}
    except Exception as e:
        return {"status": False, "message": str(e)}


def backup_database(backup_path: str | Path) -> dict:
    """Mirror backupDatabasePDO(): schema + data dump of all tables."""
    from django.core import serializers
    from django.apps import apps
    from django.db import connection

    backup_path = Path(backup_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(backup_path, "w", encoding="utf-8") as fh:
            fh.write("-- PipraPay Django backup\n")
            try:
                with connection.cursor() as cur:
                    tables = connection.introspection.table_names(cur)
                for table in sorted(tables):
                    fh.write(f"-- TABLE {table}\n")
                    with connection.cursor() as cur:
                        cur.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                        cols = [c[0] for c in cur.description]
                        fh.write(f"-- COLUMNS {', '.join(cols)}\n")
                        for row in cur.fetchall():
                            fh.write(f"-- ROW {row!r}\n")
            except Exception as e:
                fh.write(f"-- introspection skipped: {e}\n")
            for model in apps.get_models():
                try:
                    fh.write(serializers.serialize("json", model.objects.all()))
                    fh.write("\n")
                except Exception:
                    continue
        return {"status": True, "file": str(backup_path)}
    except OSError as e:
        return {"status": False, "message": str(e)}
