"""
Telegram Notifier - sends messages and documents
"""
import os, time, requests
from config import TG_TOKEN, TG_CHAT_ID

def send_msg(text: str) -> bool:
    """Send a Telegram message. Returns True on success."""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] Missing credentials")
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for attempt in range(3):
        try:
            r = requests.post(url, data={
                "chat_id": TG_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            }, timeout=15)
            resp = r.json() if r.text else {}
            if r.status_code == 200 and resp.get("ok"):
                print(f"[TG] Sent OK ({len(text)} chars)")
                return True
            else:
                err = resp.get("description", r.text[:200])
                print(f"[TG] Attempt {attempt+1} failed: {err}")
                time.sleep(2)
        except Exception as e:
            print(f"[TG] Attempt {attempt+1} error: {e}")
            time.sleep(2)
    print("[TG] All 3 attempts failed")
    return False

def send_doc(file_path: str, caption: str = "") -> bool:
    """Send a file as document."""
    if not TG_TOKEN or not TG_CHAT_ID:
        return False
    if not os.path.exists(file_path):
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendDocument"
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                mime = "text/html" if file_path.endswith(".html") else "application/octet-stream"
                files = {"document": (os.path.basename(file_path), f, mime)}
                data = {"chat_id": TG_CHAT_ID, "caption": caption}
                r = requests.post(url, data=data, files=files, timeout=30)
            resp = r.json() if r.text else {}
            if r.status_code == 200 and resp.get("ok"):
                print(f"[TG] Document sent: {os.path.basename(file_path)}")
                return True
            else:
                print(f"[TG] Doc attempt {attempt+1} failed: {resp.get('description','')}")
                time.sleep(2)
        except Exception as e:
            print(f"[TG] Doc attempt {attempt+1} error: {e}")
            time.sleep(2)
    return False
