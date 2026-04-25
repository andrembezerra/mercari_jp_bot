---
name: mercari-bot-dev
description: >
  Development skill for the mercari_jp_bot project — a Telegram bot that monitors Mercari Japan
  listings via Buyee.jp scraping. Use this skill whenever implementing new features, refactoring
  existing modules, adding Telegram commands, modifying the scraper, changing the database schema,
  updating pricing/translation logic, or writing tests for this project. Also use it when asked
  about how the bot works, how to extend it, or what patterns to follow inside this codebase.
  Trigger on: "add a command", "new keyword filter", "change the scraper", "add a column",
  "implement X in the bot", "how does the bot handle", "write a test for".
---

# Mercari JP Bot — Dev Skill

## Project at a Glance

A Python bot that polls Buyee.jp (Mercari Japan wrapper) for new or cheaper listings matching
stored keywords, then sends Telegram photo notifications. Runs as a Docker container on an
infinite loop.

```
run_bot.py              ← entry point
src/
  runner.py             ← main loop: commands → scrape → notify → sleep
  scraper.py            ← Buyee HTML scraping, item dedup, retry logic
  telegram_client.py    ← Telegram polling + send
  commands.py           ← /help /keywords /addkeyword /removekeyword /summary
  database.py           ← SQLite (seen_items, keywords, notifications)
  config.py             ← Settings dataclass, loads key.env + config.ini
  pricing.py            ← JPY↔USD exchange rate, price text parsing
  translation.py        ← googletrans Japanese→English fallback
  types.py              ← ItemData, NotificationItem TypedDicts
  logging_setup.py      ← two loggers: default (WARNING) + info_logger (INFO)
tests/                  ← pytest suite per module
```

**Runtime config:**
- `key.env` → `BOT_TOKEN`, `CHAT_ID`
- `config.ini` → `[BOT_SETTINGS]` (DB_FILE, MAX_SEEN_ITEMS), `[DELAYS]` (KEYWORD_BATCH_DELAY, FULL_CYCLE_DELAY)
- Keywords managed at runtime via `/addkeyword` → stored in `keywords` table

---

## Core Data Flow

```
runner.py main()
  │
  ├─ TelegramClient.check_commands()    # poll getUpdates, dispatch commands.py handlers
  │
  └─ for keyword in load_keywords_from_db():
       fetch_items(keyword, conn, exchange_rate)   # scraper.py
         │
         ├─ GET https://buyee.jp/mercari/search?keyword=...&order-sort=desc-created_time&status=on_sale
         ├─ parse <li class="list"> → ItemData TypedDict
         ├─ get_seen_item(item_id)  →  upsert_seen_item() if new/cheaper
         └─ yield NotificationItem
       │
       └─ TelegramClient.send_photo(title, url, img_url, price, label)
            insert into notifications table
```

---

## Patterns to Follow

### Adding a Telegram command

1. Add the handler function in [src/commands.py](src/commands.py):
   ```python
   def cmd_my_command(conn: sqlite3.Connection, client: TelegramClient, args: str) -> None:
       # args is the text after the command name, stripped
       client.send_message("response text")
   ```

2. Wire it in `TelegramClient.check_commands()` in [src/telegram_client.py](src/telegram_client.py):
   ```python
   elif text.startswith("/mycommand"):
       cmd_my_command(conn, self, text[len("/mycommand"):].strip())
   ```

3. Add the command to the `/help` response in `cmd_help()`.

4. Write a test in `tests/test_commands.py` — mock `TelegramClient.send_message` and pass a real
   in-memory DB connection (use `init_db(":memory:")` from database.py).

### Adding a database column

1. Add the column to the `CREATE TABLE` statement in `init_db()` ([src/database.py](src/database.py)).

2. Add a migration block at the end of `init_db()`:
   ```python
   try:
       conn.execute("ALTER TABLE seen_items ADD COLUMN my_field TEXT DEFAULT ''")
   except sqlite3.OperationalError:
       pass  # column already exists
   ```

3. Update `get_seen_item()` and `upsert_seen_item()` to include the new field.

4. Update the `ItemData` or `NotificationItem` TypedDict in [src/types.py](src/types.py) if it's
   surfaced in the data flow.

