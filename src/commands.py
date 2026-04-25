import datetime
import sqlite3

from src.database import (
    add_keyword,
    fetch_notification_counts,
    load_keywords_from_db,
    remove_keyword,
    resolve_keyword_by_label,
)
from src.logging_setup import info_logger

_PERIOD_LABELS = {
    "24h": ("últimas 24h", 1),
    "3d": ("últimos 3 dias", 3),
    "7d": ("últimos 7 dias", 7),
    "30d": ("últimos 30 dias", 30),
}
_DEFAULT_PERIOD = "24h"


def cmd_list_keywords(conn: sqlite3.Connection, send_message) -> None:
    kws = load_keywords_from_db(conn)
    if not kws:
        send_message("Nenhuma keyword cadastrada.\n\nUse /addkeyword &lt;keyword&gt; = &lt;label&gt;")
        return
    lines = ["📋 <b>Keywords ativas:</b>"]
    for kw, label in kws.items():
        lines.append(f"• {kw} = {label}")
    send_message("\n".join(lines))


def cmd_add_keyword(conn: sqlite3.Connection, args: str, send_message) -> None:
    if "=" in args:
        parts = args.split("=", 1)
        keyword = parts[0].strip()
        label = parts[1].strip()
    else:
        keyword = args.strip()
        label = keyword
    if not keyword:
        send_message("❌ Uso: /addkeyword &lt;keyword&gt; = &lt;label&gt;")
        return
    add_keyword(conn, keyword, label)
    send_message(f"✅ Keyword adicionada: <b>{keyword}</b> = {label}")
    info_logger.info(f"Keyword added via Telegram: {keyword} = {label}")


def cmd_remove_keyword(conn: sqlite3.Connection, keyword: str, send_message) -> None:
    if not keyword:
        send_message("❌ Uso: /removekeyword &lt;keyword&gt;")
        return
    deleted = remove_keyword(conn, keyword)
    if deleted:
        send_message(f"🗑 Keyword removida: <b>{keyword}</b>")
        info_logger.info(f"Keyword removed via Telegram: {keyword}")
    else:
        send_message(f"❌ Keyword não encontrada: <b>{keyword}</b>")


def cmd_summary(conn: sqlite3.Connection, args: str, send_message) -> None:
    parts = args.strip().split()
    period_key = _DEFAULT_PERIOD
    label_filter = None

    if parts and parts[-1].lower() in _PERIOD_LABELS:
        period_key = parts[-1].lower()
        parts = parts[:-1]

    if parts:
        label_filter = " ".join(parts)

    period_label, days = _PERIOD_LABELS[period_key]
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    keyword_filter = None
    if label_filter:
        row = resolve_keyword_by_label(conn, label_filter)
        if not row:
            send_message(
                f"❌ Keyword com label <b>{label_filter}</b> não encontrada.\nUse /keywords para ver as ativas."
            )
            return
        keyword_filter = row[0]

    rows = fetch_notification_counts(conn, since, keyword=keyword_filter)

    title = f"📊 <b>Summary — {period_label}</b>"
    if label_filter:
        title += f" — {label_filter}"

    if not rows:
        send_message(f"{title}\n\nNenhum item encontrado nesse período.")
        return

    kw_labels = load_keywords_from_db(conn)
    lines = [title, ""]
    total = 0
    for kw, count in sorted(rows, key=lambda row: row[1], reverse=True):
        lbl = kw_labels.get(kw, kw)
        lines.append(f"• <b>{lbl}</b>: {count} item{'s' if count != 1 else ''}")
        total += count

    if not label_filter and len(rows) > 1:
        lines.append(f"\nTotal: {total} items")

    send_message("\n".join(lines))


def cmd_help(send_message) -> None:
    send_message(
        "🤖 <b>Comandos disponíveis</b>\n"
        "\n"
        "<b>Keywords</b>\n"
        "/keywords — lista todas as keywords ativas\n"
        "/addkeyword &lt;keyword&gt; = &lt;label&gt; — adiciona uma keyword\n"
        "/removekeyword &lt;keyword&gt; — remove uma keyword\n"
        "\n"
        "<b>Summary</b>\n"
        "/summary — todos os keywords, últimas 24h\n"
        "/summary &lt;período&gt; — todos os keywords no período\n"
        "/summary &lt;label&gt; — keyword específica, últimas 24h\n"
        "/summary &lt;label&gt; &lt;período&gt; — keyword específica no período\n"
        "\n"
        "Períodos: <code>24h</code> · <code>3d</code> · <code>7d</code> · <code>30d</code>"
    )
