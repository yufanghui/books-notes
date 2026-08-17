#!/usr/bin/env python3
"""Local Apple Books notes browser. Reads the on-disk SQLite DBs (full text)."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
ANN_DIR = HOME / "Library/Containers/com.apple.iBooksX/Data/Documents/AEAnnotation"
LIB_DIR = HOME / "Library/Containers/com.apple.iBooksX/Data/Documents/BKLibrary"
WEB_DIR = Path(__file__).resolve().parent / "web"
CORE_DATA_EPOCH = 978307200  # 2001-01-01 UTC
HOST = "127.0.0.1"

CFI_SPINE = re.compile(r"epubcfi\(/6/(\d+)(?:\[([^\]]+)\])?")
CFI_HEADINGS = re.compile(r"\[([^\]]+)\]")


def latest_sqlite(directory: Path) -> Path:
    files = sorted(
        (p for p in directory.glob("*.sqlite") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(
            f"Apple Books database not found: {directory}. "
            "Open the Books app once, then grant Full Disk Access to Terminal "
            "if macOS blocks the read."
        )
    return files[0]


def readonly_backup(path: Path) -> sqlite3.Connection:
    mem = sqlite3.connect(":memory:")
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        src.backup(mem)
    finally:
        src.close()
    mem.row_factory = sqlite3.Row
    return mem


def apple_date(value) -> str | None:
    if value is None:
        return None
    ts = float(value) + CORE_DATA_EPOCH
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_cfi(cfi: str | None) -> dict:
    if not cfi:
        return {"spine": 10**9, "file_id": "unknown", "chapter": "Ungrouped", "section": ""}
    spine_m = CFI_SPINE.search(cfi)
    spine = int(spine_m.group(1)) if spine_m else 10**9
    file_id = (spine_m.group(2) if spine_m else None) or "unknown"
    after = cfi.split("!", 1)[-1] if "!" in cfi else ""
    heads = CFI_HEADINGS.findall(after)
    chapter = heads[0] if heads else file_id
    section = heads[1] if len(heads) > 1 else ""
    chapter = re.sub(r"[- ]\d+$", "", chapter).replace("-", " ")
    section = re.sub(r"[- ]\d+$", "", section).replace("-", " ")
    return {
        "spine": spine,
        "file_id": file_id,
        "chapter": chapter,
        "section": section,
    }


def load_library() -> dict:
    ann = readonly_backup(latest_sqlite(ANN_DIR))
    lib = readonly_backup(latest_sqlite(LIB_DIR))
    try:
        assets = {
            row["ZASSETID"]: {
                "id": row["ZASSETID"],
                "title": row["ZTITLE"] or "Untitled",
                "author": row["ZAUTHOR"] or "",
            }
            for row in lib.execute(
                """
                SELECT ZASSETID, ZTITLE, ZAUTHOR
                FROM ZBKLIBRARYASSET
                WHERE ZASSETID IS NOT NULL
                """
            )
        }

        books: dict[str, dict] = {}
        for row in ann.execute(
            """
            SELECT
                ZANNOTATIONASSETID,
                ZANNOTATIONSELECTEDTEXT,
                ZANNOTATIONNOTE,
                ZANNOTATIONSTYLE,
                ZANNOTATIONLOCATION,
                ZANNOTATIONCREATIONDATE,
                ZANNOTATIONMODIFICATIONDATE,
                ZPLLOCATIONRANGESTART
            FROM ZAEANNOTATION
            WHERE IFNULL(ZANNOTATIONDELETED, 0) = 0
              AND (
                    (ZANNOTATIONSELECTEDTEXT IS NOT NULL AND length(ZANNOTATIONSELECTEDTEXT) > 0)
                 OR (ZANNOTATIONNOTE IS NOT NULL AND length(ZANNOTATIONNOTE) > 0)
              )
            ORDER BY ZPLLOCATIONRANGESTART ASC, ZANNOTATIONCREATIONDATE ASC
            """
        ):
            asset_id = row["ZANNOTATIONASSETID"]
            if not asset_id:
                continue
            meta = assets.get(asset_id) or {
                "id": asset_id,
                "title": "Unknown book",
                "author": "",
            }
            book = books.setdefault(
                asset_id,
                {
                    **meta,
                    "chapters": {},
                    "highlightCount": 0,
                    "noteCount": 0,
                    "lastModified": None,
                },
            )
            loc = parse_cfi(row["ZANNOTATIONLOCATION"])
            chapter = book["chapters"].setdefault(
                loc["file_id"],
                {
                    "id": loc["file_id"],
                    "title": loc["chapter"],
                    "spine": loc["spine"],
                    "items": [],
                },
            )
            quote = (row["ZANNOTATIONSELECTEDTEXT"] or "").strip()
            note = (row["ZANNOTATIONNOTE"] or "").strip()
            modified = apple_date(row["ZANNOTATIONMODIFICATIONDATE"])
            book["highlightCount"] += 1
            if note:
                book["noteCount"] += 1
            if modified and (book["lastModified"] is None or modified > book["lastModified"]):
                book["lastModified"] = modified
            chapter["items"].append(
                {
                    "quote": quote,
                    "note": note,
                    "section": loc["section"],
                    "style": row["ZANNOTATIONSTYLE"] or 0,
                    "created": apple_date(row["ZANNOTATIONCREATIONDATE"]),
                    "modified": modified,
                }
            )

        book_list = []
        for book in books.values():
            chapters = sorted(book["chapters"].values(), key=lambda c: (c["spine"], c["title"]))
            book_list.append({**book, "chapters": chapters})
        book_list.sort(key=lambda b: b["lastModified"] or "", reverse=True)
        return {"books": book_list}
    finally:
        ann.close()
        lib.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        print(f"[books-notes] {fmt % args}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/data":
            try:
                payload = json.dumps(load_library(), ensure_ascii=False).encode("utf-8")
                self._send(200, payload, "application/json; charset=utf-8")
            except Exception as exc:  # noqa: BLE001
                err = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self._send(500, err, "application/json; charset=utf-8")
            return
        self._send(404, b"not found", "text/plain; charset=utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browse Apple Books highlights and notes in a local webpage."
    )
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Do not open a browser window")
    args = parser.parse_args()

    server = ThreadingHTTPServer((HOST, args.port), Handler)
    url = f"http://{HOST}:{args.port}/"
    print(f"Books notes: {url}", flush=True)
    print("Keep this terminal open. Click Refresh in the page after new highlights.", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
