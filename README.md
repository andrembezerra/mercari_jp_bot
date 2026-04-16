# Mercari Telegram Bot

This bot monitors Mercari Japan for new listings based on your specified keywords and sends notifications to a Telegram chat. It tracks seen items in a SQLite database to avoid duplicate notifications and supports on-demand summaries via Telegram commands.

## Features
- Monitors Mercari Japan for new item listings.
- Sends Telegram messages with item details, including title, price, image, and URL.
- Tracks seen items in a **SQLite database** (WAL mode) to prevent duplicate notifications — crash-safe and persistent.
- Manages keywords at runtime via **Telegram commands** — no need to edit config files.
- On-demand `/summary` command with filtering by period and keyword.
- Supports price conversion from JPY to USD.
- Configurable via environment variables and `config.ini`.

## Prerequisites
- **Python 3.8+**

## Installation

1. **Clone or download the repository.**

2. **Install Python dependencies** (virtual environment recommended):
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

## Configuration

### `key.env`
Create a `key.env` file with your Telegram credentials:

```
BOT_TOKEN=YOUR_BOT_TOKEN
CHAT_ID=YOUR_CHAT_ID
```

- **`BOT_TOKEN`**: Create a bot via `@BotFather` on Telegram (`/newbot`).
- **`CHAT_ID`**: Start a chat with your bot, then visit `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` and find the `id` field inside the `chat` object.

### `config.ini`
Create a `config.ini` file. The `[KEYWORDS]` section is optional — keywords can be managed entirely via Telegram commands.

```ini
[BOT_SETTINGS]
MAX_SEEN_ITEMS = 1000

[DELAYS]
KEYWORD_BATCH_DELAY = 10
FULL_CYCLE_DELAY = 60

[KEYWORDS]
Nintendo Switch = Nintendo Switch
ポケモンカード = Pokemon Cards
```

- **`KEYWORD_BATCH_DELAY`**: Seconds between processing each keyword.
- **`FULL_CYCLE_DELAY`**: Seconds to wait after completing a full cycle.
- **`[KEYWORDS]`**: Optional. Each line is `Search Term = English Label`. On first boot, keywords are imported from here into the SQLite database. After that, use Telegram commands to manage them.

## Running the Bot

```bash
source venv/bin/activate
python mercari_telegram_bot_config_improved.py
```

The bot runs continuously, polling Mercari and responding to Telegram commands each cycle.

## Telegram Commands

| Command | Description |
|---|---|
| `/help` | List all available commands |
| `/keywords` | List all active keywords |
| `/addkeyword <term> = <label>` | Add or update a keyword |
| `/removekeyword <term>` | Remove a keyword |
| `/summary` | Summary of all notifications in the last 24h |
| `/summary 3d` / `7d` / `30d` | Summary for the chosen period |
| `/summary <label>` | Summary for a specific keyword label (last 24h) |
| `/summary <label> 7d` | Summary for a specific keyword label and period |

Commands are only accepted from the authorised `CHAT_ID`.

## Data Storage

All persistent state is kept in **`seen_items.db`** (SQLite):

- `seen_items` — items already notified, preventing duplicates.
- `keywords` — active search keywords and their labels.
- `notifications` — log of every notification sent (used by `/summary`).

On first boot, if a legacy `seen_items.json` file exists, it is automatically migrated to the database.

## Important Notes
- The `seen_items.db` file is created automatically. Do not delete it unless you want to receive duplicate notifications for previously seen items.
- The exchange rate is fetched from `open.er-api.com`. A fallback rate of 145.0 JPY/USD is used if the API is unavailable.
- The `[SCHEDULE]` / `DAILY_SUMMARY_TIME` config options have been removed. Use `/summary` instead.

## Troubleshooting

- **`Missing Telegram credentials`**: Check your `key.env` file for correct `BOT_TOKEN` and `CHAT_ID`.
- **`Configuration file not found`**: Ensure `config.ini` is in the same directory as the script.
- **No items found/sent**: Verify your keywords are correct (use `/keywords` to list them) and that there are active listings on Mercari.
- **No keywords loaded**: If the bot starts with no keywords, it will send a warning via Telegram. Use `/addkeyword` to add at least one.
