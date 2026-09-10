import os
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests

DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"
IGNAV_BASE = "https://ignav.com/api"
BLOCKED_DESTINATIONS = {"AYK"}
TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504, 522, 524}
RUN_STARTED_AT = None


def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def add_column_if_missing(conn, table, name, definition):
    if name not in table_columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS prices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, flight_key TEXT NOT NULL, flight_identity TEXT,
        origin TEXT, destination TEXT, departure_date TEXT, price REAL NOT NULL, currency TEXT,
        airline TEXT, flight_number TEXT, duration_minutes INTEGER, baggage TEXT,
        self_transfer INTEGER DEFAULT 0, price_status TEXT, ignav_id TEXT, stops INTEGER DEFAULT 0,
        seen_at TEXT, recorded_at TEXT, base_fare REAL, taxes REAL, fees REAL, tax_ratio REAL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS price_observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT, flight_key TEXT, flight_identity TEXT,
        origin TEXT, destination TEXT, departure_date TEXT, price REAL, currency TEXT,
        airline TEXT, flight_number TEXT, duration_minutes INTEGER, baggage TEXT,
        self_transfer INTEGER DEFAULT 0, price_status TEXT, ignav_id TEXT, stops INTEGER DEFAULT 0,
        observed_at TEXT, base_fare REAL, taxes REAL, fees REAL, tax_ratio REAL)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, flight_key TEXT NOT NULL, origin TEXT, destination TEXT,
        departure_date TEXT, price REAL NOT NULL, currency TEXT, airline TEXT, flight_number TEXT,
        duration_minutes INTEGER, baggage TEXT, self_transfer INTEGER DEFAULT 0, price_status TEXT,
        ignav_id TEXT, stops INTEGER DEFAULT 0, verified INTEGER DEFAULT 0, booking_verified INTEGER DEFAULT 0,
        source_disagreement_points INTEGER DEFAULT 0, verification_reason TEXT, checked_at TEXT,
        verification_time TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT, flight_key TEXT NOT NULL, origin TEXT, destination TEXT,
        departure_date TEXT, price REAL NOT NULL, currency TEXT, airline TEXT, flight_number TEXT,
        score REAL, alert_level TEXT, sent_at TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS radar_state (
        id INTEGER PRIMARY KEY, route_index INTEGER DEFAULT 0, updated_at TEXT)""")

    schemas = {
        "prices": {
            "flight_key": "TEXT", "flight_identity": "TEXT", "origin": "TEXT", "destination": "TEXT",
            "departure_date": "TEXT", "price": "REAL", "currency": "TEXT", "airline": "TEXT",
            "flight_number": "TEXT", "duration_minutes": "INTEGER", "baggage": "TEXT",
            "self_transfer": "INTEGER DEFAULT 0", "price_status": "TEXT", "ignav_id": "TEXT",
            "stops": "INTEGER DEFAULT 0", "seen_at": "TEXT", "recorded_at": "TEXT",
            "base_fare": "REAL", "taxes": "REAL", "fees": "REAL", "tax_ratio": "REAL"
        },
        "price_observations": {
            "flight_key": "TEXT", "flight_identity": "TEXT", "origin": "TEXT", "destination": "TEXT",
            "departure_date": "TEXT", "price": "REAL", "currency": "TEXT", "airline": "TEXT",
            "flight_number": "TEXT", "duration_minutes": "INTEGER", "baggage": "TEXT",
            "self_transfer": "INTEGER DEFAULT 0", "price_status": "TEXT", "ignav_id": "TEXT",
            "stops": "INTEGER DEFAULT 0", "observed_at": "TEXT", "base_fare": "REAL",
            "taxes": "REAL", "fees": "REAL", "tax_ratio": "REAL"
        },
        "verifications": {
            "flight_key": "TEXT", "origin": "TEXT", "destination": "TEXT", "departure_date": "TEXT",
            "price": "REAL", "currency": "TEXT", "airline": "TEXT", "flight_number": "TEXT",
            "duration_minutes": "INTEGER", "baggage": "TEXT", "self_transfer": "INTEGER DEFAULT 0",
            "price_status": "TEXT", "ignav_id": "TEXT", "stops": "INTEGER DEFAULT 0",
            "verified": "INTEGER DEFAULT 0", "booking_verified": "INTEGER DEFAULT 0",
            "source_disagreement_points": "INTEGER DEFAULT 0", "verification_reason": "TEXT",
            "checked_at": "TEXT", "verification_time": "TEXT"
        },
        "alerts": {
            "flight_key": "TEXT", "origin": "TEXT", "destination": "TEXT", "departure_date": "TEXT",
            "price": "REAL", "currency": "TEXT", "airline": "TEXT", "flight_number": "TEXT",
            "score": "REAL", "alert_level": "TEXT", "sent_at": "TEXT"
        }
    }
    for table, fields in schemas.items():
        for name, definition in fields.items():
            add_column_if_missing(conn, table, name, definition)

    if cur.execute("SELECT 1 FROM radar_state WHERE id=1").fetchone() is None:
        cur.execute("INSERT INTO radar_state(id,route_index,updated_at) VALUES(1,0,?)", (utc_iso(),))

    # Backfill stable identities for observations created by V3.2.
    rows = cur.execute("""SELECT id, flight_key, origin, destination, departure_date, airline, flight_number
                          FROM price_observations WHERE flight_identity IS NULL OR flight_identity=''""").fetchall()
    for row in rows:
        identity = make_identity(row[2], row[3], row[4], row[5], row[6], "")
        cur.execute("UPDATE price_observations SET flight_identity=? WHERE id=?", (identity, row[0]))
    rows = cur.execute("""SELECT id, flight_key, origin, destination, departure_date, airline, flight_number
                          FROM prices WHERE flight_identity IS NULL OR flight_identity=''""").fetchall()
    for row in rows:
        identity = make_identity(row[2], row[3], row[4], row[5], row[6], "")
        cur.execute("UPDATE prices SET flight_identity=? WHERE id=?", (identity, row[0]))

    cur.execute("CREATE INDEX IF NOT EXISTS idx_obs_identity ON price_observations(flight_identity, observed_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_obs_route ON price_observations(origin, destination, departure_date, currency, observed_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_prices_identity ON prices(flight_identity, recorded_at)")
    conn.commit()
    conn.close()
    print("Veritabani kontrolu tamamlandi.")


def safe_float(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(" ", "")
        if not s:
            return None
        try:
            if "," in s and "." in s:
                if s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "").replace(",", ".")
                else:
                    s = s.replace(",", "")
            elif "," in s:
                s = s.replace(",", ".")
            return float(s)
        except ValueError:
            return None
    return None


def normalize_text(v):
    return str(v or "").strip().upper()


def ignav_headers():
    key = os.getenv("IGNAV_API_KEY")
    return {"X-Api-Key": key, "Content-Type": "application/json"} if key else None


def get_market():
    return settings.get("system", {}).get("market", "TR") or "TR"


def ignav_search(origin, destination, departure_date):
    if destination in BLOCKED_DESTINATIONS:
        print(f"SKIP: {destination} engelli hedef.")
        return None
    headers = ignav_headers()
    if not headers:
        print("IGNAV_API_KEY bulunamadi.")
        return None
    p = settings.get("passengers", {})
    c = settings.get("connections", {})
    cabin = "business" if settings.get("cabin", {}).get("business", False) else "economy"
    payload = {
        "origin": origin, "destination": destination, "departure_date": departure_date,
        "adults": p.get("adults", 1), "children": p.get("children", 0),
        "infants_in_seat": 0, "infants_on_lap": p.get("infants", 0),
        "cabin_class": cabin, "max_stops": c.get("max_connections", 1),
        "allow_self_transfer": c.get("self_transfer", False), "market": get_market()
    }
    max_price = settings.get("price", {}).get("maximum_try", 0)
    if max_price and max_price > 0:
        payload["max_price"] = max_price
    disabled = settings.get("airlines", {}).get("disabled", [])
    if disabled:
        payload["airlines_exclude"] = disabled

    for attempt in range(1, 4):
        try:
            r = requests.post(f"{IGNAV_BASE}/fares/one-way", headers=headers, json=payload, timeout=60)
            print(f"Ignav: {origin}->{destination} {departure_date} HTTP {r.status_code}")
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception as e:
                    print("Ignav JSON hatasi:", repr(e))
                    return None
            if r.status_code not in TRANSIENT_STATUS:
                print("Ignav kalici hata, tekrar denenmeyecek:", r.text[:500])
                return None
            print(f"Ignav gecici hata ({attempt}/3):", r.text[:500])
        except (requests.Timeout, requests.ConnectionError) as e:
            print(f"Ignav gecici baglanti hatasi ({attempt}/3):", repr(e))
        except Exception as e:
            print("Ignav beklenmeyen hata:", repr(e))
            return None
        if attempt < 3:
            time.sleep(attempt * 2)
    return None


def get_booking_links(ignav_id):
    if not ignav_id:
        return None
    headers = ignav_headers()
    if not headers:
        return None
    try:
        r = requests.post(f"{IGNAV_BASE}/fares/booking-links", headers=headers,
                          json={"ignav_id": ignav_id}, timeout=60)
        print(f"Booking links: HTTP {r.status_code}")
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        print("Booking links hatasi:", repr(e))
        return None


def segment_signature(s):
    if not isinstance(s, dict):
        return ""
    return "|".join(normalize_text(s.get(k)) for k in (
        "departure_airport", "arrival_airport", "marketing_carrier_code", "flight_number",
        "departure_time", "arrival_time"))


def segment_path_signature(segments):
    out = []
    for s in segments or []:
        if not isinstance(s, dict):
            continue
        dep = normalize_text(s.get("departure_airport"))
        arr = normalize_text(s.get("arrival_airport"))
        carrier = normalize_text(s.get("marketing_carrier_code") or s.get("carrier_code"))
        number = normalize_text(s.get("flight_number"))
        if dep or arr or carrier or number:
            out.append(f"{dep}>{arr}:{carrier}{number}")
    return "|".join(out)


def make_identity(origin, destination, departure_date, airline, flight_number, path):
    # Stable historical identity: schedule times/duration are intentionally excluded.
    # The full flight_key still retains them for display/dedup.
    return "~".join([
        normalize_text(origin), normalize_text(destination), normalize_text(departure_date),
        normalize_text(airline), normalize_text(flight_number), normalize_text(path)
    ])


def create_flight_key(origin, destination, flight_number, airline, duration, stops, segments=None):
    sigs = [segment_signature(s) for s in (segments or []) if segment_signature(s)]
    return "-".join([
        normalize_text(origin), normalize_text(destination), normalize_text(airline),
        normalize_text(flight_number), str(duration), str(stops), "||".join(sigs)
    ])


def baggage_to_text(b):
    if b is None:
        return ""
    return b if isinstance(b, str) else json.dumps(b, ensure_ascii=False)


def extract_money(v):
    n = safe_float(v)
    if n is not None:
        return n
    if isinstance(v, dict):
        for k in ("amount", "value", "total", "price"):
            n = safe_float(v.get(k))
            if n is not None:
                return n
        for k in ("items", "components", "details", "breakdown"):
            if k in v:
                n = extract_money(v[k])
                if n is not None:
                    return n
    if isinstance(v, list):
        vals = [extract_money(x) for x in v]
        vals = [x for x in vals if x is not None]
        return sum(vals) if vals else None
    return None


def recursive_component(v, names):
    if isinstance(v, dict):
        label = str(v.get("type") or v.get("category") or v.get("name") or
                    v.get("label") or v.get("description") or "").lower()
        if any(n in label for n in names):
            n = extract_money(v)
            if n is not None:
                return n
        for k, x in v.items():
            if str(k).lower() in names:
                n = extract_money(x)
                if n is not None:
                    return n
            n = recursive_component(x, names)
            if n is not None:
                return n
    elif isinstance(v, list):
        for x in v:
            n = recursive_component(x, names)
            if n is not None:
                return n
    return None


def price_breakdown(itinerary, price_info, total):
    base = recursive_component(price_info, {"base_fare", "basefare", "base fare", "fare"})
    taxes = recursive_component(price_info, {"tax", "taxes", "taxes_and_fees", "taxesandfees"})
    fees = recursive_component(price_info, {"fee", "fees", "mandatory_fee", "mandatory fees"})
    values = (base, taxes, fees)
    if any(x is not None and x < 0 for x in values):
        return None, None, None, None
    known = [x for x in values if x is not None]
    if total is not None and known and sum(known) > total * 1.03:
        return None, None, None, None
    if base is not None and taxes is not None and fees is None and total is not None:
        fees = total - base - taxes
    elif base is not None and fees is not None and taxes is None and total is not None:
        taxes = total - base - fees
    elif base is None and taxes is not None and fees is not None and total is not None:
        base = total - taxes - fees
    if any(x is not None and x < 0 for x in (base, taxes, fees)):
        return None, None, None, None
    ratio = None
    if total and taxes is not None and fees is not None:
        ratio = (taxes + fees) / total
    elif total and taxes is not None:
        ratio = taxes / total
    return base, taxes, fees, ratio


def extract_flights(data, origin, destination, departure_date):
    if not isinstance(data, dict):
        return []
    raw = data.get("itineraries") or data.get("results") or data.get("fares") or []
    if isinstance(raw, dict):
        raw = raw.get("itineraries") or raw.get("results") or list(raw.values())
    flights = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        pi = it.get("price") or it.get("total_price") or {}
        price = extract_money(pi)
        if price is None or price <= 0:
            continue
        currency = normalize_text((pi.get("currency") if isinstance(pi, dict) else None) or
                                  it.get("currency") or settings.get("system", {}).get("currency", "TRY"))
        outbound = it.get("outbound") or it.get("outbound_itinerary") or {}
        segments = outbound.get("segments") if isinstance(outbound, dict) else None
        if not isinstance(segments, list):
            segments = []
        first = segments[0] if segments else {}
        last = segments[-1] if segments else first
        airline = normalize_text(first.get("marketing_carrier_code") or first.get("carrier_code") or it.get("airline"))
        flight_no = normalize_text(first.get("flight_number") or it.get("flight_number"))
        duration_raw = it.get("duration_minutes")
        if duration_raw is None and isinstance(outbound, dict):
            duration_raw = outbound.get("duration_minutes")
        duration = int(safe_float(duration_raw) or 0)
        stops = max(0, len(segments) - 1)
        self_transfer = bool(it.get("requires_self_transfer") or it.get("self_transfer") or False)
        bags = it.get("bags") or it.get("baggage") or first.get("bags") or first.get("baggage")
        path = segment_path_signature(segments)
        identity = make_identity(origin, destination, departure_date, airline, flight_no, path)
        key = create_flight_key(origin, destination, flight_no, airline, duration, stops, segments)
        base, taxes, fees, ratio = price_breakdown(it, pi, price)
        flights.append({
            "flight_key": key, "flight_identity": identity,
            "origin": origin, "destination": destination, "departure_date": departure_date,
            "price": price, "currency": currency, "airline": airline, "flight_number": flight_no,
            "duration_minutes": duration, "baggage": baggage_to_text(bags),
            "self_transfer": self_transfer, "price_status": "",
            "ignav_id": it.get("ignav_id") or it.get("id"), "stops": stops,
            "seen_at": utc_iso(), "recorded_at": utc_iso(), "base_fare": base,
            "taxes": taxes, "fees": fees, "tax_ratio": ratio,
            "segments": segments, "last_arrival": last.get("arrival_time")
        })
    return flights


def canonical_display_key(f):
    return (
        normalize_text(f.get("origin")), normalize_text(f.get("destination")),
        normalize_text(f.get("departure_date")), normalize_text(f.get("airline")),
        normalize_text(f.get("flight_number")), round(float(f.get("price") or 0), 2),
        normalize_text(f.get("currency")), bool(f.get("self_transfer")), int(f.get("stops") or 0),
        "||".join(segment_signature(s) for s in f.get("segments", []) if segment_signature(s))
    )


def dedup_flights(flights):
    seen = set()
    out = []
    for f in flights:
        if f.get("price") is None:
            continue
        k = canonical_display_key(f)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out, len(flights) - len(out)


def save_flight(f):
    conn = get_connection()
    now = utc_iso()
    conn.execute("""INSERT INTO prices(
        flight_key,flight_identity,origin,destination,departure_date,price,currency,airline,flight_number,
        duration_minutes,baggage,self_transfer,price_status,ignav_id,stops,seen_at,recorded_at,
        base_fare,taxes,fees,tax_ratio) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (f["flight_key"], f["flight_identity"], f["origin"], f["destination"], f["departure_date"],
                  f["price"], f["currency"], f["airline"], f["flight_number"], f["duration_minutes"],
                  f["baggage"], int(f["self_transfer"]), f["price_status"], f["ignav_id"], f["stops"],
                  now, now, f["base_fare"], f["taxes"], f["fees"], f["tax_ratio"]))

    # Same flight + same fare is one observation for a short period. Different prices remain history.
    recent_cutoff = (utc_now() - timedelta(hours=6)).isoformat()
    recent = conn.execute("""SELECT 1 FROM price_observations
        WHERE flight_identity=? AND departure_date=? AND currency=? AND ABS(price-?)<0.01
        AND observed_at>=? LIMIT 1""",
                         (f["flight_identity"], f["departure_date"], f["currency"], f["price"], recent_cutoff)).fetchone()
    if not recent:
        conn.execute("""INSERT INTO price_observations(
            flight_key,flight_identity,origin,destination,departure_date,price,currency,airline,flight_number,
            duration_minutes,baggage,self_transfer,price_status,ignav_id,stops,observed_at,
            base_fare,taxes,fees,tax_ratio) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (f["flight_key"], f["flight_identity"], f["origin"], f["destination"], f["departure_date"],
                      f["price"], f["currency"], f["airline"], f["flight_number"], f["duration_minutes"],
                      f["baggage"], int(f["self_transfer"]), f["price_status"], f["ignav_id"], f["stops"], now,
                      f["base_fare"], f["taxes"], f["fees"], f["tax_ratio"]))
    conn.commit()
    conn.close()


def history_cutoff():
    return RUN_STARTED_AT or utc_iso()


def get_previous_price(f):
    conn = get_connection()
    r = conn.execute("""SELECT price FROM price_observations
        WHERE flight_identity=? AND departure_date=? AND currency=? AND observed_at<?
        ORDER BY observed_at DESC, id DESC LIMIT 1""",
                     (f["flight_identity"], f["departure_date"], f["currency"], history_cutoff())).fetchone()
    conn.close()
    return float(r[0]) if r else None


def _latest_per_identity(rows):
    latest = {}
    for identity, price, observed_at in rows:
        if not identity:
            continue
        old = latest.get(identity)
        if old is None or observed_at > old[1]:
            latest[identity] = (float(price), observed_at)
    return [x[0] for x in latest.values() if x[0] > 0]


def stable_flight_history(f):
    conn = get_connection()
    rows = conn.execute("""SELECT flight_identity,price,observed_at FROM price_observations
        WHERE flight_identity=? AND currency=? AND observed_at<? AND price>0
        ORDER BY observed_at DESC LIMIT 100""",
                        (f["flight_identity"], f["currency"], history_cutoff())).fetchall()
    conn.close()
    return [float(r[1]) for r in rows if r[1] > 0]


def airline_flight_history(f):
    conn = get_connection()
    rows = conn.execute("""SELECT flight_identity,price,observed_at FROM price_observations
        WHERE origin=? AND destination=? AND departure_date=? AND currency=?
          AND airline=? AND flight_number=? AND observed_at<? AND price>0
        ORDER BY observed_at DESC LIMIT 300""",
                        (f["origin"], f["destination"], f["departure_date"], f["currency"],
                         f["airline"], f["flight_number"], history_cutoff())).fetchall()
    conn.close()
    return _latest_per_identity(rows)


def route_market_history(f):
    conn = get_connection()
    rows = conn.execute("""SELECT flight_identity,price,observed_at FROM price_observations
        WHERE origin=? AND destination=? AND departure_date=? AND currency=?
          AND observed_at<? AND price>0
        ORDER BY observed_at DESC LIMIT 1000""",
                        (f["origin"], f["destination"], f["departure_date"], f["currency"], history_cutoff())).fetchall()
    conn.close()
    return _latest_per_identity(rows)


def normal_price(f):
    # Prefer the same flight. If it has little history, use same flight number,
    # then route/date market history. This avoids fragmenting history on time/duration drift.
    exact = stable_flight_history(f)
    if len(exact) >= 2:
        return statistics.median(exact), len(exact), "exact-flight"
    same_number = airline_flight_history(f)
    if len(same_number) >= 2:
        return statistics.median(same_number), len(same_number), "airline-flight"
    market = route_market_history(f)
    if len(market) >= 3:
        return statistics.median(market), len(market), "route-market"
    return None, max(len(exact), len(same_number), len(market)), "insufficient"


def score_preliminary(f, prev, normal_info):
    normal, samples, normal_source = normal_info
    score = 0
    market_drop = 0.0
    sudden = 0.0
    if prev and prev > 0:
        sudden = max(0, (prev - f["price"]) / prev * 100)
        if sudden >= 60: score += 18
        elif sudden >= 50: score += 15
        elif sudden >= 40: score += 12
        elif sudden >= 30: score += 9
        elif sudden >= 20: score += 6
        elif sudden >= 10: score += 3
        elif sudden > 0: score += 1

    if normal and normal > 0:
        market_drop = max(0, (normal - f["price"]) / normal * 100)
        if market_drop >= 70: score += 28
        elif market_drop >= 60: score += 25
        elif market_drop >= 50: score += 21
        elif market_drop >= 40: score += 17
        elif market_drop >= 30: score += 13
        elif market_drop >= 20: score += 9
        elif market_drop >= 10: score += 5
        elif market_drop > 0: score += 2

    if f.get("tax_ratio") is not None and f["tax_ratio"] > 0.35:
        score += 4
    if f.get("currency") == settings.get("system", {}).get("currency", "TRY"):
        score += 2
    if not f.get("self_transfer") and int(f.get("stops") or 0) <= 1:
        score += 2
    if f.get("price", 0) > 0 and f.get("price", 0) < 1500:
        score += 2
    return min(78, score), market_drop, sudden, samples, normal_source


def recursive_urls(v):
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            if isinstance(x, str) and x.startswith(("http://", "https://")):
                host = urlparse(x).netloc.lower()
                if host and host not in {"ignav.com", "www.ignav.com"}:
                    out.append(x)
            else:
                out.extend(recursive_urls(x))
    elif isinstance(v, list):
        for x in v:
            out.extend(recursive_urls(x))
    return list(dict.fromkeys(out))


def recursive_booking_prices(v, inherited_currency=""):
    """Extract likely final booking totals only; avoid treating every nested amount as a fare."""
    vals = []
    if isinstance(v, dict):
        cur = normalize_text(v.get("currency") or v.get("currency_code") or inherited_currency)
        priority_keys = ("total_price", "total", "final_price", "booking_price", "price", "fare_total", "amount")
        for k in priority_keys:
            if k in v:
                n = safe_float(v.get(k))
                if n is not None and n > 0:
                    vals.append((n, cur, str(k).lower()))
        for k, x in v.items():
            kl = str(k).lower()
            if isinstance(x, (dict, list)):
                vals.extend(recursive_booking_prices(x, cur))
            elif kl in priority_keys:
                n = safe_float(x)
                if n is not None and n > 0:
                    vals.append((n, cur, kl))
    elif isinstance(v, list):
        for x in v:
            vals.extend(recursive_booking_prices(x, inherited_currency))
    # de-duplicate same amount/currency/key
    return list(dict.fromkeys(vals))


def booking_analysis(f):
    data = get_booking_links(f.get("ignav_id"))
    if not data:
        return {"checked": False, "verified": False, "points": 0, "urls": [], "reason": "booking verisi yok"}
    target = float(f["price"])
    cur = normalize_text(f["currency"])
    candidates = []
    for n, c, key in recursive_booking_prices(data):
        if not c or c == cur:
            candidates.append((n, c, key))
    near = []
    for n, c, key in candidates:
        if c and c != cur:
            continue
        diff = abs(n - target) / target if target else 999
        if diff <= 0.03 or abs(n - target) <= 100:
            near.append((diff, n, key))
    near.sort()
    urls = recursive_urls(data)
    verified = bool(near and urls)
    points = 25 if verified else (3 if candidates else 0)
    return {
        "checked": True, "verified": verified, "points": points, "urls": urls[:3],
        "near_price": near[0][1] if near else None,
        "provider_prices": [n for n, _, _ in candidates[:10]],
        "reason": "ayni para birimi + yakin toplam + gercek link" if verified else "yakin toplam/link teyidi yok"
    }


def save_verification(f, verification, booking):
    conn = get_connection()
    conn.execute("""INSERT INTO verifications(
        flight_key,origin,destination,departure_date,price,currency,airline,flight_number,
        duration_minutes,baggage,self_transfer,price_status,ignav_id,stops,verified,booking_verified,
        source_disagreement_points,verification_reason,checked_at,verification_time)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (f["flight_key"], f["origin"], f["destination"], f["departure_date"], f["price"], f["currency"],
                  f["airline"], f["flight_number"], f["duration_minutes"], f["baggage"], int(f["self_transfer"]),
                  f["price_status"], f["ignav_id"], f["stops"], int(verification.get("verified", False)),
                  int(booking.get("verified", False)), 0, verification.get("reason", ""), utc_iso(), utc_iso()))
    conn.commit()
    conn.close()