### Modifying the scraper

The scraper ([src/scraper.py](src/scraper.py)) parses Buyee HTML. When the site structure changes:

- Target element: `<li class="list">` → `<a href="/mercari/item/...">` → title, price, image
- Image extraction handles both `src` attribute and `data-bind` attribute (lazy-loaded images)
- Price text goes through `convert_price_to_yen()` in pricing.py — handles ¥, US$, $, "yen"
- `fetch_with_retry()` wraps all HTTP calls with 3 retries + exponential backoff

If adding a new field to the scrape result, update `ItemData` in types.py first, then
`extract_items_from_search_html()`.

### Adding a new module

- Place it under `src/`
- Import lazily (inside functions) if the dependency is optional (see translation.py pattern)
- Log with `info_logger` for operational messages, root logger for warnings/errors
- Add a corresponding `tests/test_<module>.py`

---

## Python Best Practices for This Codebase

### Error handling
- Use `fetch_with_retry()` for all HTTP calls — don't write new retry loops inline
- Catch `requests.RequestException` for network errors, `sqlite3.OperationalError` for DB errors
- Log the exception with context before swallowing: `logger.warning("msg", exc_info=True)`
- 4xx HTTP errors → do not retry (permanent failure); 5xx/timeouts → retry with backoff

### Type hints
- Use `TypedDict` (already in types.py) for data structures passed between modules
- Annotate all function signatures; avoid bare `dict` in signatures
- `Optional[X]` / `X | None` for values that can be absent

### SQLite
- Always pass the `conn` object — never open a new connection inside a function
- Use `conn.execute()` as a context manager for transactions where possible
- WAL mode is set in `init_db()` — don't change journal mode elsewhere
- `PRAGMA busy_timeout = 5000` is already set; rely on it instead of manual retry for DB locks

### Logging
- Use `info_logger.info(...)` for scrape progress, command dispatch, notification sends
- Use `logger.warning(...)` for recoverable errors (parse failures, translation errors)
- Use `logger.error(...)` for failures that affect the loop (network down, DB error)
- Never log `BOT_TOKEN`, `CHAT_ID`, or any PII

### Testing
- `init_db(":memory:")` creates a fresh in-memory DB — use it in every test that touches the DB
- Mock HTTP with `unittest.mock.patch("src.scraper.fetch_with_retry")` returning sample HTML
- Keep sample HTML fixtures in a `tests/fixtures/` directory for scraper tests
- Don't mock the DB — test against the real in-memory SQLite

### Configuration
- All settings come from `Settings` dataclass in config.py — don't read env vars directly in modules
- If adding a new setting, add it to `Settings`, load it in `load_settings()`, validate there

---

## Key Invariants (Don't Break These)

- `seen_items.item_id` is the Mercari item ID extracted from the Buyee URL path (`/mercari/item/<id>`)
- A notification is only sent when an item is **new** or its price **dropped below the stored minimum**
- `upsert_seen_item()` uses `INSERT OR REPLACE` — it updates the price on conflict
- `check_commands()` advances the `offset` after processing to prevent reprocessing
- The bot sends a "bot stopped" Telegram message on clean shutdown (KeyboardInterrupt / SystemExit)

---

## Common Tasks Quick Reference

| Task | File(s) to touch |
|------|-----------------|
| Add Telegram command | commands.py + telegram_client.py |
| New DB column | database.py (CREATE TABLE + ALTER migration + get/upsert functions) + types.py |
| Change scrape logic | scraper.py + types.py if new fields |
| Add price rule | pricing.py |
| Change notification format | telegram_client.py → `send_photo()` |
| Add config setting | config.py → Settings dataclass + load_settings() |
| Add test | tests/test_<module>.py, use in-memory DB, mock HTTP |
| Change delays | config.ini [DELAYS] section — no code change needed |

---

## Running Locally

```bash
# Install deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run
cp key.env.example key.env   # fill BOT_TOKEN and CHAT_ID
python run_bot.py

# Tests
pytest tests/ -v

# Docker
docker compose up --build
```

---

## Security Best Practices

