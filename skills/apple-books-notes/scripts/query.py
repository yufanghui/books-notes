#!/usr/bin/env python3
"""Read Apple Books highlights and notes. Used by the skill and the local web UI."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

HOME = Path.home()
ANN_DIR = HOME / "Library/Containers/com.apple.iBooksX/Data/Documents/AEAnnotation"
LIB_DIR = HOME / "Library/Containers/com.apple.iBooksX/Data/Documents/BKLibrary"
CORE_DATA_EPOCH = 978307200
CFI_SPINE = re.compile(r"epubcfi\(/6/(\d+)(?:\[([^\]]+)\])?")
CFI_HEADINGS = re.compile(r"\[([^\]]+)\]")
SECTION_OPEN = re.compile(r"<section\b([^>]*)>", re.I)
HEADING_AFTER = re.compile(r"\s*<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)
SKIP_XHTML = {"nav.xhtml", "nav.html", "cover.xhtml", "title_page.xhtml", "toc.xhtml"}
MAX_UNVERIFIED_HEADINGS = 2
_HEADING_CACHE: dict[tuple[str, int], dict[str, list[str]]] = {}


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


def library_revision() -> str:
    """Cheap fingerprint of Books sqlite files so the UI can auto-reload."""
    parts: list[str] = []
    for directory in (ANN_DIR, LIB_DIR):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            name = path.name
            if not (name.endswith(".sqlite") or name.endswith(".sqlite-wal")):
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
    return "|".join(sorted(parts))


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


def _clean_heading(text: str) -> str:
    text = re.sub(r"[- ]\d+$", "", text).replace("-", " ").strip()
    return text


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf"""\b{name}\s*=\s*["']([^"']+)["']""", attrs, re.I)
    return m.group(1) if m else None


def _plain_html(html: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html)).replace("\xa0", " ").strip()


def _cfi_start_path(cfi: str) -> str:
    """Range CFI is epubcfi(common, start, end). Ignore the end so sibling sections stay siblings."""
    inner = cfi.strip()
    if inner.lower().startswith("epubcfi(") and inner.endswith(")"):
        inner = inner[8:-1]
    parts = inner.split(",")
    if len(parts) >= 2:
        return parts[0] + parts[1]
    return inner


def _iter_epub_html(path: Path):
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.suffix.lower() in {".xhtml", ".html"} and p.name.lower() not in SKIP_XHTML:
                yield p.read_text(encoding="utf-8", errors="replace")
        return
    if path.is_file() and path.suffix.lower() == ".epub":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if Path(name).suffix.lower() in {".xhtml", ".html"} and Path(name).name.lower() not in SKIP_XHTML:
                    yield z.read(name).decode("utf-8", errors="replace")


def load_heading_map(epub_path: str | None) -> dict[str, list[str]]:
    """Map EPUB section id -> ancestor heading titles, including self.

    Only sections with an explicit level class (e.g. Pandoc ``class="level3"``)
    are indexed. Deeper headings are omitted unless the EPUB confirms them.
    """
    if not epub_path or not str(epub_path).lower().endswith(".epub"):
        return {}
    path = Path(epub_path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    cache_key = (str(path), mtime)
    cached = _HEADING_CACHE.get(cache_key)
    if cached is not None:
        return cached
    index: dict[str, list[str]] = {}
    try:
        for html in _iter_epub_html(path):
            stack: list[tuple[int, str]] = []
            for m in SECTION_OPEN.finditer(html):
                sid = _attr(m.group(1), "id")
                cls = _attr(m.group(1), "class") or ""
                level_m = re.search(r"level(\d+)", cls)
                if not sid or not level_m:
                    continue
                level = int(level_m.group(1))
                hm = HEADING_AFTER.match(html[m.end() :])
                title = _plain_html(hm.group(2)) if hm else ""
                if not title:
                    title = _clean_heading(sid)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                index[sid] = [t for _, t in stack]
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError):
        index = {}
    _HEADING_CACHE[cache_key] = index
    return index


def parse_cfi(cfi: str | None, heading_map: dict[str, list[str]] | None = None) -> dict:
    if not cfi:
        return {"spine": 10**9, "file_id": "unknown", "headings": [], "chapter": "Ungrouped", "section": ""}
    spine_m = CFI_SPINE.search(cfi)
    spine = int(spine_m.group(1)) if spine_m else 10**9
    file_id = (spine_m.group(2) if spine_m else None) or "unknown"
    after = _cfi_start_path(cfi)
    after = after.split("!", 1)[-1] if "!" in after else after
    raw_ids = CFI_HEADINGS.findall(after)
    headings: list[str] = []
    if heading_map:
        for hid in reversed(raw_ids):
            path = heading_map.get(hid)
            if path:
                headings = path
                break
    if not headings:
        # CFI ids are not heading levels. Without EPUB confirmation, keep
        # chapter + section only rather than guessing a 3rd+ level.
        headings = [_clean_heading(h) for h in raw_ids if h][:MAX_UNVERIFIED_HEADINGS]
    chapter = headings[0] if headings else file_id
    section = headings[1] if len(headings) > 1 else ""
    return {
        "spine": spine,
        "file_id": file_id,
        "headings": headings,
        "chapter": chapter,
        "section": section,
    }


def load_library() -> dict:
    ann = readonly_backup(latest_sqlite(ANN_DIR))
    lib = readonly_backup(latest_sqlite(LIB_DIR))
    try:
        asset_paths: dict[str, str | None] = {}
        assets = {}
        heading_maps: dict[str, dict[str, list[str]]] = {}
        for row in lib.execute(
            """
            SELECT ZASSETID, ZTITLE, ZAUTHOR, ZPATH
            FROM ZBKLIBRARYASSET
            WHERE ZASSETID IS NOT NULL
            """
        ):
            assets[row["ZASSETID"]] = {
                "id": row["ZASSETID"],
                "title": row["ZTITLE"] or "Untitled",
                "author": row["ZAUTHOR"] or "",
            }
            asset_paths[row["ZASSETID"]] = row["ZPATH"]

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
            hmap = heading_maps.get(asset_id)
            if hmap is None:
                hmap = load_heading_map(asset_paths.get(asset_id))
                heading_maps[asset_id] = hmap
            loc = parse_cfi(row["ZANNOTATIONLOCATION"], hmap)
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
                    "headings": loc["headings"],
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
            items = []
            for item in ch.get("items", []):
                heads = item.get("headings") or []
                if notes_only and not item.get("note"):
                    continue
                if chapter and not (
                    _match(ch.get("title", ""), chapter)
                    or any(_match(h, chapter) for h in heads)
                ):
                    continue
                if section and not any(_match(h, section) for h in heads[1:] or heads):
                    continue
                if query:
                    blob = (
                        f"{item.get('quote','')}\n{item.get('note','')}\n"
                        f"{' '.join(heads)}\n{ch.get('title','')}"
                    )
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
            last_path: list[str] = []
            for item in ch.get("items", []):
                path = item.get("headings") or ([ch.get("title")] if ch.get("title") else [])
                for i, heading in enumerate(path):
                    if i >= len(last_path) or last_path[i] != heading:
                        parts.append(f"{'#' * (i + 2)} {heading}")
                last_path = path
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