def verify_flight(f):
    data = ignav_search(f["origin"], f["destination"], f["departure_date"])
    if not data:
        return {"verified": False, "reason": "ikinci arama sonucu yok"}
    candidates, _ = dedup_flights(extract_flights(data, f["origin"], f["destination"], f["departure_date"]))
    same_currency = [x for x in candidates if normalize_text(x["currency"]) == normalize_text(f["currency"])]
    tolerance = max(50, float(f["price"]) * 0.02)
    exact = [x for x in same_currency if x.get("flight_identity") == f.get("flight_identity") and
             abs(float(x["price"]) - float(f["price"])) <= tolerance]
    if exact:
        return {"verified": True, "reason": "ayni stabil ucus kimligi + yakin fiyat"}

    best = None
    bestd = 999
    for x in same_currency:
        if x["airline"] == f["airline"] and x["flight_number"] == f["flight_number"]:
            d = abs(x["price"] - f["price"]) / max(f["price"], 1)
            if d < bestd:
                bestd, best = d, x
    if best and bestd <= 0.03:
        return {"verified": True, "reason": "ayni havayolu/ucus numarasi + yakin fiyat"}
    return {"verified": False, "reason": "ikinci aramada yeterli eslesme yok"}


def final_score(pre, verify, booking, f, market_drop=0, previous_drop=0):
    score = pre
    if verify.get("verified"):
        score += 10
    if booking.get("verified"):
        score += 25
    elif booking.get("checked"):
        score += booking.get("points", 0)
    if market_drop >= 60:
        score += 12
    elif market_drop >= 50:
        score += 8
    elif market_drop >= 40:
        score += 5
    if previous_drop >= 50:
        score += 5
    if f.get("self_transfer"):
        score -= 8
    if f.get("stops", 0) > 1:
        score -= 3
    return max(0, min(100, score))


