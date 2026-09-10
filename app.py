
import os
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone

import requests


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.getenv("DB_FILE", os.path.join(BASE_DIR, "prices.db"))
SETTINGS_FILE = os.getenv(
    "SETTINGS_FILE",
    os.path.join(BASE_DIR, "config", "settings.json"),
)
IGNAV_BASE = "https://ignav.com/api"

IGNAV_BILLING_BLOCKED = False
SESSION = requests.Session()


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


def safe_int(value, default=None):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize(value):
    return str(value or "").strip().upper()


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        raise FileNotFoundError(
            f"settings.json bulunamadi: {SETTINGS_FILE}"
        )
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


# ============================================================
# DATABASE / MIGRATION
# ============================================================

def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def table_exists(conn, table):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def columns(conn, table):
    if not table_exists(conn, table):
        return set()
    return {
        row[1]
        for row in conn.execute(f'PRAGMA table_info("{table}")')
    }


def add_column(conn, table, name, definition):
    if name not in columns(conn, table):
        conn.execute(
            f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}'
        )


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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            used_at TEXT NOT NULL,
            endpoint TEXT,
            success INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0
        )
    """)

    if cur.execute(
        "SELECT 1 FROM radar_state WHERE id=1"
    ).fetchone() is None:
        cur.execute(
            "INSERT INTO radar_state(id, route_index, updated_at) VALUES(1,0,?)",
            (utc_iso(),),
        )

    # Eski V2/V3 verilerini koru ve eksik alanlari ekle.
    for name, definition in {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "checked_bags": "INTEGER",
        "carry_on_bags": "INTEGER",
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "seen_at": "TEXT",
        "recorded_at": "TEXT",
    }.items():
        add_column(conn, "prices", name, definition)

    for name, definition in {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "checked_bags": "INTEGER",
        "carry_on_bags": "INTEGER",
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "observed_at": "TEXT",
    }.items():
        add_column(conn, "price_observations", name, definition)

    for name, definition in {
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
    }.items():
        add_column(conn, "verifications", name, definition)

    for name, definition in {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "opportunity_score": "REAL",
        "booking_url": "TEXT",
        "sent_at": "TEXT",
    }.items():
        add_column(conn, "alerts", name, definition)

    now = utc_iso()

    cur.execute(
        "UPDATE prices SET seen_at=? WHERE seen_at IS NULL",
        (now,),
    )
    cur.execute("""
        UPDATE prices
        SET recorded_at=seen_at
        WHERE recorded_at IS NULL AND seen_at IS NOT NULL
    """)
    cur.execute("""
        UPDATE price_observations
        SET observed_at=?
        WHERE observed_at IS NULL
    """, (now,))
    cur.execute("""
        UPDATE verifications
        SET checked_at=?
        WHERE checked_at IS NULL
    """, (now,))
    cur.execute("""
        UPDATE verifications
        SET verification_time=checked_at
        WHERE verification_time IS NULL AND checked_at IS NOT NULL
    """)
    cur.execute("""
        UPDATE alerts
        SET sent_at=?
        WHERE sent_at IS NULL
    """, (now,))

    conn.commit()
    conn.close()
    print("Veritabani kontrolu tamamlandi.")


# ============================================================
# AYARLAR
# ============================================================

def system_cfg():
    return settings.get("system", {})


def radar_cfg():
    return settings.get("radar", {})


def verification_cfg():
    return radar_cfg().get("verification", {})


def alert_cfg():
    return settings.get("alerts", {})


def scoring_cfg():
    return settings.get("scoring", {})


def market():
    # Ignav market 2 harfli ulke kodudur.
    return normalize(system_cfg().get("market", "TR")) or "TR"


def cabin_class():
    cabin = settings.get("cabin", {})
    if cabin.get("business", False):
        return "business"
    if cabin.get("premium_economy", False):
        return "premium_economy"
    return "economy"


def api_budget():
    system = system_cfg()
    return {
        "allowed": bool(system.get("paid_api_allowed", False)),
        "daily_limit": safe_int(system.get("daily_query_limit"), 0) or 0,
        "monthly_budget_usd": (
            safe_float(system.get("monthly_api_budget_usd"), 0) or 0
        ),
    }


# ============================================================
# API KULLANIM / BUTCE
# ============================================================

def count_queries_since(conn, start_iso, successful_only=False):
    if successful_only:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM api_usage
            WHERE used_at >= ? AND success=1
            """,
            (start_iso,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) FROM api_usage WHERE used_at >= ?",
            (start_iso,),
        ).fetchone()
    return int(row[0] or 0)


