# Mercari Telegram Bot

This bot monitors Mercari Japan for new listings based on your specified keywords and sends notifications to a Telegram chat. It also tracks seen items to avoid duplicate notifications and provides daily summaries.

## Features
- Monitors Mercari Japan for new item listings.
- Sends Telegram messages with item details, including title, price, image, and URL.
- Tracks seen items to prevent duplicate notifications.
- Supports price conversion from USD to JPY.
- Provides daily summaries of new items found.
- Configurable via environment variables and a dedicated configuration file.

## Prerequisites
Before running the bot, ensure you have the following installed:
- **Python 3.8+**
- **Google Chrome Browser** (Selenium requires a Chrome installation)

## Installation
1.  **Create a project directory:**
    ```bash
    mkdir mercari_bot
    cd mercari_bot
    ```
2.  **Save the script:**
    Save the provided `mercari_telegram_bot_config_improved.py` file into this directory.

3.  **Install Python dependencies:**
    It's highly recommended to use a virtual environment.
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```
    The `requirements.txt` file lists all the Python packages your script needs to run. The `pip install -r requirements.txt` command tells `pip` to read this file and install all the listed dependencies automatically.

## Configuration

### 1. `requirements.txt`
Create a file named `requirements.txt` in your project directory with the following content:

```
python-dotenv
requests
schedule
selenium
webdriver-manager
```

### 2. `key.env`
Create a file named `key.env` in your project directory. This file will store your Telegram bot token and chat ID. Replace `YOUR_BOT_TOKEN` and `YOUR_CHAT_ID` with your actual values.

```
BOT_TOKEN=YOUR_BOT_TOKEN
CHAT_ID=YOUR_CHAT_ID
```

*   **How to get `BOT_TOKEN`:**
    *   Talk to BotFather on Telegram (`@BotFather`).
    *   Send `/newbot` and follow the instructions to create a new bot.
    *   BotFather will give you a token (e.g., `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`).

*   **How to get `CHAT_ID`:**
    *   Start a chat with your newly created bot.
    *   Go to `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` in your web browser (replace `<YOUR_BOT_TOKEN>` with your bot's token).
    *   Look for the `chat` object and find the `id` field. This is your `CHAT_ID`.

### 3. `config.ini`
Create a file named `config.ini` in your project directory with the following content. This file centralizes various bot settings.

```ini
[BOT_SETTINGS]
MAX_SEEN_ITEMS = 1000
SEEN_FILE = seen_items.json

[SCHEDULE]
DAILY_SUMMARY_TIME = 12:30

[DELAYS]
KEYWORD_BATCH_DELAY = 10
FULL_CYCLE_DELAY = 60

[KEYWORDS]
Nintendo Switch = Nintendo Switch
Pokemon Cards = Pokemon Cards
Japanese Books = Japanese Books
```

*   **`MAX_SEEN_ITEMS`**: The maximum number of seen items to keep track of.
*   **`SEEN_FILE`**: The name of the JSON file used to store seen items.
*   **`KEYWORDS`**: This section contains the keywords for Mercari searches. Each entry should be in the format `Original Keyword = English Translation`. The bot will use the original keyword for searching Mercari and the English translation for Telegram messages. For example:
    ```ini
    [KEYWORDS]
    Nintendo Switch = Nintendo Switch
    ポケモンカード = Pokemon Cards
    日本の本 = Japanese Books
    ```
    If the original keyword is already in English, you can simply repeat it as the translation.
*   **`DAILY_SUMMARY_TIME`**: The time (in HH:MM format) when the daily summary will be sent.
*   **`KEYWORD_BATCH_DELAY`**: The delay in seconds between processing different keywords.
*   **`FULL_CYCLE_DELAY`**: The delay in seconds after a full cycle of all keywords is completed.


## Running the Bot

1.  **Activate your virtual environment (if you created one):**
    ```bash
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

2.  **Run the bot:**
    ```bash
    python mercari_telegram_bot_config_improved.py
    ```

The bot will start fetching items and sending notifications to your Telegram chat. It is designed to run continuously.

## Important Notes
-   The bot uses Selenium to scrape Mercari, which requires a Chrome browser installation on the machine where the bot is running.
-   The `seen_items.json` file will be created automatically to store information about items that have already been seen. Do not delete this file unless you want to receive notifications for previously seen items again.
-   The exchange rate is fetched from `open.er-api.com`. If this API is unavailable, a fallback rate of 145.0 JPY to 1 USD will be used.

## Troubleshooting
-   **`WebDriver could not initialize`**: Ensure Google Chrome is installed and accessible by the system. Also, check your internet connection.
-   **`Missing Telegram credentials`**: Double-check your `key.env` file for correct `BOT_TOKEN` and `CHAT_ID` values.
-   **`Configuration file not found`**: Ensure `config.ini` is present in the same directory as the script.
-   **No items found/sent**: Verify your keywords in the `[KEYWORDS]` section of `config.ini` are correct and that there are active listings on Mercari for those keywords.
-   **`JSONDecodeError`**: If `seen_items.json` becomes corrupted, delete it. The bot will create a new, empty one on the next run.
