import os
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests


DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"
IGNAV_BASE = "https://ignav.com/api"


# ============================================================
# GENEL
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def safe_float(value, default=None):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize(value):
    return str(value or "").strip().upper()


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


# ============================================================
# DATABASE / MIGRATION
# ============================================================

def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column(conn, table, name, definition):
    if name not in columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            return_date TEXT,
            trip_type TEXT DEFAULT 'one-way',
            price REAL NOT NULL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            checked_bags INTEGER,
            carry_on_bags INTEGER,
            self_transfer INTEGER DEFAULT 0,
            price_status TEXT,
            ignav_id TEXT,
            stops INTEGER DEFAULT 0,
            seen_at TEXT,
            recorded_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            return_date TEXT,
            trip_type TEXT DEFAULT 'one-way',
            price REAL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            checked_bags INTEGER,
            carry_on_bags INTEGER,
            self_transfer INTEGER DEFAULT 0,
            price_status TEXT,
            ignav_id TEXT,
            stops INTEGER DEFAULT 0,
            observed_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            return_date TEXT,
            trip_type TEXT DEFAULT 'one-way',
            price REAL NOT NULL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            self_transfer INTEGER DEFAULT 0,
            price_status TEXT,
            ignav_id TEXT,
            stops INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            booking_verified INTEGER DEFAULT 0,
            source_disagreement_points INTEGER DEFAULT 0,
            verification_reason TEXT,
            checked_at TEXT,
            verification_time TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            return_date TEXT,
            trip_type TEXT DEFAULT 'one-way',
            price REAL NOT NULL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            score REAL,
            opportunity_score REAL,
            alert_level TEXT,
            booking_url TEXT,
            sent_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY,
            route_index INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)

    if cur.execute("SELECT 1 FROM radar_state WHERE id=1").fetchone() is None:
        cur.execute(
            "INSERT INTO radar_state(id, route_index, updated_at) VALUES(1,0,?)",
            (utc_iso(),)
        )

    # Eski V2/V2.1/V2.2 verileri korunur.
    price_fields = {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "checked_bags": "INTEGER",
        "carry_on_bags": "INTEGER",
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "seen_at": "TEXT",
        "recorded_at": "TEXT",
    }
    for name, definition in price_fields.items():
        add_column(conn, "prices", name, definition)

    observation_fields = {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "checked_bags": "INTEGER",
        "carry_on_bags": "INTEGER",
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "observed_at": "TEXT",
    }
    for name, definition in observation_fields.items():
        add_column(conn, "price_observations", name, definition)

    verification_fields = {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "verified": "INTEGER DEFAULT 0",
        "booking_verified": "INTEGER DEFAULT 0",
        "source_disagreement_points": "INTEGER DEFAULT 0",
        "verification_reason": "TEXT",
        "checked_at": "TEXT",
        "verification_time": "TEXT",
    }
    for name, definition in verification_fields.items():
        add_column(conn, "verifications", name, definition)

    alert_fields = {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "opportunity_score": "REAL",
        "booking_url": "TEXT",
        "sent_at": "TEXT",
    }
    for name, definition in alert_fields.items():
        add_column(conn, "alerts", name, definition)

    now = utc_iso()
    cur.execute("UPDATE prices SET seen_at=? WHERE seen_at IS NULL", (now,))
    cur.execute("""
        UPDATE prices SET recorded_at=seen_at
        WHERE recorded_at IS NULL AND seen_at IS NOT NULL
    """)
    cur.execute(
        "UPDATE price_observations SET observed_at=? WHERE observed_at IS NULL",
        (now,)
    )
    cur.execute(
        "UPDATE verifications SET checked_at=? WHERE checked_at IS NULL",
        (now,)
    )
    cur.execute("""
        UPDATE verifications SET verification_time=checked_at
        WHERE verification_time IS NULL AND checked_at IS NOT NULL
    """)
    cur.execute("UPDATE alerts SET sent_at=? WHERE sent_at IS NULL", (now,))

    conn.commit()
    conn.close()
    print("Veritabani kontrolu tamamlandi.")


# ============================================================
# API BUTCESI / SAYAC
# ============================================================

# IGNAV 402 (billing_required) alindiginda bu calisma boyunca yeni
# API istegi gonderilmez. Boylece kota bittiginde ayni istegi tekrar
# tekrar deneyerek gereksiz trafik ve uzun calisma olusmasi engellenir.
IGNAV_BILLING_BLOCKED = False

def api_budget():
    system = settings.get("system", {})
    return {
        "allowed": bool(system.get("paid_api_allowed", False)),
        "budget_tl": safe_float(system.get("api_budget_tl"), 0) or 0,
        "daily_limit": int(system.get("daily_query_limit", 0) or 0),
        "monthly_budget_usd": safe_float(system.get("monthly_api_budget_usd"), 0) or 0,
    }