def api_usage_totals():
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN success=1 THEN 1 ELSE 0 END),0)
                AS successful,
            COALESCE(SUM(estimated_cost_usd),0) AS cost
        FROM api_usage
    """).fetchone()
    conn.close()
    return int(row[0] or 0), int(row[1] or 0), float(row[2] or 0)


def api_call_allowed():
    budget = api_budget()

    conn = get_connection()
    now = utc_now()
    day_start = now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    month_start = now.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    # Basarisiz istekler IGNAV tarafinda ucretsizdir.
    daily_success = count_queries_since(
        conn, day_start, successful_only=True
    )
    monthly_success = count_queries_since(
        conn, month_start, successful_only=True
    )
    total_success = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE success=1"
    ).fetchone()[0]

    monthly_cost = conn.execute(
        """
        SELECT COALESCE(SUM(estimated_cost_usd),0)
        FROM api_usage
        WHERE used_at >= ?
        """,
        (month_start,),
    ).fetchone()[0]

    conn.close()

    if not budget["allowed"]:
        # IGNAV'in 1.000 ucretsiz basarili istek kotasini esas al.
        if int(total_success or 0) >= 1000:
            print(
                "API ucretsiz kota doldu: 1000 basarili istek. "
                "Paid API kapali."
            )
            return False

    if budget["daily_limit"] > 0:
        if daily_success >= budget["daily_limit"]:
            print("API gunluk sorgu limiti doldu.")
            return False

    if budget["monthly_budget_usd"] > 0:
        if float(monthly_cost or 0) >= budget["monthly_budget_usd"]:
            print("API aylik butce limiti doldu.")
            return False

    return True


def record_api_call(endpoint, success):
    conn = get_connection()

    successful_before = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE success=1"
    ).fetchone()[0]

    # IGNAV: ilk 1000 basarili istek ucretsiz,
    # sonrasinda $2 / 1000 basarili istek.
    cost = 0.0
    if success and successful_before >= 1000:
        cost = 2.0 / 1000.0

    conn.execute(
        """
        INSERT INTO api_usage(
            used_at, endpoint, success, estimated_cost_usd
        ) VALUES(?,?,?,?)
        """,
        (
            utc_iso(),
            endpoint,
            1 if success else 0,
            cost,
        ),
    )
    conn.commit()
    conn.close()


# ============================================================
# IGNAV
# ============================================================

def ignav_headers():
    key = os.getenv("IGNAV_API_KEY", "").strip()
    if not key:
        return None
    return {
        "X-Api-Key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def common_payload(origin, destination, departure_date):
    passengers = settings.get("passengers", {})
    connections = settings.get("connections", {})
    price_cfg = settings.get("price", {})
    airlines = settings.get("airlines", {})
    baggage = settings.get("baggage", {})

    adults = max(1, safe_int(passengers.get("adults"), 1) or 1)
    children = max(0, safe_int(passengers.get("children"), 0) or 0)
    infants = max(0, safe_int(passengers.get("infants"), 0) or 0)

    max_connections = safe_int(
        connections.get("max_connections"), 1
    )
    if max_connections is None:
        max_connections = 1
    max_connections = max(0, min(2, max_connections))

    payload = {
        "origin": normalize(origin),
        "destination": normalize(destination),
        "departure_date": departure_date,
        "adults": adults,
        "children": children,
        "infants_in_seat": 0,
        "infants_on_lap": infants,
        "cabin_class": cabin_class(),
        "max_stops": max_connections,
        "allow_self_transfer": bool(
            connections.get("self_transfer", False)
        ),
        "market": market(),
    }

    maximum = safe_float(price_cfg.get("maximum_try"), 0) or 0
    if maximum > 0:
        payload["max_price"] = int(maximum)

    disabled = airlines.get("disabled", [])
    if isinstance(disabled, list):
        disabled = [
            normalize(code) for code in disabled
            if normalize(code)
        ]
        if disabled:
            payload["airlines_exclude"] = disabled

    # Bagaji zorunlu filtre yapmiyoruz; sadece kullanici ayarini
    # skorlama/mesajlama tarafinda kullaniyoruz. Boylece ucuz
    # hata fiyatlarini istemeden API tarafinda elemiyoruz.
    _ = baggage

    return payload


def ignav_post(endpoint, payload, retry=True):
    global IGNAV_BILLING_BLOCKED

    headers = ignav_headers()
    if not headers:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    if IGNAV_BILLING_BLOCKED:
        print("IGNAV 402: faturalandirma gerekli; API durduruldu.")
        return None

    if not api_call_allowed():
        return None

    attempts = 3 if retry else 1

    for attempt in range(1, attempts + 1):
        try:
            response = SESSION.post(
                f"{IGNAV_BASE}{endpoint}",
                headers=headers,
                json=payload,
                timeout=(15, 60),
            )

            ok = response.status_code == 200
            record_api_call(endpoint, ok)

            print(
                f"IGNAV {endpoint}: HTTP {response.status_code}"
            )

            if ok:
                try:
                    data = response.json()
                except ValueError as exc:
                    print("IGNAV JSON hatasi:", repr(exc))
                    return None

                if not isinstance(data, dict):
                    print("IGNAV gecersiz JSON yapisi.")
                    return None

                return data

            if response.status_code == 402:
                IGNAV_BILLING_BLOCKED = True
                print(
                    "IGNAV 402: billing_required. "
                    "Bu calisma icin API durduruldu."
                )
                print("IGNAV cevabi:", response.text[:500])
                return None

            print("IGNAV cevabi:", response.text[:500])

            # Kalici 4xx hatalar tekrar edilmez.
            if response.status_code not in (
                408, 429, 500, 502, 503, 504
            ):
                return None

            if attempt < attempts:
                time.sleep(attempt * 2)

        except (requests.Timeout, requests.ConnectionError) as exc:
            # Basarisiz istek kaydedilir ama ucretsiz kota hesabina
            # basarili istek olarak girmez.
            record_api_call(endpoint, False)
            print(
                f"IGNAV gecici baglanti hatasi "
                f"({attempt}/{attempts}): {repr(exc)}"
            )
            if attempt < attempts:
                time.sleep(attempt * 2)

        except requests.RequestException as exc:
            record_api_call(endpoint, False)
            print("IGNAV request hatasi:", repr(exc))
            return None

        except Exception as exc:
            record_api_call(endpoint, False)
            print("IGNAV beklenmeyen hata:", repr(exc))
            return None

    return None


def ignav_search(
    origin,
    destination,
    departure_date,
    return_date=None,
):
    payload = common_payload(
        origin, destination, departure_date
    )

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
        {"ignav_id": ignav_id},
        retry=False,
    )


# ============================================================
# ITINERARY PARSING
# ============================================================

def leg_segments(leg):
    if not isinstance(leg, dict):
        return []
    segments = leg.get("segments")
    return segments if isinstance(segments, list) else []


def first_segment(leg):
    segments = leg_segments(leg)
    return segments[0] if segments else {}


def last_segment(leg):
    segments = leg_segments(leg)
    return segments[-1] if segments else {}


def segment_sig(segment):
    if not isinstance(segment, dict):
        return ""

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
    out_signatures = [
        segment_sig(s) for s in leg_segments(outbound)
    ]
    in_signatures = [
        segment_sig(s) for s in leg_segments(inbound)
    ]

    parts = [
        normalize(origin),
        normalize(destination),
        "OUT:" + "||".join(out_signatures),
    ]

    if in_signatures:
        parts.append("IN:" + "||".join(in_signatures))
    else:
        parts.append("IN:")

    return "::".join(parts)


def baggage_info(itinerary):
    bags = itinerary.get("bags")
    if not isinstance(bags, dict):
        bags = itinerary.get("baggage")
    if not isinstance(bags, dict):
        bags = {}

    checked = (
        bags.get("checked")
        if "checked" in bags else
        bags.get("checked_bags")
    )
    carry = (
        bags.get("carry_on")
        if "carry_on" in bags else
        bags.get("carry_on_bags")
    )

    checked = safe_int(checked)
    carry = safe_int(carry)

    return (
        json.dumps(bags, ensure_ascii=False)
        if bags else "",
        checked,
        carry,
    )


def itinerary_to_flight(
    itinerary,
    origin,
    destination,
    departure_date,
    return_date=None,
):
    if not isinstance(itinerary, dict):
        return None

    price_info = itinerary.get("price") or {}
    outbound = itinerary.get("outbound") or {}
    inbound = itinerary.get("inbound") or None

    if not isinstance(outbound, dict):
        return None

    amount = safe_float(price_info.get("amount"))
    if amount is None or amount <= 0:
        return None

    first = first_segment(outbound)
    last = last_segment(outbound)

    airline = normalize(
        first.get("marketing_carrier_code")
        or outbound.get("carrier")
        or first.get("operating_carrier_code")
    )
    flight_number = normalize(first.get("flight_number"))

    duration = safe_int(outbound.get("duration_minutes"))

    segments = leg_segments(outbound)
    stops = max(0, len(segments) - 1)

    baggage, checked, carry = baggage_info(itinerary)

    currency = normalize(
        price_info.get("currency")
        or system_cfg().get("currency")
    )

    return {
        "flight_key": flight_key(
            origin, destination, outbound, inbound
        ),
        "origin": normalize(origin),
        "destination": normalize(destination),
        "departure_date": departure_date,
        "return_date": return_date,
        "trip_type": (
            "round-trip" if return_date else "one-way"
        ),
        "price": amount,
        "currency": currency,
        "airline": airline,
        "flight_number": flight_number,
        "duration_minutes": duration,
        "baggage": baggage,
        "checked_bags": checked,
        "carry_on_bags": carry,
        "self_transfer": bool(
            itinerary.get("requires_self_transfer", False)
        ),
        "price_status": normalize(
            price_info.get("status")
        ),
        "ignav_id": str(
            itinerary.get("ignav_id") or ""
        ).strip() or None,
        "stops": stops,
        "departure_time": (
            first.get("departure_time_local")
            or first.get("departure_time")
            or first.get("departure_datetime")
        ),
        "arrival_time": (
            last.get("arrival_time_local")
            or last.get("arrival_time")
            or last.get("arrival_datetime")
        ),
        "inbound": inbound,
    }


def extract_flights(
    data,
    origin,
    destination,
    departure_date,
    return_date=None,
):
    if not isinstance(data, dict):
        return []

    itineraries = data.get("itineraries")
    if not isinstance(itineraries, list):
        return []

    result = []
    seen = set()

    for itinerary in itineraries:
        flight = itinerary_to_flight(
            itinerary,
            origin,
            destination,
            departure_date,
            return_date,
        )
        if not flight:
            continue

        dedupe_key = (
            flight["flight_key"],
            round(flight["price"], 2),
            flight["currency"],
            int(flight["self_transfer"]),
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        result.append(flight)

    return result


# ============================================================
# ROUTE / TARIH
# ============================================================

def build_routes():
    airports = settings.get("airports", {})

    priority_origins = [
        normalize(x)
        for x in airports.get("priority_origins", [])
        if normalize(x)
    ]
    priority_destinations = [
        normalize(x)
        for x in airports.get("priority_destinations", [])
        if normalize(x)
    ]
    domestic = [
        normalize(x)
        for x in airports.get("domestic_destinations", [])
        if normalize(x)
    ]
    europe = [
        normalize(x)
        for x in airports.get("europe_destinations", [])
        if normalize(x)
    ]

    routes = []
    route_set = set()

    def add(a, b):
        a = normalize(a)
        b = normalize(b)
        if not a or not b or a == b:
            return
        pair = (a, b)
        if pair not in route_set:
            route_set.add(pair)
            routes.append(pair)

    # Oncelik verilen cikislar -> oncelik hedefleri.
    for origin in priority_origins:
        for destination in priority_destinations:
            add(origin, destination)
            add(destination, origin)

    # Oncelik verilen cikislar -> tum hedefler.
    if airports.get("domestic_enabled", True):
        for origin in priority_origins:
            for destination in domestic:
                add(origin, destination)
                add(destination, origin)

    if airports.get("europe_enabled", True):
        for origin in priority_origins:
            for destination in europe:
                add(origin, destination)
                add(destination, origin)

    # Tum yurt ici rotalar.
    if airports.get("domestic_enabled", True):
        for a in domestic:
            for b in domestic:
                if a != b:
                    add(a, b)

    return routes


def route_batch(routes, batch_size):
    if not routes:
        return []

    batch_size = max(1, min(batch_size, len(routes)))

    conn = get_connection()
    row = conn.execute(
        "SELECT route_index FROM radar_state WHERE id=1"
    ).fetchone()

    index = safe_int(row[0], 0) if row else 0
    index = max(0, index or 0) % len(routes)

    selected = [
        routes[(index + i) % len(routes)]
        for i in range(batch_size)
    ]

    new_index = (index + len(selected)) % len(routes)

    conn.execute(
        """
        UPDATE radar_state
        SET route_index=?, updated_at=?
        WHERE id=1
        """,
        (new_index, utc_iso()),
    )
    conn.commit()
    conn.close()

    return selected


def search_dates():
    raw = radar_cfg().get(
        "dates_days_ahead",
        [30, 60, 90],
    )

    if not isinstance(raw, list):
        raw = [30, 60, 90]

    today = utc_now().date()
    dates = []

    for days in raw:
        days_int = safe_int(days)
        if days_int is None:
            continue
        d = today + timedelta(days=days_int)
        dates.append(d.isoformat())

    return sorted(set(dates))


def round_trip_return_date(departure_date):
    days = safe_int(
        radar_cfg().get("round_trip_return_days"),
        7,
    )
    days = 7 if days is None else max(1, days)

    departure = datetime.strptime(
        departure_date, "%Y-%m-%d"
    ).date()

    return (
        departure + timedelta(days=days)
    ).isoformat()


# ============================================================
# DATABASE KAYIT
# ============================================================

def save_observation(flight):
    conn = get_connection()
    conn.execute("""
        INSERT INTO price_observations (
            flight_key, origin, destination, departure_date,
            return_date, trip_type, price, currency, airline,
            flight_number, duration_minutes, baggage,
            checked_bags, carry_on_bags, self_transfer,
            price_status, ignav_id, stops, observed_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
        SELECT 1
        FROM prices
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
                flight_key, origin, destination, departure_date,
                return_date, trip_type, price, currency, airline,
                flight_number, duration_minutes, baggage,
                checked_bags, carry_on_bags, self_transfer,
                price_status, ignav_id, stops, seen_at, recorded_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
# GECMIS FIYAT
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
          AND price > 0
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

    return [
        float(row[0])
        for row in rows
        if row[0] is not None
    ]


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

    return [
        float(row[0])
        for row in rows
        if row[0] is not None
    ]


def baseline_for(flight):
    same = same_flight_history(flight)

    # Mevcut gozlem henuz kaydedilmedigi icin bu liste
    # onceki gozlemleri temsil eder.
    if len(same) >= 3:
        return (
            statistics.median(same),
            len(same),
            "same-flight",
        )

    route = route_history(flight)

    if len(route) >= 5:
        return (
            statistics.median(route),
            len(route),
            "route-date",
        )

    return (
        None,
        max(len(same), len(route)),
        "none",
    )


def previous_price(flight):
    history = same_flight_history(flight, 5)
    return history[0] if history else None


# ============================================================
# SKORLAMA
# ============================================================

def weight(name, default):
    return safe_int(
        scoring_cfg()
        .get("error_score", {})
        .get(name, default),
        default,
    ) or default


def historical_anomaly(flight):
    baseline, _, _ = baseline_for(flight)
    if not baseline or baseline <= 0:
        return 0

    ratio = flight["price"] / baseline
    maximum = weight("historical_anomaly", 20)

    if ratio <= 0.45:
        return maximum
    if ratio <= 0.60:
        return round(maximum * 0.80)
    if ratio <= 0.72:
        return round(maximum * 0.60)
    if ratio <= 0.82:
        return round(maximum * 0.40)
    if ratio <= 0.90:
        return round(maximum * 0.20)
    return 0


def market_divergence(flight):
    baseline, _, _ = baseline_for(flight)
    if not baseline or baseline <= 0:
        return 0

    ratio = flight["price"] / baseline
    maximum = weight("market_divergence", 25)

    if ratio <= 0.50:
        return maximum
    if ratio <= 0.65:
        return round(maximum * 0.80)
    if ratio <= 0.75:
        return round(maximum * 0.60)
    if ratio <= 0.85:
        return round(maximum * 0.40)
    if ratio <= 0.92:
        return round(maximum * 0.20)
    return 0


def sudden_drop(flight):
    previous = previous_price(flight)
    if not previous or previous <= 0:
        return 0

    drop = (
        (previous - flight["price"]) / previous
    )
    maximum = weight("sudden_drop", 10)

    if drop >= 0.50:
        return maximum
    if drop >= 0.35:
        return round(maximum * 0.80)
    if drop >= 0.25:
        return round(maximum * 0.60)
    if drop >= 0.15:
        return round(maximum * 0.40)
    if drop >= 0.10:
        return round(maximum * 0.20)
    return 0


def fare_anomaly(flight):
    points = 0
    maximum = weight("fare_anomaly", 5)

    if flight["self_transfer"]:
        points += 2

    checked = flight.get("checked_bags")
    if checked == 0:
        points += 1

    if flight.get("stops", 0) >= 2:
        points += 1

    return min(points, maximum)


def currency_anomaly(flight):
    # Market TR oldugu halde farkli para birimi gelmesi dikkat
    # puanidir; fakat bunu tek basina hata fiyati kabul etmiyoruz.
    if market() == "TR" and flight["currency"] not in ("TRY", ""):
        return weight("currency_anomaly", 5)
    return 0


def short_lived_persistence(flight):
    # Skorlamadan once observation kaydedilmedigi icin:
    # 0 = ilk gozlem, 1 = onceki gozlem var.
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
    maximum = weight("short_lived_persistence", 5)

    if count >= 1:
        return maximum
    return 0


def calculate_score(flight, verification=None):
    verification = verification or {}

    components = {
        "historical_anomaly": historical_anomaly(flight),
        "market_divergence": market_divergence(flight),
        "sudden_drop": sudden_drop(flight),
        "source_disagreement": min(
            weight("source_disagreement", 10),
            safe_int(
                verification.get(
                    "source_disagreement_points", 0
                ),
                0,
            ) or 0,
        ),
        "tax_anomaly": 0,
        "currency_anomaly": currency_anomaly(flight),
        "fare_anomaly": fare_anomaly(flight),
        "short_lived_persistence": short_lived_persistence(
            flight
        ),
        "source_reliability": min(
            weight("source_reliability", 5),
            safe_int(
                verification.get("reliability_points", 0),
                0,
            ) or 0,
        ),
    }

    score = sum(components.values())

    status = normalize(flight.get("price_status"))
    if status == "UNVERIFIED":
        score -= 3

    return max(0, min(100, score)), components


def opportunity_score(flight):
    score = 0

    if (
        flight.get("checked_bags") is not None
        and flight["checked_bags"] > 0
    ):
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
# DOGRULAMA
# ============================================================

def matching_flight(flight, candidates):
    for candidate in candidates:
        if candidate["flight_key"] != flight["flight_key"]:
            continue
        if abs(
            candidate["price"] - flight["price"]
        ) >= 0.01:
            continue
        if candidate["currency"] != flight["currency"]:
            continue
        if (
            candidate["self_transfer"]
            != flight["self_transfer"]
        ):
            continue
        return candidate

    return None


def booking_analysis(booking_data, flight):
    if not isinstance(booking_data, dict):
        return False, 0, None

    options = booking_data.get("booking_options")
    if not isinstance(options, list):
        return False, 0, None

    prices = []
    urls = []

    for option in options:
        if not isinstance(option, dict):
            continue

        links = option.get("links")
        if not isinstance(links, list):
            continue

        for link in links:
            if not isinstance(link, dict):
                continue

            price_info = link.get("price") or {}
            price = safe_float(
                price_info.get("amount")
            )
            currency = normalize(
                price_info.get("currency")
            )

            if (
                price is not None
                and currency == flight["currency"]
            ):
                prices.append(price)

            url = str(link.get("url") or "").strip()
            if url:
                urls.append(url)

    if not prices:
        return (
            False,
            0,
            urls[0] if urls else None,
        )

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

    return (
        True,
        points,
        urls[0] if urls else None,
    )


def save_verification(
    flight,
    verified,
    booking_verified,
    disagreement,
    reason,
):
    conn = get_connection()
    now = utc_iso()

    conn.execute("""
        INSERT INTO verifications (
            flight_key, origin, destination, departure_date,
            return_date, trip_type, price, currency, airline,
            flight_number, duration_minutes, baggage,
            self_transfer, price_status, ignav_id, stops,
            verified, booking_verified,
            source_disagreement_points, verification_reason,
            checked_at, verification_time
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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

    # Ayni aramayi ikinci kez yapmak yerine, sadece adayin
    # mevcut ignav_id'sini booking-links ile dogrulama secenegine
    # sahibiz. Fakat booking kontrolu istenmiyorsa, arama sonucunun
    # kendi price.status bilgisine guvenilir.
    status = normalize(flight.get("price_status"))

    if status == "VERIFIED":
        result["verified"] = True
        result["reliability_points"] += 3
        result["reason"] = (
            "IGNAV fiyat durumu VERIFIED olarak dondu."
        )
    else:
        # IGNAV status alaninin yoklugu otomatik olarak "yanlis"
        # sayilmaz; ancak yuksek guven puani verilmez.
        result["reason"] = (
            "IGNAV fiyat durumu VERIFIED degil veya belirtilmemis."
        )

    if do_booking and flight.get("ignav_id"):
        booking = get_booking_links(
            flight["ignav_id"]
        )
        booking_ok, disagreement, url = booking_analysis(
            booking,
            flight,
        )

        result["booking_verified"] = booking_ok
        result["source_disagreement_points"] = disagreement
        result["booking_url"] = url

        if booking_ok:
            result["reliability_points"] += 2
            if result["verified"]:
                result["reason"] += (
                    " Booking linkleri de alindi."
                )
            else:
                result["reason"] += (
                    " Booking linkleri bulundu."
                )

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
    return scoring_cfg().get("thresholds", {})


def alert_level(score):
    t = thresholds()

    if score >= safe_int(
        t.get("high_confidence"), 90
    ):
        return "KRITIK HATA FIYATI"

    if score >= safe_int(
        t.get("candidate"), 85
    ):
        return "HATA FIYATI ADAYI"

    if score >= safe_int(
        t.get("suspicious"), 70
    ):
        return "SUPHELI FIYAT"

    if score >= safe_int(
        t.get("good_deal"), 50
    ):
        return "IYI FIRSAT"

    return "NORMAL"


def quiet_hours_active():
    cfg = alert_cfg().get("quiet_hours", {})
    if not cfg.get("enabled", False):
        return False

    start = str(cfg.get("start", "23:00"))
    end = str(cfg.get("end", "08:00"))

    try:
        start_h, start_m = [
            int(x) for x in start.split(":")
        ]
        end_h, end_m = [
            int(x) for x in end.split(":")
        ]
    except (TypeError, ValueError):
        return False

    now = utc_now()
    # Sunucu UTC saatini kullanmak yerine Turkiye saatini
    # hesapla. DST olmadigi icin UTC+3 sabittir.
    local_hour = (
        now.hour + 3
    ) % 24
    local_minutes = local_hour * 60 + now.minute

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m

    if start_minutes < end_minutes:
        return (
            start_minutes
            <= local_minutes
            < end_minutes
        )

    return (
        local_minutes >= start_minutes
        or local_minutes < end_minutes
    )


def already_alerted(flight):
    hours = safe_float(
        radar_cfg().get(
            "duplicate_alert_window_hours", 24
        ),
        24,
    ) or 24

    since = (
        utc_now() - timedelta(hours=hours)
    ).isoformat()

    conn = get_connection()
    row = conn.execute("""
        SELECT 1
        FROM alerts
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
    return bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        and os.getenv("TELEGRAM_CHAT_ID", "").strip()
    )


def telegram_send(message):
    if not alert_cfg().get(
        "telegram_enabled", True
    ):
        print("Telegram devre disi.")
        return False

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN", ""
    ).strip()
    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID", ""
    ).strip()

    if not token or not chat_id:
        print(
            "Telegram BASARISIZ: "
            "TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID eksik."
        )
        return False

    try:
        response = SESSION.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message[:4090],
                "disable_web_page_preview": True,
            },
            timeout=(10, 30),
        )

        if response.status_code == 200:
            print("Telegram: mesaj gonderildi.")
            return True

        print(
            "Telegram HTTP hata:",
            response.status_code,
            response.text[:500],
        )
        return False

    except requests.RequestException as exc:
        print("Telegram request hatasi:", repr(exc))
        return False
    except Exception as exc:
        print("Telegram beklenmeyen hata:", repr(exc))
        return False


