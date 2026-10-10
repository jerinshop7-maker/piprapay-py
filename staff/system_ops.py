"""
System update / module import / addon helpers — port of
system-settings-update-*, system-settings-import and addon-* handlers
(pp-adapter.php:7161-7509, 7988-8360).
"""
from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import requests
from django.conf import settings

from core import money as M
from core.env import get_env, set_env
from core.storage import backup_database, copy_folder, delete_folder, extract_update, run_sql, zip_folder

MANIFEST_URL = "https://updates.piprapay.com/manifest.json"
UPDATE_DIR = Path(settings.MEDIA_ROOT) / "storage" / "updates"
APP_ROOT = Path(settings.BASE_DIR)


def _php_version_compare(a: str, b: str) -> int:
    import re

    def parts(s: str):
        out = []
        for chunk in re.split(r"[.\-+_]", str(s)):
            m = re.match(r"\d+", chunk)
            out.append(int(m.group(0)) if m else 0)
        return out

    pa, pb = parts(a), parts(b)
    for x, y in zip(pa, pb):
        if x != y:
            return 1 if x > y else -1
    if len(pa) != len(pb):
        longer, shorter = (pa, pb) if len(pa) > len(pb) else (pb, pa)
        if any(v != 0 for v in longer[len(shorter):]):
            return 1 if len(pa) > len(pb) else -1
    return 0


def update_check() -> dict:
    """Mirror system-settings-update-check."""
    set_env("last-auto-update-check", M.get_current_datetime())
    try:
        manifest = requests.get(MANIFEST_URL, timeout=15).json()
    except Exception as e:
        return {"status": "false", "title": "Request Failed", "message": f"Could not fetch manifest: {e}"}
    channel = get_env("system-settings-update_channel") or "stable"
    channel = "stable" if channel in ("", "--", "stable") else "beta"
    ch = (manifest.get("channels") or {}).get(channel) or {}
    current = settings.PIPRAPAY_VERSION["version_code"]
    latest = ch.get("latest_version_code")
    if latest and _php_version_compare(str(latest), str(current)) > 0:
        set_env("last-update-version-name", ch.get("latest_version_name", ""))
        set_env("last-update-version", str(latest))
        if ch.get("hash"):
            set_env("last-update-version-hash", str(ch["hash"]))
        return {"status": "true", "title": "Update Available",
                "message": f"Version {ch.get('latest_version_name')} is available.",
                "version": str(latest)}
    set_env("last-update-version-name", settings.PIPRAPAY_VERSION["version_name"])
    set_env("last-update-version", str(current))
    return {"status": "true", "title": "Up To Date", "message": "You are running the latest version."}


def update_download() -> dict:
    """Mirror system-settings-update-download: fetch zip to storage/updates/."""
    version = get_env("last-update-version")
    if version in ("--", "", None):
        return {"status": "false", "title": "Request Failed", "message": "Run Check for Updates first."}
    UPDATE_DIR.mkdir(parents=True, exist_ok=True)
    dest = UPDATE_DIR / f"{version}.zip"
    try:
        r = requests.get(f"https://updates.piprapay.com/download.php?version={version}",
                         timeout=120, verify=False)
        r.raise_for_status()
        dest.write_bytes(r.content)
    except Exception as e:
        return {"status": "false", "title": "Request Failed", "message": f"Download failed: {e}"}
    return {"status": "true", "title": "Download Complete",
            "message": f"Version {version} downloaded.", "file": str(dest)}


def update_install() -> dict:
    """Mirror system-settings-update-install: sha1 verify, backup, maintenance flag,
    extract, run update.sql, cleanup."""
    version = get_env("last-update-version")
    expected = get_env("last-update-version-hash")
    zf = UPDATE_DIR / f"{version}.zip" if version not in ("--", "", None) else None
    if not zf or not zf.exists():
        return {"status": "false", "title": "Request Failed", "message": "Download the update first."}
    digest = hashlib.sha1(zf.read_bytes()).hexdigest()
    if expected not in ("--", "", None) and digest != expected:
        return {"status": "false", "title": "Request Failed",
                "message": "Update package hash mismatch. Aborting."}
    # Backup: full tree zip + database dump.
    backup_dir = Path(settings.MEDIA_ROOT) / "storage" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = M.get_current_datetime("%Y%m%d%H%M%S")
    try:
        zip_folder(APP_ROOT, backup_dir / f"backup-{stamp}.zip")
    except Exception as e:
        return {"status": "false", "title": "Request Failed", "message": f"Backup failed: {e}"}
    bk = backup_database(backup_dir / f"db_{stamp}.json")
    if not bk.get("status"):
        return {"status": "false", "title": "Request Failed",
                "message": f"Database backup failed: {bk.get('message')}"}
    flag = APP_ROOT / ".maintenance"
    try:
        flag.write_text("updating")
        if not extract_update(zf, APP_ROOT):
            return {"status": "false", "title": "Request Failed", "message": "Extraction failed."}
        sql_file = APP_ROOT / "update.sql"
        if sql_file.exists():
            result = run_sql(sql_file.read_text(encoding="utf-8", errors="replace"))
            sql_file.unlink(missing_ok=True)
            if not result.get("status"):
                return {"status": "false", "title": "Request Failed",
                        "message": f"Migration SQL failed: {result.get('message')}"}
        try:
            zf.unlink()
        except OSError:
            pass
        return {"status": "true", "title": "Update Installed",
                "message": f"Version {version} installed successfully."}
    finally:
        try:
            flag.unlink()
        except OSError:
            pass