def count_queries_since(conn, start_iso):
    row = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE used_at >= ?",
        (start_iso,)
    ).fetchone()
    return int(row[0] or 0)


def ensure_usage_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            used_at TEXT NOT NULL,
            endpoint TEXT,
            success INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0
        )
    """)


def api_call_allowed():
    budget = api_budget()

    if not budget["allowed"]:
        # KullanÄ±cÄ± paid API'yi kapattÄ±ysa Ã¼cretsiz 1000 istek sÄ±nÄ±rÄ±nÄ±
        # aÅŸmayÄ± Ã¶nlemek iÃ§in kalÄ±cÄ± sayaÃ§ tutuyoruz.
        conn = get_connection()
        ensure_usage_table(conn)
        count = conn.execute("SELECT COUNT(*) FROM api_usage").fetchone()[0]
        conn.close()
        return count < 1000

    conn = get_connection()
    ensure_usage_table(conn)
    now = utc_now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    daily = count_queries_since(conn, day_start)
    monthly = count_queries_since(conn, month_start)
    conn.close()

    if budget["daily_limit"] > 0 and daily >= budget["daily_limit"]:
        print("API gunluk sorgu limiti doldu.")
        return False

    # Ignav faturalamasi USD bazli oldugu icin burada kullanici
    # monthly_api_budget_usd alanini kullanir.
    if budget["monthly_budget_usd"] > 0:
        conn = get_connection()
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd),0) FROM api_usage WHERE used_at >= ?",
            (month_start,)
        ).fetchone()
        conn.close()
        if float(row[0] or 0) >= budget["monthly_budget_usd"]:
            print("API aylik butce limiti doldu.")
            return False

    return True


def record_api_call(endpoint, success):
    conn = get_connection()
    ensure_usage_table(conn)

    # Ignav dokumantasyonundaki model: ilk 1000 basarili istek ucretsiz,
    # sonrasinda 1000 basarili istek basina $2.
    successful = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE success=1"
    ).fetchone()[0]
    cost = 0
    if success and successful >= 1000:
        cost = 2 / 1000

    conn.execute(
        "INSERT INTO api_usage(used_at,endpoint,success,estimated_cost_usd) VALUES(?,?,?,?)",
        (utc_iso(), endpoint, 1 if success else 0, cost)
    )
    conn.commit()
    conn.close()


# ============================================================
# IGNAV
# ============================================================

def ignav_headers():
    key = os.getenv("IGNAV_API_KEY")
    if not key:
        return None
    return {"X-Api-Key": key, "Content-Type": "application/json"}


def market():
    return settings.get("system", {}).get("market", "TR") or "TR"


def cabin_class():
    cabin = settings.get("cabin", {})
    if cabin.get("business", False):
        return "business"
    return "economy"


def common_payload(origin, destination, departure_date):
    passengers = settings.get("passengers", {})
    connections = settings.get("connections", {})
    price_cfg = settings.get("price", {})
    airlines = settings.get("airlines", {})

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": int(passengers.get("adults", 1) or 1),
        "children": int(passengers.get("children", 0) or 0),
        "infants_in_seat": 0,
        "infants_on_lap": int(passengers.get("infants", 0) or 0),
        "cabin_class": cabin_class(),
        "max_stops": int(connections.get("max_connections", 1) or 1),
        "allow_self_transfer": bool(connections.get("self_transfer", False)),
        "market": market(),
    }

    maximum = safe_float(price_cfg.get("maximum_try"), 0) or 0
    if maximum > 0:
        payload["max_price"] = maximum

    disabled = airlines.get("disabled", [])
    if disabled:
        payload["airlines_exclude"] = disabled

    return payload


def ignav_post(endpoint, payload):
    global IGNAV_BILLING_BLOCKED

    headers = ignav_headers()
    if not headers:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    # Bir onceki IGNAV istegi 402 billing_required verdiyse bu calisma
    # boyunca yeni istek gonderme. Ucretsiz kota bittiginde 402 kalici
    # bir durumdur; retry yapmak fayda saglamaz.
    if IGNAV_BILLING_BLOCKED:
        print("IGNAV 402: faturalandirma gerekli; bu calisma icin API durduruldu.")
        return None

    if not api_call_allowed():
        print("API sorgusu butce/limit nedeniyle durduruldu.")
        return None

    for attempt in range(1, 4):
        try:
            response = requests.post(
                f"{IGNAV_BASE}{endpoint}",
                headers=headers,
                json=payload,
                timeout=60,
            )
            ok = response.status_code == 200
            record_api_call(endpoint, ok)

            print(f"Ignav {endpoint}: HTTP {response.status_code}")

            if ok:
                try:
                    return response.json()
                except ValueError as exc:
                    print("Ignav JSON hatasi:", repr(exc))
                    return None

            if response.status_code == 402:
                IGNAV_BILLING_BLOCKED = True
                print("IGNAV 402: billing_required. Yeni API istekleri bu calisma icin durduruldu.")
                print("Ignav cevabi:", response.text[:500])
                return None

            print("Ignav cevabi:", response.text[:500])

            # 4xx kalici hatalarda retry yapma. Yalnizca gecici HTTP
            # durumlari ve baglanti hatalari tekrar denenebilir.
            if response.status_code not in (408, 429, 500, 502, 503, 504):
                print("Ignav kalici hata, tekrar denenmeyecek.")
                return None

            if attempt < 3:
                time.sleep(attempt * 2)

        except (requests.Timeout, requests.ConnectionError) as exc:
            record_api_call(endpoint, False)
            print(f"Ignav gecici baglanti hatasi ({attempt}/3):", repr(exc))
            if attempt < 3:
                time.sleep(attempt * 2)
        except Exception as exc:
            record_api_call(endpoint, False)
            print("Ignav beklenmeyen hata:", repr(exc))
            return None

    return None


def ignav_search(origin, destination, departure_date, return_date=None):
    payload = common_payload(origin, destination, departure_date)

    if return_date:
        payload["return_date"] = return_date
        endpoint = "/fares/round-trip"
    else:
        endpoint = "/fares/one-way"

    return ignav_post(endpoint, payload)


def get_booking_links(ignav_id):
    if not ignav_id:
        return None

    return ignav_post(
        "/fares/booking-links",
        {"ignav_id": ignav_id}
    )


# ============================================================
# UÃ‡UÅ / ITINERARY PARSING
# ============================================================

def first_segment(leg):
    if not isinstance(leg, dict):
        return {}
    segments = leg.get("segments") or []
    return segments[0] if segments else {}


def last_segment(leg):
    if not isinstance(leg, dict):
        return {}
    segments = leg.get("segments") or []
    return segments[-1] if segments else {}


def leg_segments(leg):
    return leg.get("segments") or [] if isinstance(leg, dict) else []


def segment_sig(segment):
    return "|".join([
        normalize(segment.get("departure_airport")),
        normalize(segment.get("arrival_airport")),
        normalize(
            segment.get("marketing_carrier_code")
            or segment.get("carrier_code")
        ),
        normalize(segment.get("flight_number")),
        normalize(
            segment.get("departure_time_local")
            or segment.get("departure_time")
            or segment.get("departure_datetime")
        ),
    ])


def flight_key(origin, destination, outbound, inbound=None):
    out_segments = leg_segments(outbound)
    in_segments = leg_segments(inbound)

    signatures = [segment_sig(s) for s in out_segments]
    signatures += ["RETURN"]
    signatures += [segment_sig(s) for s in in_segments]

    return "::".join([
        normalize(origin),
        normalize(destination),
        "||".join(signatures),
    ])


def baggage_info(itinerary):
    bags = itinerary.get("bags")
    if not isinstance(bags, dict):
        return "", None, None

    checked = bags.get("checked")
    carry = bags.get("carry_on")

    try:
        checked = int(checked) if checked is not None else None
    except (TypeError, ValueError):
        checked = None

    try:
        carry = int(carry) if carry is not None else None
    except (TypeError, ValueError):
        carry = None

    return json.dumps(bags, ensure_ascii=False), checked, carry


def itinerary_to_flight(data, itinerary, origin, destination, departure_date, return_date=None):
    price_info = itinerary.get("price") or {}
    outbound = itinerary.get("outbound") or {}
    inbound = itinerary.get("inbound") or None

    segments = leg_segments(outbound)
    first = first_segment(outbound)
    last = last_segment(outbound)

    airline = normalize(
        first.get("marketing_carrier_code")
        or outbound.get("carrier")
    )
    flight_number = normalize(first.get("flight_number"))

    duration = outbound.get("duration_minutes")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    stops = max(0, len(segments) - 1)
    baggage, checked, carry = baggage_info(itinerary)

    key = flight_key(origin, destination, outbound, inbound)

    return {
        "flight_key": key,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "trip_type": "round-trip" if return_date else "one-way",
        "price": safe_float(price_info.get("amount"), 0),
        "currency": normalize(price_info.get("currency")),
        "airline": airline,
        "flight_number": flight_number,
        "duration_minutes": duration,
        "baggage": baggage,
        "checked_bags": checked,
        "carry_on_bags": carry,
        "self_transfer": bool(itinerary.get("requires_self_transfer", False)),
        "price_status": normalize(price_info.get("status")),
        "ignav_id": itinerary.get("ignav_id"),
        "stops": stops,
        "departure_time": first.get("departure_time_local") or first.get("departure_time"),
        "arrival_time": last.get("arrival_time_local") or last.get("arrival_time"),
        "inbound": inbound,
    }


def extract_flights(data, origin, destination, departure_date, return_date=None):
    if not data or not isinstance(data, dict):
        return []

    result = []
    seen = set()

    for itinerary in data.get("itineraries", []) or []:
        if not isinstance(itinerary, dict):
            continue

        flight = itinerary_to_flight(
            data,
            itinerary,
            origin,
            destination,
            departure_date,
            return_date,
        )

        dedupe_key = (
            flight["flight_key"],
            flight["price"],
            flight["currency"],
            int(flight["self_transfer"]),
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        result.append(flight)

    return result


# ============================================================
# ROUTE / TARÄ°H ROTASYONU
# ============================================================

def build_routes():
    airports = settings.get("airports", {})
    priority_origins = airports.get("priority_origins", [])
    priority_destinations = airports.get("priority_destinations", [])
    domestic = airports.get("domestic_destinations", [])
    europe = airports.get("europe_destinations", [])

    routes = []

    def add(a, b):
        if a and b and a != b and (a, b) not in routes:
            routes.append((a, b))

    # Ã–nce SZF yÃ¶nleri.
    for destination in priority_destinations + domestic + europe:
        for origin in priority_origins:
            add(origin, destination)
            add(destination, origin)

    # Daha sonra listedeki diÄŸer havaalanlarÄ±.
    all_destinations = []
    for code in domestic + europe:
        if code not in all_destinations:
            all_destinations.append(code)

    if airports.get("domestic_enabled", True):
        for a in domestic:
            for b in domestic:
                if a != b:
                    add(a, b)

    if airports.get("europe_enabled", True):
        for a in priority_origins:
            for b in all_destinations:
                add(a, b)
                add(b, a)

    return routes


def route_batch(routes, batch_size):
    conn = get_connection()
    row = conn.execute(
        "SELECT route_index FROM radar_state WHERE id=1"
    ).fetchone()
    index = int(row[0] or 0) if row else 0

    selected = []
    total = len(routes)

    for i in range(min(batch_size, total)):
        selected.append(routes[(index + i) % total])

    new_index = (index + len(selected)) % total
    conn.execute(
        "UPDATE radar_state SET route_index=?, updated_at=? WHERE id=1",
        (new_index, utc_iso())
    )
    conn.commit()
    conn.close()
    return selected


def search_dates():
    radar = settings.get("radar", {})
    raw = radar.get("dates_days_ahead", [30, 60, 90])
    today = utc_now().date()
    dates = []

    for days in raw:
        try:
            d = today + timedelta(days=int(days))
            dates.append(d.isoformat())
        except (TypeError, ValueError):
            continue

    return dates


def round_trip_return_date(departure_date):
    radar = settings.get("radar", {})
    days = int(radar.get("round_trip_return_days", 7) or 7)
    departure = datetime.strptime(departure_date, "%Y-%m-%d").date()
    return (departure + timedelta(days=days)).isoformat()


# ============================================================
# DATABASE KAYIT
# ============================================================

def save_observation(flight):
    conn = get_connection()
    conn.execute("""
        INSERT INTO price_observations (
            flight_key, origin, destination, departure_date, return_date,
            trip_type, price, currency, airline, flight_number,
            duration_minutes, baggage, checked_bags, carry_on_bags,
            self_transfer, price_status, ignav_id, stops, observed_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        flight["flight_key"],
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
        flight["trip_type"],
        flight["price"],
        flight["currency"],
        flight["airline"],
        flight["flight_number"],
        flight["duration_minutes"],
        flight["baggage"],
        flight["checked_bags"],
        flight["carry_on_bags"],
        int(flight["self_transfer"]),
        flight["price_status"],
        flight["ignav_id"],
        flight["stops"],
        utc_iso(),
    ))
    conn.commit()
    conn.close()


