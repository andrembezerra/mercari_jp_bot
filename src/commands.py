import datetime
import sqlite3
from html import escape

from src.database import (
    add_keyword,
    count_active_suppressions,
    disable_keyword,
    enable_keyword,
    fetch_notification_counts,
    find_notification_by_message_id,
    get_state,
    is_paused,
    list_active_suppressions,
    load_keywords_from_db,
    load_skipped_keywords,
    remove_keyword,
    resolve_keyword_by_label,
    resolve_keyword_by_label_or_keyword,
    set_state,
    suppress_item,
    unsuppress_item,
)
from src.logging_setup import info_logger
from src.types import CommandContext

_PERIOD_LABELS = {
    "24h": ("últimas 24h", 1),
    "3d": ("últimos 3 dias", 3),
    "7d": ("últimos 7 dias", 7),
    "30d": ("últimos 30 dias", 30),
}
_DEFAULT_PERIOD = "24h"


class RestartRequested(Exception):
    pass


def cmd_list_keywords(conn: sqlite3.Connection, send_message) -> None:
    kws = load_keywords_from_db(conn, include_disabled=True)
    if not kws:
        send_message("Nenhuma keyword cadastrada.\n\nUse /addkeyword &lt;keyword&gt; = &lt;label&gt;")
        return
    skipped = {kw for kw, _, _ in load_skipped_keywords(conn)}
    lines = ["📋 <b>Keywords ativas:</b>"]
    for kw, label in kws.items():
        marker = " 💤" if kw in skipped else ""
        lines.append(f"• {escape(kw)} = {escape(label)}{marker}")
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
    send_message(f"✅ Keyword adicionada: <b>{escape(keyword)}</b> = {escape(label)}")
    info_logger.info(f"Keyword added via Telegram: {keyword} = {label}")


def cmd_remove_keyword(conn: sqlite3.Connection, keyword: str, send_message) -> None:
    if not keyword:
        send_message("❌ Uso: /removekeyword &lt;keyword&gt;")
        return
    deleted = remove_keyword(conn, keyword)
    if deleted:
        send_message(f"🗑 Keyword removida: <b>{escape(keyword)}</b>")
        info_logger.info(f"Keyword removed via Telegram: {keyword}")
    else:
        send_message(f"❌ Keyword não encontrada: <b>{escape(keyword)}</b>")


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
                f"❌ Keyword com label <b>{escape(label_filter)}</b> não encontrada.\nUse /keywords para ver as ativas."
            )
            return
        keyword_filter = row[0]

    rows = fetch_notification_counts(conn, since, keyword=keyword_filter)

    title = f"📊 <b>Summary — {period_label}</b>"
    if label_filter:
        title += f" — {escape(label_filter)}"

    if not rows:
        send_message(f"{title}\n\nNenhum item encontrado nesse período.")
        return

    kw_labels = load_keywords_from_db(conn, include_disabled=True)
    lines = [title, ""]
    total = 0
    for kw, count in sorted(rows, key=lambda row: row[1], reverse=True):
        lbl = kw_labels.get(kw, kw)
        lines.append(f"• <b>{escape(lbl)}</b>: {count} item{'s' if count != 1 else ''}")
        total += count

    if not label_filter and len(rows) > 1:
        lines.append(f"\nTotal: {total} items")

    send_message("\n".join(lines))


def _suppress_via_reply(
    conn: sqlite3.Connection,
    ctx: CommandContext,
    send_message,
    reason: str,
    success_message: str,
) -> None:
    if not ctx.reply_to_message_id:
        send_message(
            "❌ Responda a uma notificação de item do bot para usar este comando."
        )
        return
    row = find_notification_by_message_id(conn, ctx.reply_to_message_id)
    if not row:
        send_message(
            "❌ Não consegui identificar o item desta notificação. Use o comando "
            "respondendo diretamente à mensagem da foto enviada pelo bot."
        )
        return
    item_id, keyword, title, url = row
    suppress_item(conn, item_id, reason, title, url, keyword)
    info_logger.info(f"Item suppressed via /{reason}: {item_id}")
    title_snippet = (title or "")[:60]
    send_message(f"{success_message} <b>{escape(title_snippet)}</b>")


def cmd_hide(conn: sqlite3.Connection, ctx: CommandContext, send_message) -> None:
    _suppress_via_reply(conn, ctx, send_message, "hide", "🚫 Item ocultado:")


def cmd_wrong(conn: sqlite3.Connection, ctx: CommandContext, send_message) -> None:
    _suppress_via_reply(conn, ctx, send_message, "wrong", "❌ Item marcado como errado:")


def cmd_unblock(conn: sqlite3.Connection, ctx: CommandContext, send_message) -> None:
    if not ctx.reply_to_message_id:
        send_message("❌ Responda a uma notificação de item do bot para desbloquear.")
        return
    row = find_notification_by_message_id(conn, ctx.reply_to_message_id)
    if not row:
        send_message("❌ Não consegui identificar o item desta notificação.")
        return
    item_id, _keyword, title, _url = row
    affected = unsuppress_item(conn, item_id)
    if affected:
        info_logger.info(f"Item unsuppressed via /unblock: {item_id}")
        title_snippet = (title or "")[:60]
        send_message(f"✅ Item desbloqueado: <b>{escape(title_snippet)}</b>")
    else:
        send_message("ℹ️ Esse item não estava bloqueado.")


def cmd_blocked(conn: sqlite3.Connection, send_message) -> None:
    rows = list_active_suppressions(conn, limit=20)
    if not rows:
        send_message("✅ Nenhum item bloqueado no momento.")
        return
    lines = ["🚫 <b>Itens bloqueados (mais recentes):</b>"]
    for item_id, reason, title, _url, keyword, created_at in rows:
        title_snippet = (title or "(sem título)")[:50]
        keyword_part = f" · {escape(keyword)}" if keyword else ""
        lines.append(
            f"• [{escape(reason)}] <b>{escape(title_snippet)}</b>{keyword_part} · {escape(created_at)}"
        )
    send_message("\n".join(lines))


