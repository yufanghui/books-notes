#!/usr/bin/env python3
"""Read Apple Books highlights and notes. Used by the skill and the local web UI."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
ANN_DIR = HOME / "Library/Containers/com.apple.iBooksX/Data/Documents/AEAnnotation"
LIB_DIR = HOME / "Library/Containers/com.apple.iBooksX/Data/Documents/BKLibrary"
CORE_DATA_EPOCH = 978307200
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


def _match(haystack: str, needle: str) -> bool:
    return needle.casefold() in (haystack or "").casefold()


def filter_library(
    data: dict,
    book: str | None = None,
    chapter: str | None = None,
    section: str | None = None,
    notes_only: bool = False,
    query: str | None = None,
) -> dict:
    books = []
    for b in data.get("books", []):
        if book and not (_match(b.get("title", ""), book) or _match(b.get("author", ""), book)):
            continue
        chapters = []
        for ch in b.get("chapters", []):
            if chapter and not _match(ch.get("title", ""), chapter):
                continue
            items = []
            for item in ch.get("items", []):
                if notes_only and not item.get("note"):
                    continue
                if section and not _match(item.get("section", ""), section):
                    continue
                if query:
                    blob = f"{item.get('quote','')}\n{item.get('note','')}\n{item.get('section','')}\n{ch.get('title','')}"
                    if not _match(blob, query):
                        continue
                items.append(item)
            if items:
                chapters.append({**ch, "items": items})
        if chapters:
            books.append({**b, "chapters": chapters})
    return {"books": books}


def to_markdown(data: dict) -> str:
    parts = []
    for b in data.get("books", []):
        parts.append(f"# {b.get('title') or 'Untitled'}")
        if b.get("author"):
            parts.append(b["author"])
        parts.append("")
        for ch in b.get("chapters", []):
            parts.append(f"## {ch.get('title') or ch.get('id')}")
            current_section = None
            for item in ch.get("items", []):
                sec = item.get("section") or ""
                if sec != current_section:
                    current_section = sec
                    if sec:
                        parts.append(f"### {sec}")
                if item.get("quote"):
                    parts.append("**原文**")
                    parts.append(item["quote"])
                    parts.append("")
                if item.get("note"):
                    parts.append("**评论**")
                    parts.append(item["note"])
                    parts.append("")
            parts.append("")
    return "\n".join(parts).strip() + "\n"


def list_books(data: dict) -> str:
    lines = []
    for b in data.get("books", []):
        lines.append(
            f"{b.get('title')}\t{b.get('author')}\t"
            f"notes={b.get('noteCount', 0)}\thighlights={b.get('highlightCount', 0)}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def main() -> None:
    parser = argparse.ArgumentParser(description="Query Apple Books highlights and notes.")
    parser.add_argument("--list", action="store_true", help="List books with note counts")
    parser.add_argument("--book", help="Filter by book title or author (substring)")
    parser.add_argument("--chapter", help="Filter by chapter title (substring)")
    parser.add_argument("--section", help="Filter by subsection title (substring)")
    parser.add_argument("--query", help="Search quote, note, and headings")
    parser.add_argument("--notes-only", action="store_true", help="Skip highlights without comments")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown")
    args = parser.parse_args()

    try:
        data = load_library()
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc

    if args.list:
        sys.stdout.write(list_books(data))
        return

    filtered = filter_library(
        data,
        book=args.book,
        chapter=args.chapter,
        section=args.section,
        notes_only=args.notes_only,
        query=args.query,
    )
    if args.json:
        json.dump(filtered, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return
    sys.stdout.write(to_markdown(filtered))


if __name__ == "__main__":
    main()