def save_stable_price(flight):
    conn = get_connection()

    exists = conn.execute("""
        SELECT 1 FROM prices
        WHERE flight_key=?
          AND departure_date=?
          AND COALESCE(return_date,'')=COALESCE(?,'')
          AND price=?
          AND currency=?
        LIMIT 1
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["return_date"],
        flight["price"],
        flight["currency"],
    )).fetchone()

    if not exists:
        now = utc_iso()
        conn.execute("""
            INSERT INTO prices (
                flight_key, origin, destination, departure_date, return_date,
                trip_type, price, currency, airline, flight_number,
                duration_minutes, baggage, checked_bags, carry_on_bags,
                self_transfer, price_status, ignav_id, stops,
                seen_at, recorded_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            flight["flight_key"],
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["return_date"],
            flight["trip_type"],
            flight["price"],
            flight["currency"],
            flight["airline"],
            flight["flight_number"],
            flight["duration_minutes"],
            flight["baggage"],
            flight["checked_bags"],
            flight["carry_on_bags"],
            int(flight["self_transfer"]),
            flight["price_status"],
            flight["ignav_id"],
            flight["stops"],
            now,
            now,
        ))

    conn.commit()
    conn.close()


# ============================================================
# FÄ°YAT GEÃ‡MÄ°ÅÄ°
# ============================================================