def telegram_test():
    if not telegram_configured():
        print(
            "TELEGRAM TEST: BASARISIZ - secret eksik."
        )
        return False

    message = (
        "Ucus Hata Fiyati Radari V3.5\n"
        "Telegram baglanti testi basarili.\n"
        "Bildirim kanali aktif."
    )

    ok = telegram_send(message)

    print(
        "TELEGRAM TEST SONUCU:",
        "BASARILI" if ok else "BASARISIZ",
    )

    return ok


def make_alert_message(
    flight,
    score,
    opportunity,
    verification,
):
    level = alert_level(score)
    trip = (
        "Gidis-Donus"
        if flight["return_date"]
        else "Tek Yon"
    )

    baseline, sample, baseline_type = baseline_for(
        flight
    )
    previous = previous_price(flight)

    lines = [
        f"UCUS HATA FIYATI RADARI - {level}",
        "",
        f"Rota: {flight['origin']} -> {flight['destination']}",
        f"Gidis: {flight['departure_date']}",
    ]

    if flight["return_date"]:
        lines.append(
            f"Donus: {flight['return_date']}"
        )

    lines += [
        f"Tip: {trip}",
        (
            f"Fiyat: {flight['price']:.0f} "
            f"{flight['currency']}"
        ),
        (
            f"Havayolu/Ucus: {flight['airline']} "
            f"{flight['flight_number']}"
        ),
        f"Aktarma: {flight['stops']}",
        (
            "Bagaj: "
            f"{flight['checked_bags']}"
            if flight["checked_bags"] is not None
            else "Bagaj: belirtilmemis"
        ),
        f"Error Score: {score}/100",
        f"Opportunity Score: {opportunity}/100",
        (
            "IGNAV VERIFIED: EVET"
            if verification["verified"]
            else "IGNAV VERIFIED: HAYIR"
        ),
    ]

    if baseline:
        lines.append(
            f"Baz fiyat: {baseline:.0f} "
            f"{flight['currency']} "
            f"({sample} gozlem, {baseline_type})"
        )

    if previous:
        lines.append(
            f"Onceki fiyat: {previous:.0f} "
            f"{flight['currency']}"
        )

    if verification.get("booking_verified"):
        lines.append(
            "Booking provider: link bulundu"
        )

    if verification.get("booking_url"):
        lines.append(
            f"Booking: {verification['booking_url']}"
        )

    lines += [
        "",
        "Neden dikkat cekti?",
        (
            "Dogrulama: "
            f"{'basarili' if verification['verified'] else 'basarisiz'}"
        ),
        (
            "Booking kontrolu: "
            f"{'var' if verification['booking_verified'] else 'yok'}"
        ),
        (
            "Fiyat durumu: "
            f"{flight.get('price_status') or 'belirtilmemis'}"
        ),
        "",
        (
            "Bu alarm otomatik analizdir. "
            "Satin almadan once canli fiyati ve kosullari "
            "booking sayfasinda tekrar kontrol edin."
        ),
    ]

    return "\n".join(lines)


