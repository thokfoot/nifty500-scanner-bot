"""Shim: legacy imports keep working; real code in telegram_notifier.py."""
from telegram_notifier import (  # noqa: F401
    send_message, send_document, send_msg, send_doc,
    fmt_new_signal, fmt_order_placed, fmt_filled, fmt_no_fill,
    fmt_t1_hit, fmt_t2_hit, fmt_stop_loss, fmt_be_exit, fmt_time_exit,
    fmt_closed_trade, fmt_summary,
)