def cmd_pause(conn: sqlite3.Connection, send_message) -> None:
    set_state(conn, "paused", "1")
    info_logger.info("Bot paused via /pause")
    send_message("⏸ Bot pausado. Notificações suspensas até /resume.")


def cmd_resume(conn: sqlite3.Connection, send_message) -> None:
    set_state(conn, "paused", "0")
    info_logger.info("Bot resumed via /resume")
    send_message("▶️ Bot retomado.")


def cmd_restart(send_message) -> None:
    info_logger.info("Bot restart requested via /restart")
    send_message("🔄 Reiniciando o bot. O container deve voltar em alguns segundos.")
    raise RestartRequested()


def cmd_status(conn: sqlite3.Connection, send_message) -> None:
    paused = is_paused(conn)
    active_kw = len(load_keywords_from_db(conn))
    skipped_kw = len(load_skipped_keywords(conn))
    blocked = count_active_suppressions(conn)
    last_cycle = get_state(conn, "last_cycle_at") or "—"
    last_cycle_summary = get_state(conn, "last_cycle_summary") or ""
    last_error_at = get_state(conn, "last_error_at")
    last_error_summary = get_state(conn, "last_error_summary")

    lines = [
        "🩺 <b>Status</b>",
        f"• Estado: {'⏸ pausado' if paused else '▶️ rodando'}",
        f"• Keywords ativas: {active_kw}",
        f"• Keywords ignoradas: {skipped_kw}",
        f"• Itens bloqueados: {blocked}",
        f"• Último ciclo: {escape(last_cycle)}"
        + (f" ({escape(last_cycle_summary)})" if last_cycle_summary else ""),
    ]
    if last_error_at:
        lines.append(
            f"• Último erro: {escape(last_error_at)} — {escape(last_error_summary or 'erro')}"
        )
    send_message("\n".join(lines))


def cmd_skip_keyword(conn: sqlite3.Connection, value: str, send_message) -> None:
    if not value:
        send_message("❌ Uso: /skipkeyword &lt;label ou keyword&gt;")
        return
    row = resolve_keyword_by_label_or_keyword(conn, value)
    if not row:
        send_message(f"❌ Keyword não encontrada: <b>{escape(value)}</b>")
        return
    keyword, label, disabled_at = row
    if disabled_at:
        send_message(f"ℹ️ Keyword já estava ignorada: <b>{escape(label)}</b>")
        return
    disable_keyword(conn, keyword)
    info_logger.info(f"Keyword skipped via /skipkeyword: {keyword}")
    send_message(f"💤 Keyword ignorada: <b>{escape(label)}</b>")


def cmd_enable_keyword(conn: sqlite3.Connection, value: str, send_message) -> None:
    if not value:
        send_message("❌ Uso: /enablekeyword &lt;label ou keyword&gt;")
        return
    row = resolve_keyword_by_label_or_keyword(conn, value)
    if not row:
        send_message(f"❌ Keyword não encontrada: <b>{escape(value)}</b>")
        return
    keyword, label, disabled_at = row
    if not disabled_at:
        send_message(f"ℹ️ Keyword já estava ativa: <b>{escape(label)}</b>")
        return
    enable_keyword(conn, keyword)
    info_logger.info(f"Keyword re-enabled via /enablekeyword: {keyword}")
    send_message(f"✅ Keyword reativada: <b>{escape(label)}</b>")


def cmd_skipped(conn: sqlite3.Connection, send_message) -> None:
    rows = load_skipped_keywords(conn)
    if not rows:
        send_message("✅ Nenhuma keyword ignorada.")
        return
    lines = ["💤 <b>Keywords ignoradas:</b>"]
    for keyword, label, disabled_at in rows:
        lines.append(
            f"• <b>{escape(label)}</b> ({escape(keyword)}) · desde {escape(disabled_at)}"
        )
    send_message("\n".join(lines))


def cmd_image_search(conn: sqlite3.Connection, ctx: CommandContext, client) -> None:
    from src.image_search import run_image_search

    run_image_search(conn, ctx, client)


def cmd_help(send_message) -> None:
    send_message(
        "🤖 <b>Comandos disponíveis</b>\n"
        "\n"
        "<b>Keywords</b>\n"
        "/keywords — lista todas as keywords cadastradas\n"
        "/addkeyword &lt;keyword&gt; = &lt;label&gt; — adiciona uma keyword\n"
        "/removekeyword &lt;keyword&gt; — remove uma keyword\n"
        "/skipkeyword &lt;label ou keyword&gt; — ignora temporariamente\n"
        "/enablekeyword &lt;label ou keyword&gt; — reativa\n"
        "/skipped — lista keywords ignoradas\n"
        "\n"
        "<b>Moderação de itens</b> (responda à notificação)\n"
        "/hide — oculta o item permanentemente\n"
        "/wrong — marca como falso match\n"
        "/unblock — remove o bloqueio\n"
        "/blocked — lista itens bloqueados\n"
        "\n"
        "<b>Runtime</b>\n"
        "/status — estado atual do bot\n"
        "/pause — pausa as notificações\n"
        "/resume — retoma o bot\n"
        "/restart — reinicia o bot/container\n"
        "\n"
        "<b>Busca</b>\n"
        "/summary — resumo de notificações\n"
        "/imagesearch — responda a uma foto para buscar via OCR\n"
        "\n"
        "Períodos do summary: <code>24h</code> · <code>3d</code> · <code>7d</code> · <code>30d</code>"
    )