def quality_gate(f, score, verified, booking, market_drop, previous_drop, history_samples):
    if not verified or history_samples < 2:
        return False
    if f.get("self_transfer"):
        return score >= 92 and market_drop >= 60 and booking.get("verified")
    return (
        (score >= 90 and market_drop >= 50) or
        (score >= 87 and market_drop >= 50 and previous_drop >= 30) or
        (score >= 87 and market_drop >= 40 and booking.get("verified")) or
        (score >= 90 and market_drop >= 60)
    )


def alert_level(score):
    if score >= 90:
        return "HIGH_CONFIDENCE_ERROR_FARE"
    if score >= 85:
        return "ERROR_FARE_CANDIDATE"
    if score >= 70:
        return "SUSPICIOUS"
    return "BELOW_ALERT"


def telegram_configured():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID eksik.")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text[:4090], "disable_web_page_preview": False},
            timeout=30
        )
        if r.status_code == 200:
            print("Telegram: mesaj gonderildi.")
            return True
        print("Telegram HTTP hata:", r.status_code, r.text[:500])
        return False
    except Exception as e:
        print("Telegram hatasi:", repr(e))
        return False


def telegram_test():
    if not telegram_configured():
        print("Telegram test: secret eksik, test yapilmadi.")
        return False
    return telegram_send("âœ… UÃ§uÅŸ Hata FiyatÄ± RadarÄ± V3.3 baÄŸlantÄ± testi baÅŸarÄ±lÄ±.\nTelegram bildirim kanalÄ± aktif.")


