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
    ok = _send("sendMessage", {"text": text[:4000], "parse_mode": "HTML",
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
        ok = _send("sendDocument", {"caption": caption[:1000], "parse_mode": "HTML"}, files=files)
    if ok:
        print(f"[TG] Audit Document delivered: {os.path.basename(file_path)}")
    return ok


def fmt_entry(pos: dict) -> str:
    return (
        f"🚀 <b>NEW BREAKOUT ENTRY</b>\n"
        f"Ticker: <code>{pos['ticker']}</code>\n"
        f"Entry Price: <code>{CURRENCY}{pos['entry_price']:.2f}</code>\n"
        f"Hard Stop Loss: <code>{CURRENCY}{pos['current_sl']:.2f}</code> (-4.5%)\n"
        f"Qty: <code>{pos['qty']}</code> | Invested: <code>{CURRENCY}{pos['invested']:.2f}</code>\n"
        f"Strategy: Bollinger Band Breakout + EMA Trend-Ride"
    )


def fmt_exit(trade: dict) -> str:
    emoji = "🟢" if trade["net_pnl_pct"] > 0 else "🔴"
    return (
        f"{emoji} <b>TRADE CLOSED ({trade['exit_reason']})</b>\n"
        f"Ticker: <code>{trade['ticker']}</code>\n"
        f"Entry: <code>{CURRENCY}{trade['entry_price']:.2f}</code> -&gt; Exit: <code>{CURRENCY}{trade['exit_price']:.2f}</code>\n"
        f"Net P&amp;L: <b>{trade['net_pnl_pct']:+.2f}%</b> ({CURRENCY}{trade['pnl_amount']:+.2f})\n"
        f"Holding Duration: <code>{trade['days_held']}</code> days"
    )


def fmt_summary(scanned_count: int, signals_found: int, open_count: int,
                closed_count: int, cash: float, realized_pnl: float) -> str:
    return (
        f"📊 <b>RUN SUMMARY - AUDIT STATUS</b>\n"
        f"Stocks Scanned: <code>{scanned_count}</code>\n"
        f"Breakouts Found: <code>{signals_found}</code>\n"
        f"Active Positions: <code>{open_count}</code>\n"
        f"Trades Closed Today: <code>{closed_count}</code>\n"
        f"Cash Available: <code>{CURRENCY}{cash:,.2f}</code>\n"
        f"Total Realized P&amp;L: <code>{CURRENCY}{realized_pnl:+,.2f}</code>\n"
        f"📁 <i>Attached: Detailed 7-sheet trade_log.xlsx</i>"
    )

