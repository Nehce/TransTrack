import json
import os
import random
import time
import hashlib
import requests
from urllib.parse import quote

BASE_URL = "https://www.tesla.com/inventory/api/v4/inventory-results"
from dotenv import load_dotenv
load_dotenv()
# =========================
# Telegram config (fill yourself)
# =========================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")   # <-- fill
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")     # <-- fill (your user id or group id)

# =========================
# Human-like polling config
# =========================
POLL_MIN_SEC = 90         # base min interval
POLL_MAX_SEC = 240        # base max interval
RANDOM_JITTER_SEC = 25    # extra jitter
MAX_RETRIES = 4

STATE_FILE = "tesla_seen_state.json"

headers_base = {
    "Accept": "application/json",
    "Accept-Language": "de-DE,de;q=0.9",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.tesla.com/de_DE/inventory/used/m3",
}


def build_query_param(payload: dict) -> str:
    s = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return quote(s, safe="")

def build_payload(
    *,
    years=(2023, 2024, 2025),
    paints=("WHITE", "BLACK", "SILVER"),
    category=("LRAWD",),
    price_min=21000,
    price_max=55000,
    odo_min=0,
    odo_max=117000,
    zip_code="85057",
    lat=48.7750985,
    lng=11.4097351,
    radius_km=200,
    region="BY",
    offset=0,
    count=24,
) -> dict:
    return {
        "query": {
            "model": "m3",
            "condition": "used",
            "options": {
                "CATEGORY": list(category),
                "Year": list(years),
                "PAINT": list(paints),
            },
            "arrangeby": "Price",
            "order": "asc",
            "market": "DE",
            "language": "de",
            "PaymentType": "cash",
            "paymentRange": f"{price_min},{price_max}",
            "Odometer": f"{odo_min},{odo_max}",
            "lng": float(lng),
            "lat": float(lat),
            "zip": str(zip_code),
            "range": int(radius_km),
            "region": str(region),
        },
        "offset": int(offset),
        "count": int(count),
        "outsideOffset": 0,
        "outsideSearch": False,
        "isFalconDeliverySelectionEnabled": False,
        "version": None,
    }

def parse_vehicle(v: dict) -> dict:
    def d10(x):
        return (x or "")[:10]

    adl = v.get("ADL_OPTS", []) or []
    ap = v.get("AUTOPILOT", []) or []

    return {
        "VIN": v.get("VIN"),
        "Price_EUR": v.get("Price"),
        "Paint": ",".join(v.get("PAINT", []) or []),
        "Odometer_km": v.get("Odometer"),
        "ActualRange_km": v.get("ActualRange"),
        "FirstRegistration": d10(v.get("FirstRegistrationDate")),
        "VehicleHistory": v.get("VehicleHistory"),
        "Has_Accident_Record": v.get("VehicleHistory") not in ("CLEAN", None),
        "Wheels": ",".join(v.get("WHEELS", []) or []),
        "Has_Towing": "TOWING" in adl,
        "Interior": ",".join(v.get("INTERIOR", []) or []),
        # keep these if you want later:
        "Has_EAP": "ENHANCED_AUTOPILOT" in ap,
        "Has_FSD": any(x.get("code") == "$APF2" for x in v.get("FlexibleOptionsData", [])),
    }

def fmt_vehicle_msg(p: dict) -> str:
    def yn(x): return "Yes" if x else "No"

    return (
        f"Tesla Used Inventory Update\n"
        f"VIN: {p.get('VIN')}\n"
        f"价格 Price_EUR: {p.get('Price_EUR')} €\n"
        f"颜色 Paint: {p.get('Paint')}\n"
        f"公里数 Odometer_km: {p.get('Odometer_km')} km\n"
        f"续航 ActualRange_km: {p.get('ActualRange_km')} km\n"
        f"注册时间 FirstRegistration: {p.get('FirstRegistration')}\n"
        f"事故 VehicleHistory: {p.get('VehicleHistory')}\n"
        f"事故记录 Has_Accident_Record: {yn(p.get('Has_Accident_Record'))}\n"
        f"轮毂 Wheels: {p.get('Wheels')}\n"
        f"Towing: {yn(p.get('Has_Towing'))}\n"
        f"Interior颜色 Interior: {p.get('Interior')}\n"
    )

def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        # leave blank by design; do not crash
        print("[WARN] Telegram token/chat_id not set. Message not sent.")
        print(text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, json=payload, timeout=20)
    r.raise_for_status()