def alert_allowed(f, score, quality):
    a = settings.get("alerts", {})
    if not a.get("enabled", True) or not a.get("telegram_enabled", True) or not quality:
        return False
    return score >= int(a.get("candidate_score", 85))


def already_alerted(f):
    hours = float(settings.get("radar", {}).get("duplicate_alert_window_hours", 24))
    conn = get_connection()
    r = conn.execute("""SELECT 1 FROM alerts
        WHERE origin=? AND destination=? AND departure_date=? AND flight_key=?
          AND sent_at>=? LIMIT 1""",
                     (f["origin"], f["destination"], f["departure_date"], f["flight_key"],
                      (utc_now() - timedelta(hours=hours)).isoformat())).fetchone()
    conn.close()
    return bool(r)


def save_alert(f, score, level):
    conn = get_connection()
    conn.execute("""INSERT INTO alerts(
        flight_key,origin,destination,departure_date,price,currency,airline,flight_number,score,alert_level,sent_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                 (f["flight_key"], f["origin"], f["destination"], f["departure_date"], f["price"],
                  f["currency"], f["airline"], f["flight_number"], score, level, utc_iso()))
    conn.commit()
    conn.close()


def send_alert(f, score, normal, normal_source, prev, booking, samples, market_drop, previous_drop):
    level = alert_level(score)
    lines = [
        "ğŸš¨ UÃ‡UÅ HATA FÄ°YATI RADARI",
        f"{f['origin']} â†’ {f['destination']}",
        f"Tarih: {f['departure_date']}",
        f"UÃ§uÅŸ: {f['airline']} {f['flight_number']}",
        f"Fiyat: {f['price']:.0f} {f['currency']}",
        f"Skor: {score:.0f} / 100",
        f"Seviye: {level}",
        f"GeÃ§miÅŸ Ã¶rnek: {samples} | Normal kaynaÄŸÄ±: {normal_source}",
        f"Piyasa dÃ¼ÅŸÃ¼ÅŸÃ¼: %{market_drop:.1f}",
        f"Ã–nceki fiyata dÃ¼ÅŸÃ¼ÅŸ: %{previous_drop:.1f}",
    ]
    if normal:
        lines.append(f"Normal medyan: {normal:.0f} {f['currency']}")
    if prev:
        lines.append(f"Ã–nceki fiyat: {prev:.0f} {f['currency']}")
    if booking.get("near_price") is not None:
        lines.append(f"Booking teyidi: {booking['near_price']:.0f} {f['currency']}")
    if booking.get("urls"):
        lines.append("Rezervasyon baÄŸlantÄ±larÄ±:")
        lines.extend(f"{i+1}. {u}" for i, u in enumerate(booking["urls"]))
    return telegram_send("\n".join(lines))


def build_routes():
    a = settings.get("airports", {})
    origins = a.get("priority_origins", [])
    domestic = a.get("domestic_destinations", []) if a.get("domestic_enabled", True) else []
    europe = a.get("europe_destinations", []) if a.get("europe_enabled", True) else []
    destinations = []
    for d in domestic + europe:
        if d not in BLOCKED_DESTINATIONS and d not in destinations:
            destinations.append(d)
    return [(o, d) for o in origins for d in destinations if o != d]


def rotated_routes(routes, count):
    if not routes:
        return []
    conn = get_connection()
    idx = int(conn.execute("SELECT route_index FROM radar_state WHERE id=1").fetchone()[0])
    chosen = [routes[(idx + i) % len(routes)] for i in range(min(count, len(routes)))]
    new = (idx + len(chosen)) % len(routes)
    conn.execute("UPDATE radar_state SET route_index=?,updated_at=? WHERE id=1", (new, utc_iso()))
    conn.commit()
    conn.close()
    return chosen


def target_dates():
    offsets = settings.get("radar", {}).get("dates_days_ahead", [30, 60, 90])
    base = datetime.now(timezone.utc).date()
    return [(base + timedelta(days=int(x))).isoformat() for x in offsets]


def process_flight(f):
    # IMPORTANT: calculate history before inserting this run's observation.
    prev = get_previous_price(f)
    normal_info = normal_price(f)
    normal, samples, normal_source = normal_info
    pre, market_drop, sudden, history_samples, history_source = score_preliminary(f, prev, normal_info)

    verify_min = int(settings.get("radar", {}).get("verification", {}).get("minimum_score_for_verification", 30))
    verification = (
        verify_flight(f)
        if pre >= verify_min and settings.get("radar", {}).get("verification", {}).get("enabled", True)
        else {"verified": False, "reason": "esik altinda"}
    )

    previous_drop = max(0, (prev - f["price"]) / prev * 100) if prev and prev > 0 else 0
    # Booking is independent of the old >=60 deadlock.
    booking_trigger = pre >= 30 and (
        market_drop >= 30 or previous_drop >= 20 or pre >= 45
    )
    booking = booking_analysis(f) if booking_trigger else {
        "checked": False, "verified": False, "points": 0, "urls": [], "reason": "tetiklenmedi"
    }

    score = final_score(pre, verification, booking, f, market_drop, previous_drop)
    quality = quality_gate(f, score, verification.get("verified", False), booking,
                           market_drop, previous_drop, history_samples)

    # Persist current observation after all historical calculations.
    save_flight(f)
    save_verification(f, verification, booking)

    sent = False
    if alert_allowed(f, score, quality) and not already_alerted(f):
        sent = send_alert(f, score, normal, normal_source, prev, booking, history_samples,
                          market_drop, previous_drop)
        if sent:
            save_alert(f, score, alert_level(score))

    print(
        f"  {f['origin']}->{f['destination']} {f['departure_date']} "
        f"{f['airline']} {f['flight_number']} {f['price']:.0f} {f['currency']} | "
        f"NORMAL={normal:.0f}({normal_source},{history_samples}) " if normal else
        f"  {f['origin']}->{f['destination']} {f['departure_date']} {f['airline']} {f['flight_number']} {f['price']:.0f} {f['currency']} | NORMAL=YOK "
    , end="")
    print(
        f"CURRENT={f['price']:.0f} MARKET_DROP=%{market_drop:.1f} "
        f"PREV={'%.0f' % prev if prev else '-'} PREV_DROP=%{previous_drop:.1f} "
        f"PRE={pre} VERIFY={verification.get('verified', False)} "
        f"BOOKING={booking.get('checked', False)}/{booking.get('verified', False)} "
        f"FINAL={score} QUALITY={'PASS' if quality else 'FAIL'} SENT={sent}"
    )

    return {
        "pre": pre, "score": score, "verified": verification.get("verified", False),
        "booking_checked": booking.get("checked", False), "booking_verified": booking.get("verified", False),
        "quality": quality, "sent": sent, "market_drop": market_drop,
        "previous_drop": previous_drop, "history_samples": history_samples
    }


def main():
    global RUN_STARTED_AT
    RUN_STARTED_AT = utc_iso()
    init_db()

    if os.getenv("TELEGRAM_TEST", "0") == "1":
        telegram_test()

    routes = build_routes()
    chosen = rotated_routes(routes, int(settings.get("radar", {}).get("routes_per_run", 10)))
    dates = target_dates()
    print(f"Toplam rota havuzu: {len(routes)} | Bu calismada: {len(chosen)} | Tarih: {dates}")
    print(f"Telegram yapilandirilmis: {'EVET' if telegram_configured() else 'HAYIR'}")

    total = dedup = verified = bookings = booking_verified = quality_pass = sent_count = 0
    for origin, destination in chosen:
        for date in dates:
            data = ignav_search(origin, destination, date)
            if not data:
                continue
            flights, dc = dedup_flights(extract_flights(data, origin, destination, date))
            dedup += dc
            total += len(flights)
            for f in flights:
                r = process_flight(f)
                verified += int(r["verified"])
                bookings += int(r["booking_checked"])
                booking_verified += int(r["booking_verified"])
                quality_pass += int(r["quality"])
                sent_count += int(r["sent"])

    print("\n=== V3.3 DIAGNOSTIK ===")
    print(f"Islenen ucus: {total}")
    print(f"Dedup edilen: {dedup}")
    print(f"Verification: {verified}")
    print(f"Booking kontrolu: {bookings}")
    print(f"Booking yakin fiyat teyidi: {booking_verified}")
    print(f"Kaliteyi gecen: {quality_pass}")
    print(f"Telegram gonderilen: {sent_count}")
    print("Calisma tamamlandi.")


if __name__ == "__main__":
    main()