def same_flight_history(flight, limit=40):
    conn = get_connection()
    rows = conn.execute("""
        SELECT price
        FROM price_observations
        WHERE flight_key=?
          AND departure_date=?
          AND COALESCE(return_date,'')=COALESCE(?,'')
          AND currency=?
        ORDER BY id DESC
        LIMIT ?
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["return_date"],
        flight["currency"],
        limit,
    )).fetchall()
    conn.close()
    return [float(r[0]) for r in rows if r[0] is not None]


def route_history(flight, limit=100):
    conn = get_connection()
    rows = conn.execute("""
        SELECT price
        FROM price_observations
        WHERE origin=?
          AND destination=?
          AND departure_date=?
          AND COALESCE(return_date,'')=COALESCE(?,'')
          AND currency=?
          AND price > 0
        ORDER BY id DESC
        LIMIT ?
    """, (
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
        flight["currency"],
        limit,
    )).fetchall()
    conn.close()
    return [float(r[0]) for r in rows if r[0] is not None]


def baseline_for(flight):
    same = same_flight_history(flight)
    if len(same) >= 3:
        return statistics.median(same), len(same), "same-flight"

    route = route_history(flight)
    if len(route) >= 5:
        return statistics.median(route), len(route), "route-date"

    return None, len(same) or len(route), "none"


def previous_price(flight):
    history = same_flight_history(flight, 5)
    return history[0] if history else None


# ============================================================
# SKORLAMA
# ============================================================

def historical_anomaly(flight):
    baseline, count, _ = baseline_for(flight)
    if not baseline or baseline <= 0:
        return 0

    ratio = flight["price"] / baseline
    if ratio <= 0.45:
        return 20
    if ratio <= 0.60:
        return 16
    if ratio <= 0.72:
        return 12
    if ratio <= 0.82:
        return 8
    if ratio <= 0.90:
        return 4
    return 0


def market_divergence(flight):
    baseline, count, _ = baseline_for(flight)
    if not baseline or baseline <= 0:
        return 0

    ratio = flight["price"] / baseline
    if ratio <= 0.50:
        return 25
    if ratio <= 0.65:
        return 20
    if ratio <= 0.75:
        return 15
    if ratio <= 0.85:
        return 10
    if ratio <= 0.92:
        return 5
    return 0


def sudden_drop(flight):
    previous = previous_price(flight)
    if not previous or previous <= 0:
        return 0

    drop = (previous - flight["price"]) / previous
    if drop >= 0.50:
        return 10
    if drop >= 0.35:
        return 8
    if drop >= 0.25:
        return 6
    if drop >= 0.15:
        return 4
    if drop >= 0.10:
        return 2
    return 0


def fare_anomaly(flight):
    points = 0
    if flight["self_transfer"]:
        points += 2

    checked = flight.get("checked_bags")
    if checked is not None and checked == 0:
        points += 1

    if flight.get("stops", 0) >= 2:
        points += 1

    return min(points, 5)


def currency_anomaly(flight):
    expected = market()
    if expected == "TR" and flight["currency"] not in ("TRY", ""):
        return 5
    return 0


def short_lived_persistence(flight):
    # Ä°lk gÃ¶zlemde puan yok. Ä°kinci gÃ¶zlemde kÃ¼Ã§Ã¼k puan.
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*)
        FROM price_observations
        WHERE flight_key=?
          AND departure_date=?
          AND COALESCE(return_date,'')=COALESCE(?,'')
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["return_date"],
    )).fetchone()
    conn.close()

    count = int(row[0] or 0)
    return 2 if count == 1 else 0


def calculate_score(flight, verification):
    components = {
        "historical_anomaly": historical_anomaly(flight),
        "market_divergence": market_divergence(flight),
        "sudden_drop": sudden_drop(flight),
        "source_disagreement": verification.get("source_disagreement_points", 0),
        # Ignav standart response'unda ayri tax/fee satirlari yok.
        # Bu nedenle verisiz tax anomaly puani uydurulmuyor.
        "tax_anomaly": 0,
        "currency_anomaly": currency_anomaly(flight),
        "fare_anomaly": fare_anomaly(flight),
        "short_lived_persistence": short_lived_persistence(flight),
        "source_reliability": verification.get("reliability_points", 0),
    }

    score = sum(components.values())

    # Unverified fiyatlar aday olabilir ama yuksek guven puanini
    # tek basina tasimamalidir.
    if flight.get("price_status") == "UNVERIFIED":
        score -= 3

    score = max(0, min(100, score))
    return score, components


def opportunity_score(flight):
    score = 0

    if flight.get("checked_bags") is not None and flight["checked_bags"] > 0:
        score += 15

    if not flight["self_transfer"]:
        score += 15

    if flight.get("stops", 0) == 0:
        score += 15
    elif flight.get("stops", 0) == 1:
        score += 8

    duration = flight.get("duration_minutes")
    if duration is not None:
        if duration <= 180:
            score += 15
        elif duration <= 300:
            score += 10
        elif duration <= 480:
            score += 5

    baseline, _, _ = baseline_for(flight)
    if baseline and baseline > 0:
        ratio = flight["price"] / baseline
        if ratio <= 0.60:
            score += 40
        elif ratio <= 0.75:
            score += 30
        elif ratio <= 0.85:
            score += 20
        elif ratio <= 0.95:
            score += 10

    return min(100, score)


# ============================================================
# DOÄRULAMA
# ============================================================

def matching_flight(flight, candidates):
    for candidate in candidates:
        if (
            candidate["flight_key"] == flight["flight_key"]
            and abs(candidate["price"] - flight["price"]) < 0.01
            and candidate["currency"] == flight["currency"]
            and candidate["self_transfer"] == flight["self_transfer"]
        ):
            return candidate
    return None


def booking_analysis(booking_data, flight):
    if not booking_data:
        return False, 0, None

    options = booking_data.get("booking_options") or []
    prices = []
    urls = []

    for option in options:
        for link in option.get("links", []) or []:
            price = safe_float((link.get("price") or {}).get("amount"))
            currency = normalize((link.get("price") or {}).get("currency"))
            if price is not None and currency == flight["currency"]:
                prices.append(price)

            url = link.get("url")
            if url:
                urls.append(url)

    if not prices:
        return False, 0, urls[0] if urls else None

    spread = max(prices) - min(prices)
    base = min(prices)
    points = 0

    if base > 0:
        pct = spread / base
        if pct >= 0.50:
            points = 10
        elif pct >= 0.30:
            points = 8
        elif pct >= 0.15:
            points = 5
        elif pct >= 0.08:
            points = 2

    return True, points, urls[0] if urls else None


def save_verification(flight, verified, booking_verified, disagreement, reason):
    conn = get_connection()
    now = utc_iso()

    conn.execute("""
        INSERT INTO verifications (
            flight_key, origin, destination, departure_date, return_date,
            trip_type, price, currency, airline, flight_number,
            duration_minutes, baggage, self_transfer, price_status,
            ignav_id, stops, verified, booking_verified,
            source_disagreement_points, verification_reason,
            checked_at, verification_time
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        flight["flight_key"],
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
        flight["trip_type"],
        flight["price"],
        flight["currency"],
        flight["airline"],
        flight["flight_number"],
        flight["duration_minutes"],
        flight["baggage"],
        int(flight["self_transfer"]),
        flight["price_status"],
        flight["ignav_id"],
        flight["stops"],
        int(verified),
        int(booking_verified),
        disagreement,
        reason,
        now,
        now,
    ))

    conn.commit()
    conn.close()