def send_telegram_safe(text: str) -> None:
    """Send Telegram message but never crash the poll loop."""
    try:
        send_telegram(text)
    except Exception as e:
        print(f"[WARN] Telegram send failed: {e}")

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"seen": {}}  # VIN -> fingerprint
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def fingerprint_vehicle(p: dict) -> str:
    # fingerprint only on fields you care about (so you get update alerts)
    keys = [
        "Price_EUR", "Paint", "Odometer_km", "ActualRange_km",
        "FirstRegistration", "VehicleHistory", "Has_Accident_Record",
        "Wheels", "Has_Towing", "Interior",
    ]
    s = json.dumps({k: p.get(k) for k in keys}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def make_headers() -> dict:
    return dict(headers_base)

def fetch_inventory(payload: dict, session: requests.Session) -> dict:
    q = build_query_param(payload)
    url = f"{BASE_URL}?query={q}"

    # short, human-ish pre-delay
    time.sleep(random.uniform(0.4, 2.3))

    r = session.get(url, headers=make_headers(), timeout=(10, 30))

    # Debug info (keep it compact)
    print("status:", r.status_code)
    print("content-type:", r.headers.get("content-type"))
    print("server:", r.headers.get("server"))
    print("via:", r.headers.get("via"))
    print("set-cookie:", "set-cookie" in r.headers)

    # Akamai bot protection typically returns HTML on 403.
    if r.status_code == 403:
        # Save the HTML body for inspection
        try:
            with open("last_403.html", "w", encoding="utf-8") as f:
                f.write(r.text)
        except Exception:
            pass
        raise requests.HTTPError(
            "403 Forbidden (Akamai). Response HTML saved to last_403.html",
            response=r,
        )

    r.raise_for_status()

    # Guard: if content-type is not JSON, persist it for debugging
    ct = (r.headers.get("content-type") or "").lower()
    if "json" not in ct:
        try:
            with open("last_non_json.html", "w", encoding="utf-8") as f:
                f.write(r.text)
        except Exception:
            pass
        raise ValueError(f"Expected JSON but got content-type={ct}. Saved to last_non_json.html")

    return r.json()

def poll_loop(payload: dict) -> None:
    state = load_state()
    seen = state.get("seen", {})  # VIN -> fingerprint
    last_403_notice_ts = state.get("last_403_notice_ts", 0)

    with requests.Session() as s:
        # keep a few headers at session level too
        s.headers.update({"Accept": "*/*"})

        while True:
            try:
                # retry with backoff (and randomization)
                last_err = None
                data = None
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        data = fetch_inventory(payload, s)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e

                        # If Akamai is blocking (403), retries usually won't help.
                        if isinstance(e, requests.HTTPError) and getattr(e, "response", None) is not None:
                            if e.response.status_code == 403:
                                # Akamai block: avoid hammering. Notify at most once per hour.
                                now = int(time.time())
                                if now - int(last_403_notice_ts) > 3600:
                                    send_telegram_safe(
                                        "Tesla inventory API returned 403 (Akamai bot protection). "
                                        "Direct HTTP polling is currently blocked. "
                                        "I will back off and retry later."
                                    )
                                    last_403_notice_ts = now
                                    state["last_403_notice_ts"] = last_403_notice_ts
                                    save_state(state)

                                # Back off longer; short retries usually don't help.
                                time.sleep(random.uniform(30 * 60, 60 * 60))  # 30–60 min
                                break

                        backoff = (2 ** (attempt - 1)) + random.uniform(0.0, 1.8)
                        time.sleep(backoff)
                if last_err:
                    raise last_err

                results = data.get("results", []) or []
                updates = 0

                for v in results:
                    p = parse_vehicle(v)
                    vin = p.get("VIN") or ""
                    if not vin:
                        continue

                    fp = fingerprint_vehicle(p)
                    prev = seen.get(vin)

                    # Send on: new VIN OR tracked fields changed
                    if prev != fp:
                        send_telegram(fmt_vehicle_msg(p))
                        seen[vin] = fp
                        updates += 1

                        # spacing between messages to look less “botty”
                        time.sleep(random.uniform(1.0, 3.0))

                state["seen"] = seen
                save_state(state)

                # Persist any updated notice timestamps
                state["last_403_notice_ts"] = last_403_notice_ts
                save_state(state)

                # random polling interval
                base = random.uniform(POLL_MIN_SEC, POLL_MAX_SEC)
                jitter = random.uniform(0, RANDOM_JITTER_SEC)
                # if there were updates, poll a bit sooner sometimes; if none, sometimes slower
                if updates > 0:
                    sleep_s = max(30.0, base * random.uniform(0.6, 0.95) + jitter)
                else:
                    sleep_s = base * random.uniform(0.9, 1.25) + jitter

                time.sleep(sleep_s)

            except KeyboardInterrupt:
                print("Stopped by user.")
                return
            except Exception as e:
                # Generic errors: short backoff.
                err_sleep = random.uniform(60, 180)
                print(f"[ERROR] {e} -> sleep {err_sleep:.1f}s then retry")
                time.sleep(err_sleep)

def main():
    payload = build_payload(
        years=(2023, 2024, 2025),
        price_min=21000,
        price_max=31111,
        odo_max=70000,
        zip_code="85057",
        radius_km=200,
    )
    poll_loop(payload)

if __name__ == "__main__":
    main()