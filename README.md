# Apple Books Notes

A local notes browser for [Apple Books](https://www.apple.com/apple-books/) on macOS.

The Books sidebar truncates both the highlighted passage and your comment, and clicking an item jumps into the book. This tool reads the **full** text from the local SQLite databases and shows it as a list: book → chapter → section. Nothing is uploaded. The Books database is opened read-only.

Not affiliated with Apple.

## Requirements

- macOS (Apple Books installed, with at least one highlight or note)
- Python 3.9+ (stdlib only, no pip packages)

## Run

```bash
git clone https://github.com/yufanghui/books-notes.git
cd books-notes
python3 serve.py
```

Then open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

```text
python3 serve.py --port 8765      # change port
python3 serve.py --no-open        # do not launch a browser
```

If the page cannot read the library, grant **Full Disk Access** to Terminal (or iTerm / Cursor) in System Settings → Privacy & Security, open the Books app once, and click Refresh.

## What you get

- Full original quote and full note, no truncation
- Nested table of contents from EPUB locations (chapter and subsection)
- Click a subsection to see only that subsection
- Filter to notes-only, and search across quote + comment
- Chinese / English UI (follows the browser language, toggle in the toolbar)

## How it works

Apple Books stores:

| What | Where |
| --- | --- |
| Book list | `~/Library/Containers/com.apple.iBooksX/Data/Documents/BKLibrary/` |
| Highlights & notes | `~/Library/Containers/com.apple.iBooksX/Data/Documents/AEAnnotation/` |

Each annotation has `ZANNOTATIONSELECTEDTEXT` (quote), `ZANNOTATIONNOTE` (your comment), and `ZANNOTATIONLOCATION` (EPUB CFI). Chapter and section titles are parsed from the CFI, which is why subsections that never appear in the Books TOC still show up here.

The server copies the databases into memory with SQLite `backup()` so the live Books files are never written.

## Privacy

- Binds to `127.0.0.1` only
- Read-only access to your Books databases
- No accounts, no network calls, no analytics

## License

MIT

---

# 中文

Apple Books 侧栏会截断原文和评论，点进去还会跳页。这个本地页从 Books 的 SQLite 里读完整划线和评论，按书 / 大章 / 小章浏览，不会跳走。

```bash
python3 serve.py
```

读完一章后点「刷新」。默认「只看有评论」。读库失败时，给终端开一下「完全磁盘访问权限」。