def verify_flight(flight, do_booking=False):
    result = {
        "verified": False,
        "booking_verified": False,
        "source_disagreement_points": 0,
        "reliability_points": 0,
        "booking_url": None,
        "reason": "",
    }

    # Ikinci Ignav sorgusu.
    data = ignav_search(
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
    )

    candidates = extract_flights(
        data,
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
    )

    matched = matching_flight(flight, candidates)

    if matched:
        result["verified"] = True
        result["reliability_points"] += 3
        result["reason"] = "Ayni ucus ve fiyat ikinci Ignav aramasinda tekrar bulundu."
    else:
        result["reason"] = "Ikinci Ignav aramasinda ayni ucus/fiyat dogrulanamadi."

    if do_booking and matched and matched.get("ignav_id"):
        booking = get_booking_links(matched["ignav_id"])
        booking_ok, disagreement, url = booking_analysis(booking, matched)

        result["booking_verified"] = booking_ok
        result["source_disagreement_points"] = disagreement
        result["booking_url"] = url

        if booking_ok:
            result["reliability_points"] += 2

    save_verification(
        flight,
        result["verified"],
        result["booking_verified"],
        result["source_disagreement_points"],
        result["reason"],
    )

    return result


# ============================================================
# ALERT
# ============================================================

def thresholds():
    return settings.get("scoring", {}).get("thresholds", {})


