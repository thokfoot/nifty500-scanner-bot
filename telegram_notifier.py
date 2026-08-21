"""
Telegram Notifier - broker-type notifications.

Every notable event goes to Telegram:
  NEW SIGNAL / ORDER PLACED / FILLED / NO_FILL / T1 HIT / T2 HIT /
  STOP_LOSS / BE EXIT / TIME_EXIT / DAY SUMMARY
Plus Excel document delivery. All sends retry 3x.
"""
import os, time, requests
from config import TG_TOKEN, TG_CHAT_ID

MAX_RETRIES = 3


def _send(method: str, payload: dict, files: dict = None) -> bool:
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] Missing credentials")
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
        print(f"[TG] Sent OK ({len(text)} chars)")
    return ok


def send_document(file_path: str, caption: str = "") -> bool:
    if not TG_TOKEN or not TG_CHAT_ID or not os.path.exists(file_path):
        return False
    with open(file_path, "rb") as f:
        files = {"document": (os.path.basename(file_path), f,
                              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        ok = _send("sendDocument", {"caption": caption[:1000]}, files=files)
    if ok:
        print(f"[TG] Document sent: {os.path.basename(file_path)}")
    return ok


# Backward-compatible aliases
send_msg = send_message
send_doc = send_document


# ── Event formatters ────────────────────────────────────────────────
def fmt_new_signal(sig: dict) -> str:
    d = sig.get("details", {})
    return (f"🔍 *NEW SIGNAL: {sig['ticker']}*\n"
            f"Entry `{sig['entry_price']}` | SL `{sig['sl']}`\n"
            f"T1 `{sig['t1']}` | T2 `{sig['t2']}`\n"
            f"RSI {d.get('rsi', '?')} | Range {d.get('daily_range_pct', '?')}% | "
            f"CP {d.get('close_position', '?')}")


def fmt_order_placed(sig: dict, qty: int, trade_date: str) -> str:
    return (f"📝 *ORDER PLACED: {sig['ticker']}*\n"
            f"BUY LIMIT `{sig['entry_price']}` x{qty} (Rs 10K)\n"
            f"SL-M `{sig['sl']}` working\n"
            f"_Pending fill on {trade_date}_")


def fmt_filled(pos: dict) -> str:
    return (f"✅ *FILLED: {pos['ticker']}* @ `{pos['entry_price']}` x{pos['qty']}\n"
            f"SL `{pos['sl_eff']}` | T1 `{pos['t1']}` T2 `{pos['t2']}`\n"
            f"MIS | {pos.get('trade_date', '')}")


def fmt_no_fill(ticker: str, entry, day_low: float, info: str = "") -> str:
    reason = "gap-up open" if "gap" in info else "not touched"
    return (f"❌ *NO_FILL: {ticker}*\n"
            f"Entry `{entry}` {reason}\n"
            f"Day Low `{day_low}`")


def fmt_t1_hit(pos: dict) -> str:
    banked = pos.get("pnl_banked_pct", 0.0)
    return (f"🎯 *T1 HIT: {pos['ticker']}*\n"
            f"T1 `{pos['t1']}` booked 50% (+{banked:.2f}%)\n"
            f"SL shifted to BE `{pos['sl_eff']}`\n"
            f"Remaining 50% running for T2 `{pos['t2']}`")


def fmt_t2_hit(trade: dict) -> str:
    return (f"🎯 *T2 HIT: {trade['ticker']}*\n"
            f"T2 `{trade['exit_price']}` booked\n"
            f"Net *{trade['net_pnl_pct']:+.2f}%* "
            f"(Rs {trade['entry_price'] * trade['qty'] * trade['net_pnl_pct'] / 100:+,.0f})")


def fmt_stop_loss(trade: dict) -> str:
    rs = trade["entry_price"] * trade["qty"] * trade["net_pnl_pct"] / 100
    low = f" | Low `{trade.get('exit_candle_low')}`" if trade.get("exit_candle_low") else ""
    return (f"🛑 *STOP_LOSS: {trade['ticker']}*\n"
            f"SL `{trade['exit_price']}` hit{low}\n"
            f"Loss *{trade['net_pnl_pct']:+.2f}%* net (Rs {rs:+,.0f})")


def fmt_be_exit(trade: dict) -> str:
    rs = trade["entry_price"] * trade["qty"] * trade["net_pnl_pct"] / 100
    return (f"⚖️ *BE EXIT: {trade['ticker']}*\n"
            f"T1 was booked, rest exited @ BE `{trade['exit_price']}`\n"
            f"Net *{trade['net_pnl_pct']:+.2f}%* (Rs {rs:+,.0f})")


def fmt_time_exit(trade: dict) -> str:
    rs = trade["entry_price"] * trade["qty"] * trade["net_pnl_pct"] / 100
    return (f"⏰ *TIME_EXIT: {trade['ticker']}*\n"
            f"@ `{trade['exit_price']}` | Net *{trade['net_pnl_pct']:+.2f}%* (Rs {rs:+,.0f})")


def fmt_closed_trade(trade: dict) -> str:
    """Route to the right formatter by exit_reason."""
    r = trade.get("exit_reason", "")
    if r == "STOP_LOSS":
        return fmt_stop_loss(trade)
    if r == "T2_HIT":
        return fmt_t2_hit(trade)
    if r == "BE_SL":
        return fmt_be_exit(trade)
    return fmt_time_exit(trade)


def fmt_summary(date_str: str, scanned: int, signals_found: int, executed: int,
                closed: int, pnl_rs: float, win_rate: float,
                pending: list, holdings: list) -> str:
    lines = [f"📊 *DAY SUMMARY* _{date_str}_",
             f"Scanned: {scanned} | Signals: {signals_found}",
             f"Executed: {executed} | Closed: {closed}",
             f"P&L today: Rs *{pnl_rs:+,.0f}* | WinRate: {win_rate}%"]
    if holdings:
        hs = ", ".join(f"{h['ticker']}@{h['entry_price']}" for h in holdings)
        lines.append(f"🟢 Open: {hs}")
    if pending:
        lines.append(f"📋 Pending: {', '.join(pending)}")
    if not holdings and not pending:
        lines.append("Flat - no open exposure.")
    return "\n".join(lines)