def should_send_for_score(score):
    cfg = alert_cfg()

    if not cfg.get("enabled", True):
        return False

    if score < safe_int(
        cfg.get("minimum_score"), 50
    ):
        return False

    if score >= safe_int(
        cfg.get("critical_score"), 90
    ):
        return bool(
            cfg.get(
                "send_high_confidence_error_fares",
                True,
            )
        )

    if score >= safe_int(
        cfg.get("candidate_score"), 85
    ):
        return bool(
            cfg.get(
                "send_error_fare_candidates",
                True,
            )
        )

    if score >= safe_int(
        thresholds().get("suspicious"), 70
    ):
        return bool(
            cfg.get(
                "send_suspicious_prices",
                True,
            )
        )

    return bool(
        cfg.get("send_good_deals", True)
    )


def create_and_send_alert(
    flight,
    score,
    opportunity,
    verification,
):
    if not should_send_for_score(score):
        return False

    if not verification.get("verified"):
        print(
            "Alarm atlandi: IGNAV VERIFIED degil."
        )
        return False

    if quiet_hours_active():
        if (
            score < safe_int(
                thresholds().get(
                    "high_confidence", 90
                ),
                90,
            )
            or not alert_cfg().get(
                "critical_override_quiet_hours",
                True,
            )
        ):
            print(
                "Alarm atlandi: sessiz saat."
            )
            return False

    if already_alerted(flight):
        print(
            "Alarm atlandi: duplicate pencere."
        )
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
                flight_key, origin, destination,
                departure_date, return_date, trip_type,
                price, currency, airline, flight_number,
                score, opportunity_score, alert_level,
                booking_url, sent_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
    print("=" * 64)
    print("UCUS HATA FIYATI RADARI V3.5")
    print("=" * 64)

    init_db()

    api_key_ready = bool(
        os.getenv("IGNAV_API_KEY", "").strip()
    )
    telegram_ready = telegram_configured()

    print(
        "IGNAV API key:",
        "EVET" if api_key_ready else "HAYIR",
    )
    print(
        "Telegram yapilandirilmis:",
        "EVET" if telegram_ready else "HAYIR",
    )

    telegram_test_result = None
    if os.getenv("TELEGRAM_TEST", "0") == "1":
        telegram_test_result = telegram_test()

    routes = build_routes()

    batch_size = safe_int(
        radar_cfg().get("routes_per_run", 10),
        10,
    ) or 10

    selected_routes = route_batch(
        routes,
        batch_size,
    )

    dates = search_dates()

    trip_cfg = settings.get("trip", {})
    one_way_enabled = bool(
        trip_cfg.get("one_way", True)
    )
    round_trip_enabled = bool(
        trip_cfg.get("round_trip", True)
    )

    print(f"Toplam rota: {len(routes)}")
    print(
        f"Bu calisma: {len(selected_routes)} rota"
    )
    print(
        f"Tarihler: {', '.join(dates)}"
    )
    print(f"Market: {market()}")
    print(
        "Tek yon:",
        one_way_enabled,
        "| Gidis-donus:",
        round_trip_enabled,
    )

    total_api, successful_api, cost = api_usage_totals()
    print(
        f"API kullanimi: toplam={total_api}, "
        f"basarili={successful_api}, "
        f"tahmini_maliyet=${cost:.4f}"
    )

    searches = 0
    flights_found = 0
    verified = 0
    booking_checks = 0
    alerts = 0

    verification_minimum = safe_int(
        verification_cfg().get(
            "minimum_score_for_verification",
            30,
        ),
        30,
    ) or 30

    # V3.5'te ayri "booking_minimum_score" ayari
    # settings.json'da yoksa 85 kullan.
    booking_minimum = safe_int(
        verification_cfg().get(
            "booking_minimum_score",
            85,
        ),
        85,
    ) or 85

    verification_enabled = bool(
        verification_cfg().get(
            "enabled", True
        )
    )

    for origin, destination in selected_routes:
        for departure_date in dates:
            if (
                not one_way_enabled
                and not round_trip_enabled
            ):
                break

            search_jobs = []

            if one_way_enabled:
                search_jobs.append(
                    ("one-way", None)
                )

            if round_trip_enabled:
                search_jobs.append(
                    (
                        "round-trip",
                        round_trip_return_date(
                            departure_date
                        ),
                    )
                )

            for trip_type, return_date in search_jobs:
                print(
                    "\nARANIYOR:",
                    f"{origin}->{destination}",
                    departure_date,
                    (
                        f"/ {return_date}"
                        if return_date
                        else ""
                    ),
                )

                data = ignav_search(
                    origin,
                    destination,
                    departure_date,
                    return_date,
                )
                searches += 1

                if data is None:
                    print(
                        "Sonuc alinamadi; "
                        "bu arama atlandi."
                    )
                    continue

                flights = extract_flights(
                    data,
                    origin,
                    destination,
                    departure_date,
                    return_date,
                )

                flights_found += len(flights)

                print(
                    "Bulunan benzersiz itinerary:",
                    len(flights),
                )

                for flight in flights:
                    # Ilk gozlemde DB'ye kaydetmeden once
                    # baseline hesaplanir.
                    preliminary = {
                        "source_disagreement_points": 0,
                        "reliability_points": 0,
                    }

                    preliminary_score, _ = (
                        calculate_score(
                            flight,
                            preliminary,
                        )
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
                        verification_enabled
                        and preliminary_score
                        >= verification_minimum
                    ):
                        do_booking = (
                            preliminary_score
                            >= booking_minimum
                        )

                        if do_booking:
                            booking_checks += 1

                        verification = verify_flight(
                            flight,
                            do_booking=do_booking,
                        )

                        if verification["verified"]:
                            verified += 1

                    score, components = (
                        calculate_score(
                            flight,
                            verification,
                        )
                    )

                    opportunity = (
                        opportunity_score(flight)
                    )

                    print(
                        f"SKOR {score:02d} | "
                        f"FIRSAT {opportunity:02d} | "
                        f"{flight['origin']}->"
                        f"{flight['destination']} | "
                        f"{flight['departure_date']} | "
                        f"{flight['price']:.0f} "
                        f"{flight['currency']} | "
                        f"{flight['airline']} "
                        f"{flight['flight_number']} | "
                        f"{'DOGRULANDI' if verification['verified'] else 'bekliyor'}"
                    )

                    # Her gerÃ§ek gozlemi history'ye ekle.
                    save_observation(flight)
                    save_stable_price(flight)

                    if create_and_send_alert(
                        flight,
                        score,
                        opportunity,
                        verification,
                    ):
                        alerts += 1

    total_api, successful_api, cost = (
        api_usage_totals()
    )

    print("\n" + "=" * 64)
    print("V3.5 CALISMA OZETI")
    print("=" * 64)
    print(f"API aramasi: {searches}")
    print(f"Ucus/itinerary: {flights_found}")
    print(f"Dogrulama: {verified}")
    print(f"Booking kontrolu: {booking_checks}")
    print(f"Telegram alarmi: {alerts}")
    print(
        "API kullanimi:",
        f"{successful_api} basarili / {total_api} toplam",
    )
    print(
        "Tahmini API maliyeti:",
        f"${cost:.4f}",
    )
    print(
        "IGNAV faturalandirma:",
        (
            "GEREKLI - 402"
            if IGNAV_BILLING_BLOCKED
            else "NORMAL"
        ),
    )

    if telegram_test_result is not None:
        print(
            "Telegram test:",
            (
                "BASARILI"
                if telegram_test_result
                else "BASARISIZ"
            ),
        )

    print("=" * 64)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nProgram kullanici tarafindan durduruldu.")
    except Exception as exc:
        print(
            "KRITIK UYGULAMA HATASI:",
            repr(exc),
        )
        raise