def alert_level(score):
    t = thresholds()

    if score >= int(t.get("high_confidence", 90)):
        return "KRITIK HATA FIYATI"
    if score >= int(t.get("candidate", 85)):
        return "HATA FIYATI ADAYI"
    if score >= int(t.get("suspicious", 70)):
        return "SUPHELI FIYAT"
    if score >= int(t.get("good_deal", 50)):
        return "IYI FIRSAT"
    return "NORMAL"


def already_alerted(flight):
    hours = float(
        settings.get("radar", {})
        .get("duplicate_alert_window_hours", 24)
        or 24
    )

    since = (utc_now() - timedelta(hours=hours)).isoformat()
    conn = get_connection()
    row = conn.execute("""
        SELECT 1 FROM alerts
        WHERE flight_key=?
          AND departure_date=?
          AND COALESCE(return_date,'')=COALESCE(?,'')
          AND price=?
          AND currency=?
          AND sent_at >= ?
        LIMIT 1
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["return_date"],
        flight["price"],
        flight["currency"],
        since,
    )).fetchone()
    conn.close()
    return row is not None


def telegram_configured():
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def telegram_send(message):
    if not settings.get("alerts", {}).get("telegram_enabled", True):
        print("Telegram devre disi.")
        return False
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram BASARISIZ: secret eksik.")
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message[:4090], "disable_web_page_preview": True},
            timeout=30,
        )
        if response.status_code == 200:
            print("Telegram: mesaj gonderildi.")
            return True
        print("Telegram HTTP hata:", response.status_code, response.text[:500])
        return False
    except Exception as exc:
        print("Telegram hata:", repr(exc))
        return False


def telegram_test():
    if not telegram_configured():
        print("TELEGRAM TEST: BASARISIZ - secret eksik.")
        return False
    print("TELEGRAM TEST: secretlar mevcut.")
    ok = telegram_send("ğŸš¨ UÃ‡UÅ HATA FÄ°YATI RADARI V3.4\nTelegram baÄŸlantÄ± testi baÅŸarÄ±lÄ±.\nBildirim kanalÄ± aktif.")
    print("TELEGRAM TEST SONUCU:", "BASARILI" if ok else "BASARISIZ")
    return ok


def make_alert_message(flight, score, opportunity, verification):
    level = alert_level(score)
    trip = "Gidis-Donus" if flight["return_date"] else "Tek Yon"

    baseline, sample, baseline_type = baseline_for(flight)
    drop = previous_price(flight)

    lines = [
        f"âœˆï¸ {level}",
        "",
        f"ğŸ›« {flight['origin']} â†’ {flight['destination']}",
        f"ğŸ“… Gidis: {flight['departure_date']}",
    ]

    if flight["return_date"]:
        lines.append(f"ğŸ”„ Donus: {flight['return_date']}")

    lines += [
        f"ğŸ§³ {trip}",
        f"ğŸ’° Fiyat: {flight['price']:.0f} {flight['currency']}",
        f"ğŸ·ï¸ Havayolu/UÃ§uÅŸ: {flight['airline']} {flight['flight_number']}",
        f"ğŸ›‘ Aktarma: {flight['stops']}",
        f"ğŸ§³ Bagaj: {flight['checked_bags'] if flight['checked_bags'] is not None else '?'} checked",
        f"ğŸ” Error Score: {score}/100",
        f"â­ Opportunity Score: {opportunity}/100",
        f"âœ… Dogrulama: {'EVET' if verification['verified'] else 'HAYIR'}",
    ]

    if baseline:
        lines.append(
            f"ğŸ“Š Baz fiyat: {baseline:.0f} {flight['currency']} "
            f"({sample} gozlem, {baseline_type})"
        )

    if drop:
        lines.append(f"ğŸ“‰ Son bilinen fiyat: {drop:.0f} {flight['currency']}")

    if verification.get("booking_verified"):
        lines.append("ğŸ”— Booking provider kontrolu: VAR")

    if verification.get("booking_url"):
        lines.append(f"ğŸ”— {verification['booking_url']}")

    lines += [
        "",
        "NEDEN DIKKAT CEKTI?",
        f"â€¢ Dogrulama: {'basarili' if verification['verified'] else 'basarisiz'}",
        f"â€¢ Kaynak farki puani: {verification.get('source_disagreement_points', 0)}",
        f"â€¢ Fiyat durumu: {flight.get('price_status', '')}",
        "",
        "Bu alarm otomatik analizdir; satin almadan once fiyat ve kosullari son kez kontrol edin.",
    ]

    return "\n".join(lines)


def create_and_send_alert(flight, score, opportunity, verification):
    alerts_cfg = settings.get("alerts", {})
    minimum = int(alerts_cfg.get("minimum_score", 50) or 50)

    if score < minimum:
        return False

    if not verification.get("verified"):
        print("Alarm atlandi: fiyat dogrulanmadi.")
        return False

    if already_alerted(flight):
        print("Alarm atlandi: ayni alarm yakin zamanda gonderilmis.")
        return False

    message = make_alert_message(
        flight,
        score,
        opportunity,
        verification,
    )

    sent = telegram_send(message)

    if sent:
        conn = get_connection()
        conn.execute("""
            INSERT INTO alerts (
                flight_key, origin, destination, departure_date, return_date,
                trip_type, price, currency, airline, flight_number,
                score, opportunity_score, alert_level, booking_url, sent_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            flight["flight_key"],
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["return_date"],
            flight["trip_type"],
            flight["price"],
            flight["currency"],
            flight["airline"],
            flight["flight_number"],
            score,
            opportunity,
            alert_level(score),
            verification.get("booking_url"),
            utc_iso(),
        ))
        conn.commit()
        conn.close()

    return sent