### Secrets management
- `key.env` is already in `.gitignore` — never add it or any `*.env` variant to version control
- Install `git-secrets` and add a pre-commit hook scanning for `BOT_TOKEN`/`CHAT_ID` patterns
- Fail fast if secrets are missing — don't use `os.environ.get("BOT_TOKEN", "default")` fallbacks
- In Docker, pass secrets via `-e` or `docker run --env` at runtime, not via `env_file:` in a committed compose file

### HTML injection in Telegram messages
- `send_message()` uses HTML parse mode — escape all user-sourced and scraped strings before interpolating:
  ```python
  from html import escape
  client.send_message(f"✅ Added: <b>{escape(keyword)}</b>")
  ```
- This applies to: command args in commands.py, item titles in telegram_client.py `send_photo()`, and any label rendered into a message

### SQL injection
- All queries already use `?` parameterized placeholders — keep it that way strictly
- Never construct SQL with f-strings or string concatenation, even for column names

### Error messages sent to Telegram
- Never forward raw exceptions to Telegram — they may contain internal paths, tokens, or SQL details:
  ```python
  # Wrong
  telegram.send_message(f"❗️ Error: {exc}")
  # Right
  logger.error("Critical error", exc_info=True)   # stack trace stays in logs
  telegram.send_message("❗️ An error occurred. Check logs for details.")
  ```

### Scraped content validation
- Validate extracted URLs before storing — reject anything not on `buyee.jp`:
  ```python
  from urllib.parse import urlparse
  parsed = urlparse(url)
  if parsed.scheme not in ("http", "https") or not parsed.netloc.endswith("buyee.jp"):
      logger.warning("Rejected URL from unexpected domain: %s", url)
      continue
  ```
- Apply the same domain check to image URLs before passing to `send_photo()`

### Telegram bot access control
- `check_commands()` already validates `chat_id` against `CHAT_ID` — never remove that check
- Log unauthorized attempts at WARNING level (but not more than once per unique chat_id to avoid log spam)
- Telegram rate limit: max ~30 msgs/sec; add `time.sleep(0.5)` between sends when notifying on many items

### Dependency security
- Pin exact versions in `requirements.txt` (`pip freeze > requirements.txt` after updating)
- Run `pip-audit` before each release to check for known CVEs:
  ```bash
  pip install pip-audit && pip-audit
  ```
- `googletrans` is unmaintained — consider replacing with `deep-translator` if translation breaks

### Docker security
- Run as a non-root user — add to Dockerfile:
  ```dockerfile
  RUN useradd -m -u 1000 appuser
  USER appuser
  ```
- The dev compose mounts `.:/app` (fine for local dev); production should mount only `config.ini` read-only and a separate named volume for the DB file
- Never `COPY key.env` into the image

### Logging security
- Never log `bot_token`, `chat_id`, or raw Telegram API response bodies (they include the token in the URL)
- Log `response.status_code`, not `response.text`, for Telegram errors
- Add `RotatingFileHandler` if writing logs to disk (prevents unbounded disk growth):
  ```python
  from logging.handlers import RotatingFileHandler
  handler = RotatingFileHandler("bot.log", maxBytes=10_000_000, backupCount=5)
  ```

### DB path safety
- Resolve and validate the DB path in `config.py` to prevent path traversal from a tampered `config.ini`:
  ```python
  db_file = (base_dir / db_file_name).resolve()
  if not str(db_file).startswith(str(base_dir.resolve())):
      raise ValueError("DB file path escapes base directory")
  ```

---

## Anti-Patterns to Avoid

- **Don't open new DB connections** inside scraper.py or commands.py — accept `conn` as a parameter
- **Don't hardcode delays** — read from Settings
- **Don't add `print()`** — use the loggers
- **Don't catch bare `Exception`** unless you immediately log and re-raise or have a clear reason
- **Don't send Telegram messages in a tight loop** — respect the `KEYWORD_BATCH_DELAY` between sends
- **Don't use `schedule` library** — the bot already uses a manual sleep loop; mixing schedulers adds confusion
- **Don't send raw exceptions to Telegram** — log them, send a generic message
- **Don't interpolate user input into Telegram HTML** without `html.escape()` first
- **Don't construct SQL with f-strings** — use `?` placeholders always
