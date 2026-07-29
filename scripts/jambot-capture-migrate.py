#!/usr/bin/env python3
"""
jambot-capture-migrate.py — one-time migration from flat capture-manifest.json
to server-authoritative album records in /app/runtime/captures/.

Safe: copies existing photos, never moves or deletes originals.
Run inside the OVU container:  python3 /app/scripts/jambot-capture-migrate.py
"""

import json, os, shutil, sys, time, uuid
from pathlib import Path

CAPTURES_DIR   = Path(os.getenv("CAPTURES_DIR", "/app/runtime/captures"))
UPLOADS_DIR    = Path(os.getenv("UPLOADS_DIR", "/app/runtime/uploads"))
MANIFEST_PATH  = UPLOADS_DIR / "capture-manifest.json"

def migrate():
    if not MANIFEST_PATH.exists():
        print("No capture-manifest.json found — nothing to migrate.")
        return 0

    with open(MANIFEST_PATH, "r") as f:
        manifest = json.load(f)

    photos = manifest if isinstance(manifest, list) else manifest.get("photos", manifest.get("images", []))
    if not photos:
        print("Manifest is empty — nothing to migrate.")
        return 0

    # Create captures structure
    (CAPTURES_DIR / "albums").mkdir(parents=True, exist_ok=True)
    (CAPTURES_DIR / "shares").mkdir(parents=True, exist_ok=True)

    album_id = uuid.uuid4().hex[:12]
    album_dir = CAPTURES_DIR / "albums" / album_id
    media_dir = album_dir / "media"
    thumb_dir = album_dir / "thumbs"
    media_dir.mkdir(parents=True)
    thumb_dir.mkdir(parents=True)

    migrated = 0
    skipped = 0
    photo_records = []

    for photo in photos:
        filename = photo.get("filename", photo.get("url", photo.get("src", "")))
        if not filename:
            skipped += 1
            continue

        src_path = UPLOADS_DIR / filename.replace("/uploads/", "")
        if not src_path.exists():
            # Try other common patterns
            src_path = UPLOADS_DIR / Path(filename).name
        if not src_path.exists():
            print(f"  SKIP (file not found): {filename}")
            skipped += 1
            continue

        photo_id = uuid.uuid4().hex[:16]
        ext = src_path.suffix or ".jpg"

        # Copy original to album media
        dest = media_dir / f"{photo_id}{ext}"
        shutil.copy2(src_path, dest)

        photo_records.append({
            "id": photo_id,
            "type": "photo" if ext.lower() in (".jpg", ".jpeg", ".png", ".heif", ".webp") else "video",
            "folder": photo.get("folder", photo.get("category", "Uncategorized")),
            "captured_at": photo.get("captured_at", photo.get("timestamp", photo.get("date", ""))),
            "uploaded_at": photo.get("uploaded_at", ""),
            "lat": photo.get("lat"),
            "lng": photo.get("lng"),
            "gps_accuracy_m": photo.get("gps_accuracy_m"),
            "note": photo.get("note", photo.get("notes", "")),
            "tags": photo.get("tags", photo.get("keywords", [])),
            "ai_caption": photo.get("ai_caption", photo.get("visionDesc", photo.get("description", ""))),
            "annotated": False,
            "annotated_url": None,
            "pair_before_id": None,
            "hidden_from_share": False,
            "deleted": False,
            "client_uuid": None,
            "seq": photo.get("seq", migrated),
        })
        migrated += 1

    # Build album record
    album = {
        "id": album_id,
        "name": "Migrated Photos",
        "client_name": "",
        "address": "",
        "lat": None, "lng": None, "geofence_m": None,
        "status": "in_progress",
        "folders": sorted(set(p["folder"] for p in photo_records if p["folder"])),
        "share": {"token": None, "enabled": False, "scope": "all"},
        "photos": photo_records,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    # Write album.json
    with open(album_dir / "album.json", "w") as f:
        json.dump(album, f, indent=2, default=str)

    # Update index.json
    index_path = CAPTURES_DIR / "index.json"
    index = []
    if index_path.exists():
        with open(index_path, "r") as f:
            index = json.load(f)
    index.append({
        "id": album_id,
        "name": album["name"],
        "status": album["status"],
        "photo_count": migrated,
        "cover_url": f"/api/capture/albums/{album_id}/media/{photo_records[0]['id']}" if photo_records else None,
        "updated_at": album["updated_at"],
    })
    with open(index_path, "w") as f:
        json.dump(index, f, indent=2, default=str)

    print(f"Migration complete: {migrated} photos migrated, {skipped} skipped.")
    print(f"Album ID: {album_id}")
    print(f"Album dir: {album_dir}")
    print(f"Original manifest preserved at: {MANIFEST_PATH}")
    return migrated


if __name__ == "__main__":
    count = migrate()
    sys.exit(0 if count >= 0 else 1)