# ============================================================
# ANA RADAR
# ============================================================

def main():
    print("=" * 60)
    print("UÃ‡UÅ HATA FÄ°YATI RADARI V3.5")
    print("=" * 60)

    init_db()

    telegram_ready = telegram_configured()
    print("Telegram yapilandirilmis:", "EVET" if telegram_ready else "HAYIR")
    telegram_test_result = None
    if os.getenv("TELEGRAM_TEST", "0") == "1":
        telegram_test_result = telegram_test()

    routes = build_routes()
    batch_size = int(
        settings.get("radar", {}).get("routes_per_run", 10)
        or 10
    )

    selected_routes = route_batch(routes, batch_size)
    dates = search_dates()

    trip_cfg = settings.get("trip", {})
    one_way_enabled = trip_cfg.get("one_way", True)
    round_trip_enabled = trip_cfg.get("round_trip", True)

    print(f"Toplam rota: {len(routes)}")
    print(f"Bu calisma: {len(selected_routes)} rota")
    print(f"Tarihler: {', '.join(dates)}")
    print(f"Market: {market()}")
    print(f"Tek yon: {one_way_enabled} | Gidis-donus: {round_trip_enabled}")

    searches = 0
    flights_found = 0
    verified = 0
    booking_checks = 0
    alerts = 0

    verification_minimum = int(
        settings.get("radar", {})
        .get("verification", {})
        .get("minimum_score_for_verification", 30)
        or 30
    )

    booking_minimum = int(
        settings.get("radar", {})
        .get("verification", {})
        .get("booking_minimum_score", 70)
        or 70
    )

    for origin, destination in selected_routes:
        for departure_date in dates:

            search_jobs = []

            if one_way_enabled:
                search_jobs.append(("one-way", None))

            if round_trip_enabled:
                search_jobs.append(("round-trip", round_trip_return_date(departure_date)))

            for trip_type, return_date in search_jobs:
                print(
                    f"\nARANIYOR: {origin}->{destination} "
                    f"{departure_date}"
                    + (f" / {return_date}" if return_date else "")
                )

                data = ignav_search(
                    origin,
                    destination,
                    departure_date,
                    return_date,
                )
                searches += 1

                flights = extract_flights(
                    data,
                    origin,
                    destination,
                    departure_date,
                    return_date,
                )

                flights_found += len(flights)
                print(f"Bulunan benzersiz itinerary: {len(flights)}")

                for flight in flights:
                    # Skorlamadan once kaydetmiyoruz.
                    # Boylece mevcut gozlem kendi baseline'ini bozmaz.
                    preliminary_verification = {
                        "source_disagreement_points": 0,
                        "reliability_points": 0,
                    }

                    preliminary_score, _ = calculate_score(
                        flight,
                        preliminary_verification,
                    )

                    verification = {
                        "verified": False,
                        "booking_verified": False,
                        "source_disagreement_points": 0,
                        "reliability_points": 0,
                        "booking_url": None,
                        "reason": "",
                    }

                    if (
                        settings.get("radar", {})
                        .get("verification", {})
                        .get("enabled", True)
                        and preliminary_score >= verification_minimum
                    ):
                        do_booking = preliminary_score >= booking_minimum
                        if do_booking:
                            booking_checks += 1

                        verification = verify_flight(
                            flight,
                            do_booking=do_booking,
                        )

                        if verification["verified"]:
                            verified += 1

                    score, components = calculate_score(
                        flight,
                        verification,
                    )

                    opportunity = opportunity_score(flight)

                    print(
                        f"SKOR {score:02d} | FIRSAT {opportunity:02d} | "
                        f"{flight['origin']}->{flight['destination']} | "
                        f"{flight['departure_date']} | "
                        f"{flight['price']:.0f} {flight['currency']} | "
                        f"{flight['airline']} {flight['flight_number']} | "
                        f"{'DOGRULANDI' if verification['verified'] else 'bekliyor'}"
                    )

                    save_observation(flight)
                    save_stable_price(flight)

                    if create_and_send_alert(
                        flight,
                        score,
                        opportunity,
                        verification,
                    ):
                        alerts += 1

    print("\n" + "=" * 60)
    print("V3.5 CALISMA OZETI")
    print("=" * 60)
    print(f"API aramasi: {searches}")
    print(f"Ucus/itinerary: {flights_found}")
    print(f"Dogrulama: {verified}")
    print(f"Booking kontrolu: {booking_checks}")
    print(f"Telegram alarmi: {alerts}")
    print("IGNAV faturalandirma durumu:", "GEREKLI - 402" if IGNAV_BILLING_BLOCKED else "NORMAL")
    if telegram_test_result is not None:
        print("Telegram test:", "BASARILI" if telegram_test_result else "BASARISIZ")
    print("=" * 60)


if __name__ == "__main__":
    main()
