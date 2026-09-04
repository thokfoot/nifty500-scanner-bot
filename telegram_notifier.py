"""
Telegram Notifier - Volatility Expansion & Trend-Ride Bot
Sends real-time event alerts and attaches the multi-sheet trade_log.xlsx audit workbook.
"""
import os, time, requests
from config import TG_TOKEN, TG_CHAT_ID, CURRENCY

MAX_RETRIES = 3


def _send(method: str, payload: dict, files: dict = None) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] Missing credentials, skipping Telegram dispatch")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/{method}"
    payload = dict(payload, chat_id=TG_CHAT_ID)
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(url, data=payload, files=files, timeout=30)
            resp = r.json() if r.text else {}
            if r.status_code == 200 and resp.get("ok"):
                return True
            print(f"[TG] {method} attempt {attempt+1} failed: {resp.get('description', r.text[:120])}")
        except Exception as e:
            print(f"[TG] {method} attempt {attempt+1} error: {e}")
        time.sleep(2 * (attempt + 1))
    return False


def send_message(text: str) -> bool:
    ok = _send("sendMessage", {"text": text[:4000], "parse_mode": "Markdown",
                               "disable_web_page_preview": True})
    if ok:
        print(f"[TG] Sent message OK ({len(text)} chars)")
    return ok


def send_document(file_path: str, caption: str = "") -> bool:
    if not TG_TOKEN or not TG_CHAT_ID or not os.path.exists(file_path):
        return False
    with open(file_path, "rb") as f:
        files = {"document": (os.path.basename(file_path), f,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        ok = _send("sendDocument", {"caption": caption[:1000]}, files=files)
    if ok:
        print(f"[TG] Audit Document delivered: {os.path.basename(file_path)}")
    return ok


def fmt_entry(pos: dict) -> str:
    return (
        f"🚀 *NEW BREAKOUT ENTRY*\n"
        f"Ticker: `{pos['ticker']}`\n"
        f"Entry Price: `{CURRENCY}{pos['entry_price']:.2f}`\n"
        f"Hard Stop Loss: `{CURRENCY}{pos['current_sl']:.2f}` (-4.5%)\n"
        f"Qty: `{pos['qty']}` | Invested: `{CURRENCY}{pos['invested']:.2f}`\n"
        f"Strategy: Bollinger Band Breakout + EMA Trend-Ride"
    )


def fmt_exit(trade: dict) -> str:
    emoji = "🟢" if trade["net_pnl_pct"] > 0 else "🔴"
    return (
        f"{emoji} *TRADE CLOSED ({trade['exit_reason']})*\n"
        f"Ticker: `{trade['ticker']}`\n"
        f"Entry: `{CURRENCY}{trade['entry_price']:.2f}` -> Exit: `{CURRENCY}{trade['exit_price']:.2f}`\n"
        f"Net P&L: *{trade['net_pnl_pct']:+.2f}%* ({CURRENCY}{trade['pnl_amount']:+.2f})\n"
        f"Holding Duration: `{trade['days_held']}` days"
    )


def fmt_summary(scanned_count: int, signals_found: int, open_count: int,
                closed_count: int, cash: float, realized_pnl: float) -> str:
    return (
        f"📊 *RUN SUMMARY - AUDIT STATUS*\n"
        f"Stocks Scanned: `{scanned_count}`\n"
        f"Breakouts Found: `{signals_found}`\n"
        f"Active Positions: `{open_count}`\n"
        f"Trades Closed Today: `{closed_count}`\n"
        f"Cash Available: `{CURRENCY}{cash:,.2f}`\n"
        f"Total Realized P&L: `{CURRENCY}{realized_pnl:+,.2f}`\n"
        f"📁 _Attached: Detailed 7-sheet trade_log.xlsx_"
    )
