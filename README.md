# Apple Books Notes

A local notes browser **and agent skill** for [Apple Books](https://www.apple.com/apple-books/) on macOS.

The Books sidebar truncates both the highlighted passage and your comment, and clicking an item jumps into the book. This tool reads the **full** text from the local SQLite databases.

Not affiliated with Apple.

## Agent skill (recommended)

Install into Cursor / Claude Code / Codex and ask in chat: “复习《Swift 异步与并发编程》协作式任务取消这一章的笔记”.

```bash
npx skills add yufanghui/books-notes -g
```

The skill runs `skills/apple-books-notes/scripts/query.py` (Python 3 stdlib, no pip). It lists books and returns full quotes + comments filtered by book / chapter / section.

## Visual browser

```bash
git clone https://github.com/yufanghui/books-notes.git
cd books-notes
python3 serve.py
```

Open [http://127.0.0.1:8765/](http://127.0.0.1:8765/).

```text
python3 serve.py --port 8765      # change port
python3 serve.py --no-open        # do not launch a browser
```

Query from the terminal without the UI:

```text
python3 skills/apple-books-notes/scripts/query.py --list
python3 skills/apple-books-notes/scripts/query.py --book "Swift" --notes-only
```

If the page or script cannot read the library, grant **Full Disk Access** to Terminal (or iTerm / Cursor) in System Settings → Privacy & Security, open the Books app once, and retry. Keep `serve.py` running: the page watches the Books database and reloads when you add or delete a note.

## What you get

- Full original quote and full note, no truncation
- Nested table of contents from EPUB locations (chapter and subsection)
- Click a subsection to see only that subsection
- Filter to notes-only, and search across quote + comment
- Share a note as an image, preview it, and copy the image
- Chinese / English UI (follows the browser language, toggle in the toolbar)
- Agent skill for chapter review in chat

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

Apple Books 侧栏会截断原文和评论，点进去还会跳页。这个仓库提供：

1. **Skill**：`npx skills add yufanghui/books-notes -g` 之后，在对话里让 AI 按书/章/节取出完整原文和评论。
2. **本地网页**：`python3 serve.py`，按章节浏览，还可生成分享图。

读库失败时，给终端 / Cursor 开一下「完全磁盘访问权限」。