def import_module(uploaded_file) -> dict:
    """Mirror system-settings-import: <=100MB zip -> temp extract -> copy to root
    -> run sql.sql if present -> cleanup."""
    if uploaded_file is None:
        return {"status": "false", "title": "Request Failed", "message": "No file uploaded."}
    name = getattr(uploaded_file, "name", "") or ""
    if not name.lower().endswith(".zip"):
        return {"status": "false", "title": "Request Failed", "message": "Only .zip files are accepted."}
    try:
        size = uploaded_file.size
    except AttributeError:
        size = len(uploaded_file.read())
        uploaded_file.seek(0)
    if size > 100 * 1024 * 1024:
        return {"status": "false", "title": "Request Failed", "message": "File exceeds 100 MB."}
    tmp_root = Path(settings.MEDIA_ROOT) / "storage" / "temp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    dest = tmp_root / name
    try:
        with open(dest, "wb") as fh:
            for chunk in uploaded_file.chunks():
                fh.write(chunk)
        with zipfile.ZipFile(dest) as zf:
            if zf.testzip() is not None:
                return {"status": "false", "title": "Request Failed", "message": "Invalid zip archive."}
            extract_dir = tmp_root / dest.stem
            zf.extractall(extract_dir)
        children = [c for c in extract_dir.iterdir()]
        root = children[0] if len(children) == 1 and children[0].is_dir() else extract_dir
        copy_folder(root, APP_ROOT)
        sql_file = root / "sql.sql"
        if sql_file.exists():
            result = run_sql(sql_file.read_text(encoding="utf-8", errors="replace"))
            if not result.get("status"):
                return {"status": "false", "title": "Request Failed",
                        "message": f"Module SQL failed: {result.get('message')}"}
        return {"status": "true", "title": "Import Complete",
                "message": "Module imported successfully."}
    except zipfile.BadZipFile:
        return {"status": "false", "title": "Request Failed", "message": "Invalid zip archive."}
    except OSError as e:
        return {"status": "false", "title": "Request Failed", "message": str(e)}
    finally:
        delete_folder(tmp_root / dest.stem)
        try:
            dest.unlink()
        except OSError:
            pass


def install_addon(uploaded_file, *, name: str = "", status: str = "active") -> dict:
    """Mirror addons-create: zip upload -> pp-modules/pp-addons/<slug>/ + registry row."""
    from core.models import Addon

    if uploaded_file is None:
        return {"status": "false", "title": "Request Failed", "message": "No file uploaded."}
    fname = getattr(uploaded_file, "name", "") or "addon.zip"
    if not fname.lower().endswith(".zip"):
        return {"status": "false", "title": "Request Failed", "message": "Only .zip files are accepted."}
    slug = "".join(c.lower() if (c.isalnum() or c == "-") else "-" for c in fname[:-4]) or "addon"
    target = APP_ROOT / "pp-modules" / "pp-addons" / slug
    tmp = Path(settings.MEDIA_ROOT) / "storage" / "temp" / f"addon_{slug}"
    try:
        tmp.mkdir(parents=True, exist_ok=True)
        tmp_zip = tmp / fname
        with open(tmp_zip, "wb") as fh:
            for chunk in uploaded_file.chunks():
                fh.write(chunk)
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(tmp / "src")
        src = tmp / "src"
        children = [c for c in src.iterdir()]
        root = children[0] if len(children) == 1 and children[0].is_dir() else src
        copy_folder(root, target)
        addon_id = M.generate_item_id()
        Addon.objects.update_or_create(slug=slug, defaults={"addon_id": addon_id,
                                                            "name": name or slug, "status": status})
        return {"status": "true", "title": "Addon Installed",
                "message": f"Addon '{slug}' installed.", "slug": slug}
    except (zipfile.BadZipFile, OSError) as e:
        return {"status": "false", "title": "Request Failed", "message": str(e)}
    finally:
        delete_folder(tmp)
