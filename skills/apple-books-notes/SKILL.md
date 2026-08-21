---
name: apple-books-notes
description: >-
  Reads local Apple Books (iBooks) highlights and notes on macOS: full quote
  text, user comments, book/chapter/section navigation. Use when the user
  mentions Apple Books, Books.app, iBooks, 划线, 笔记, 书摘, 读书笔记, highlights,
  annotations, reviewing a chapter after reading, or wants to browse/export
  notes without jumping back into the book.
---

# Apple Books Notes

macOS only. Reads the local Books SQLite databases (read-only). Do not scrape the Books UI.

## Script

`scripts/query.py` lives next to this `SKILL.md`. Run with `python3`. No pip packages.

```bash
QUERY="$(dirname "$0")/scripts/query.py"   # if cwd is the skill folder: ./scripts/query.py
```

From this repository root:

```bash
python3 skills/apple-books-notes/scripts/query.py --list
```

If the command fails with a database/permission error: tell the user to open the Books app once, then grant **Full Disk Access** to Terminal / Cursor.

## Workflow

1. If the book is unknown, `--list` first.
2. Fetch notes with the tightest filters (`--book`, `--chapter`, `--section`, `--query`, `--notes-only`). `--section` matches any heading in the path. Paths deeper than chapter/section appear only when the EPUB confirms them; otherwise stop at two levels.
3. Answer from the script output. Keep **原文** and **评论** in full. Group by the heading path (`##` / `###` / `####` …).
4. When the user is reviewing thoughts, pass `--notes-only`. Include bare highlights only if they ask for 划线/highlights.
5. Reply in the user's language.

Do not dump the entire library unless they ask for everything.

## Commands

List books:

```bash
python3 skills/apple-books-notes/scripts/query.py --list
```

Chapter review:

```bash
python3 skills/apple-books-notes/scripts/query.py --book "Swift 异步" --chapter "协作式任务取消" --notes-only
```

One subsection:

```bash
python3 skills/apple-books-notes/scripts/query.py --book "Swift 异步" --section "处理任务取消" --notes-only
```

Search:

```bash
python3 skills/apple-books-notes/scripts/query.py --book "Swift" --query "onCancel" --notes-only
```

JSON (only when you need structured data):

```bash
python3 skills/apple-books-notes/scripts/query.py --book "Swift" --json
```

Default stdout is Markdown: `# book` / nested headings (`##` chapter, `###` section, deeper only if verified) / **原文** / **评论**.

When the skill is installed standalone, replace `skills/apple-books-notes/scripts/query.py` with the `scripts/query.py` path beside `SKILL.md`.

## Visual browser

For the clickable list page, share images, or copy-image preview, start the git checkout UI (not bundled in a standalone skill install):

```bash
python3 serve.py --no-open
```

Then tell them to open http://127.0.0.1:8765/

## Privacy

- Localhost / local files only
- Never write the Books databases
- Never upload notes
