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
    if not os.path.exists(SETTINGS_FILE):
        raise FileNotFoundError(
            f"Ayar dosyasi bulunamadi: {SETTINGS_FILE}"
        )

    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


# ============================================================
# DATABASE
# ============================================================

def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def columns(conn, table):
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def add_column(conn, table, name, definition):
    if name not in columns(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
        )


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # --------------------------------------------------------
    # Fiyatlar
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Gözlemler
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Doğrulamalar
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Telegram alarmları
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Rota durumu
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY,
            route_index INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)

    if cur.execute(
        "SELECT 1 FROM radar_state WHERE id=1"
    ).fetchone() is None:
        cur.execute(
            """
            INSERT INTO radar_state(
                id,
                route_index,
                updated_at
            )
            VALUES(1,0,?)
            """,
            (utc_iso(),)
        )

    # --------------------------------------------------------
    # API kullanım sayacı
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            used_at TEXT NOT NULL,
            endpoint TEXT,
            success INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0
        )
    """)

    # --------------------------------------------------------
    # API durum bilgisi
    #
    # ÖNEMLİ:
    # 402 bilgisi burada kalıcı tutulur.
    # Render yeniden başlasa bile sistem 402 aldığını hatırlar.
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_state (
            id INTEGER PRIMARY KEY,
            billing_blocked INTEGER DEFAULT 0,
            billing_status TEXT,
            last_http_status INTEGER,
            last_endpoint TEXT,
            last_error TEXT,
            updated_at TEXT
        )
    """)

    if cur.execute(
        "SELECT 1 FROM api_state WHERE id=1"
    ).fetchone() is None:
        cur.execute(
            """
            INSERT INTO api_state(
                id,
                billing_blocked,
                billing_status,
                last_http_status,
                last_endpoint,
                last_error,
                updated_at
            )
            VALUES(1,0,'NORMAL',NULL,NULL,NULL,?)
            """,
            (utc_iso(),)
        )

    # ========================================================
    # ESKİ VERİLER İÇİN MIGRATION
    # ========================================================

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
        add_column(
            conn,
            "price_observations",
            name,
            definition
        )

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
        add_column(
            conn,
            "verifications",
            name,
            definition
        )

    alert_fields = {
        "return_date": "TEXT",
        "trip_type": "TEXT DEFAULT 'one-way'",
        "opportunity_score": "REAL",
        "booking_url": "TEXT",
        "sent_at": "TEXT",
    }

    for name, definition in alert_fields.items():
        add_column(
            conn,
            "alerts",
            name,
            definition
        )

    # Eski kayıtlara tarih doldur.
    now = utc_iso()

    cur.execute(
        """
        UPDATE prices
        SET seen_at=?
        WHERE seen_at IS NULL
        """,
        (now,)
    )

    cur.execute(
        """
        UPDATE prices
        SET recorded_at=seen_at
        WHERE recorded_at IS NULL
        AND seen_at IS NOT NULL
        """
    )

    cur.execute(
        """
        UPDATE price_observations
        SET observed_at=?
        WHERE observed_at IS NULL
        """,
        (now,)
    )

    cur.execute(
        """
        UPDATE verifications
        SET checked_at=?
        WHERE checked_at IS NULL
        """,
        (now,)
    )

    cur.execute(
        """
        UPDATE verifications
        SET verification_time=checked_at
        WHERE verification_time IS NULL
        AND checked_at IS NOT NULL
        """
    )

    cur.execute(
        """
        UPDATE alerts
        SET sent_at=?
        WHERE sent_at IS NULL
        """,
        (now,)
    )

    conn.commit()
    conn.close()

    print("Veritabani kontrolu tamamlandi.")


# ============================================================
# API BUTCESI / SAYAC
# ============================================================

def api_budget():
    system = settings.get("system", {})

    return {
        "allowed": bool(
            system.get("paid_api_allowed", False)
        ),
        "budget_tl": safe_float(
            system.get("api_budget_tl"),
            0
        ) or 0,
        "daily_limit": int(
            system.get("daily_query_limit", 0) or 0
        ),
        "monthly_budget_usd": safe_float(
            system.get("monthly_api_budget_usd"),
            0
        ) or 0,
    }


def count_queries_since(conn, start_iso):
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM api_usage
        WHERE used_at >= ?
        """,
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

    conn = get_connection()
    ensure_usage_table(conn)

    # --------------------------------------------------------
    # Ücretli API kapalıysa toplam sayaç kontrolü
    # --------------------------------------------------------

    if not budget["allowed"]:
        count = conn.execute(
            """
            SELECT COUNT(*)
            FROM api_usage
            """
        ).fetchone()[0]

        conn.close()

        if count >= 1000:
            print(
                "API sorgusu durduruldu: "
                "ucretsiz kullanım sayaci sinira ulasti."
            )
            return False

        return True

    # --------------------------------------------------------
    # Günlük / aylık limit
    # --------------------------------------------------------

    now = utc_now()

    day_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    ).isoformat()

    month_start = now.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    ).isoformat()

    daily = count_queries_since(
        conn,
        day_start
    )

    monthly = count_queries_since(
        conn,
        month_start
    )

    if (
        budget["daily_limit"] > 0
        and daily >= budget["daily_limit"]
    ):
        conn.close()
        print("API gunluk sorgu limiti doldu.")
        return False

    if budget["monthly_budget_usd"] > 0:

        row = conn.execute(
            """
            SELECT COALESCE(
                SUM(estimated_cost_usd),
                0
            )
            FROM api_usage
            WHERE used_at >= ?
            """,
            (month_start,)
        ).fetchone()

        monthly_cost = float(row[0] or 0)

        if monthly_cost >= budget["monthly_budget_usd"]:
            conn.close()
            print("API aylik butce limiti doldu.")
            return False

    conn.close()

    return True


def record_api_call(
    endpoint,
    success,
    estimated_cost_usd=0
):
    conn = get_connection()
    ensure_usage_table(conn)

    conn.execute(
        """
        INSERT INTO api_usage(
            used_at,
            endpoint,
            success,
            estimated_cost_usd
        )
        VALUES(?,?,?,?)
        """,
        (
            utc_iso(),
            endpoint,
            1 if success else 0,
            float(estimated_cost_usd or 0),
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# KALICI IGNAV DURUMU
# ============================================================

def ignav_billing_is_blocked():
    conn = get_connection()

    row = conn.execute(
        """
        SELECT billing_blocked
        FROM api_state
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    return bool(row and int(row[0] or 0) == 1)


def set_ignav_billing_blocked(
    status_code=402,
    endpoint="",
    error_text=""
):
    conn = get_connection()

    conn.execute(
        """
        UPDATE api_state
        SET billing_blocked=1,
            billing_status='BILLING_REQUIRED',
            last_http_status=?,
            last_endpoint=?,
            last_error=?,
            updated_at=?
        WHERE id=1
        """,
        (
            status_code,
            endpoint,
            str(error_text or "")[:2000],
            utc_iso(),
        )
    )

    conn.commit()
    conn.close()


def clear_ignav_billing_blocked():
    conn = get_connection()

    conn.execute(
        """
        UPDATE api_state
        SET billing_blocked=0,
            billing_status='NORMAL',
            last_http_status=NULL,
            last_endpoint=NULL,
            last_error=NULL,
            updated_at=?
        WHERE id=1
        """,
        (utc_iso(),)
    )

    conn.commit()
    conn.close()


def ignav_billing_status():
    conn = get_connection()

    row = conn.execute(
        """
        SELECT
            billing_blocked,
            billing_status,
            last_http_status,
            last_endpoint,
            updated_at
        FROM api_state
        WHERE id=1
        """
    ).fetchone()

    conn.close()

    if not row:
        return {
            "blocked": False,
            "status": "UNKNOWN",
            "http_status": None,
            "endpoint": None,
            "updated_at": None,
        }

    return {
        "blocked": bool(row[0]),
        "status": row[1],
        "http_status": row[2],
        "endpoint": row[3],
        "updated_at": row[4],
    }


# ============================================================
# IGNAV
# ============================================================

def ignav_headers():
    key = os.getenv("IGNAV_API_KEY")

    if not key:
        return None

    return {
        "X-Api-Key": key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def market():
    return (
        settings.get("system", {})
        .get("market", "TR")
        or "TR"
    )


def cabin_class():
    cabin = settings.get("cabin", {})

    if cabin.get("business", False):
        return "business"

    return "economy"


def common_payload(
    origin,
    destination,
    departure_date
):
    passengers = settings.get("passengers", {})
    connections = settings.get("connections", {})
    price_cfg = settings.get("price", {})
    airlines = settings.get("airlines", {})

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": int(
            passengers.get("adults", 1) or 1
        ),
        "children": int(
            passengers.get("children", 0) or 0
        ),
        "infants_in_seat": 0,
        "infants_on_lap": int(
            passengers.get("infants", 0) or 0
        ),
        "cabin_class": cabin_class(),
        "max_stops": int(
            connections.get(
                "max_connections",
                1
            ) or 1
        ),
        "allow_self_transfer": bool(
            connections.get(
                "self_transfer",
                False
            )
        ),
        "market": market(),
    }

    maximum = safe_float(
        price_cfg.get("maximum_try"),
        0
    ) or 0

    if maximum > 0:
        payload["max_price"] = maximum

    disabled = airlines.get("disabled", [])

    if disabled:
        payload["airlines_exclude"] = disabled

    return payload


def ignav_post(endpoint, payload):
    headers = ignav_headers()

    if not headers:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    budget = api_budget()

    # --------------------------------------------------------
    # Ücretli API kapalıysa ve daha önce 402 alınmışsa
    # kalıcı blok uygulanır.
    #
    # paid_api_allowed=True yapılırsa bu blok engel olmaz.
    # --------------------------------------------------------

    if (
        not budget["allowed"]
        and ignav_billing_is_blocked()
    ):
        print(
            "IGNAV DURDURULDU: "
            "daha once 402 billing_required alindi."
        )
        print(
            "API'yi yeniden kullanmak icin "
            "IGNAV faturalandirma durumunu duzeltin "
            "ve paid_api_allowed ayarini kontrol edin."
        )
        return None

    if not api_call_allowed():
        print(
            "API sorgusu limit/butce nedeniyle durduruldu."
        )
        return None

    max_attempts = 3

    for attempt in range(1, max_attempts + 1):

        try:
            response = requests.post(
                f"{IGNAV_BASE}{endpoint}",
                headers=headers,
                json=payload,
                timeout=60,
            )

            status_code = response.status_code

            print(
                f"IGNAV {endpoint}: "
                f"HTTP {status_code}"
            )

            # ------------------------------------------------
            # BAŞARILI
            # ------------------------------------------------

            if status_code == 200:

                record_api_call(
                    endpoint,
                    True,
                    0
                )

                try:
                    data = response.json()

                    # Başarılı API cevabı geldiyse,
                    # daha önceki billing block temizlenebilir.
                    if ignav_billing_is_blocked():
                        clear_ignav_billing_blocked()

                    return data

                except ValueError as exc:
                    print(
                        "IGNAV JSON hatasi:",
                        repr(exc)
                    )
                    return None

            # ------------------------------------------------
            # 402 BILLING REQUIRED
            #
            # EN ÖNEMLİ BÖLÜM
            # ------------------------------------------------

            if status_code == 402:

                response_text = response.text[:2000]

                record_api_call(
                    endpoint,
                    False,
                    0
                )

                set_ignav_billing_blocked(
                    status_code=402,
                    endpoint=endpoint,
                    error_text=response_text
                )

                print(
                    "!!! IGNAV 402 BILLING_REQUIRED !!!"
                )
                print(
                    "IGNAV API kalici olarak durduruldu."
                )
                print(
                    "Yeni verification/booking "
                    "istekleri de gonderilmeyecek."
                )
                print(
                    "Cevap:",
                    response_text[:500]
                )

                return None

            # ------------------------------------------------
            # DİĞER HATALAR
            # ------------------------------------------------

            record_api_call(
                endpoint,
                False,
                0
            )

            print(
                "IGNAV cevabi:",
                response.text[:500]
            )

            # Kalıcı 4xx hatalar tekrar denenmez.
            if status_code not in (
                408,
                429,
                500,
                502,
                503,
                504,
            ):
                print(
                    "IGNAV kalici HTTP hatasi; "
                    "retry yapilmayacak."
                )
                return None

            # Geçici hata.
            if attempt < max_attempts:
                wait_seconds = attempt * 2

                print(
                    f"IGNAV gecici hata. "
                    f"{wait_seconds} saniye sonra tekrar deneniyor..."
                )

                time.sleep(wait_seconds)

        except (
            requests.Timeout,
            requests.ConnectionError
        ) as exc:

            record_api_call(
                endpoint,
                False,
                0
            )

            print(
                f"IGNAV baglanti hatasi "
                f"({attempt}/{max_attempts}):",
                repr(exc)
            )

            if attempt < max_attempts:
                time.sleep(attempt * 2)

        except Exception as exc:

            record_api_call(
                endpoint,
                False,
                0
            )

            print(
                "IGNAV beklenmeyen hata:",
                repr(exc)
            )

            return None

    return None


def ignav_search(
    origin,
    destination,
    departure_date,
    return_date=None
):
    payload = common_payload(
        origin,
        destination,
        departure_date
    )

    if return_date:
        payload["return_date"] = return_date
        endpoint = "/fares/round-trip"
    else:
        endpoint = "/fares/one-way"

    return ignav_post(
        endpoint,
        payload
    )


def get_booking_links(ignav_id):
    if not ignav_id:
        return None

    # Billing block varsa booking de kesinlikle gönderilmez.
    if ignav_billing_is_blocked() and not api_budget()["allowed"]:
        print(
            "Booking sorgusu atlandi: "
            "IGNAV billing block aktif."
        )
        return None

    return ignav_post(
        "/fares/booking-links",
        {
            "ignav_id": ignav_id
        }
    )


# ============================================================
# UÇUŞ / ITINERARY PARSING
# ============================================================

def first_segment(leg):
    if not isinstance(leg, dict):
        return {}

    segments = leg.get("segments") or []

    return (
        segments[0]
        if segments
        else {}
    )


def last_segment(leg):
    if not isinstance(leg, dict):
        return {}

    segments = leg.get("segments") or []

    return (
        segments[-1]
        if segments
        else {}
    )


def leg_segments(leg):
    if not isinstance(leg, dict):
        return []

    return leg.get("segments") or []


def segment_sig(segment):
    return "|".join([
        normalize(
            segment.get(
                "departure_airport"
            )
        ),
        normalize(
            segment.get(
                "arrival_airport"
            )
        ),
        normalize(
            segment.get(
                "marketing_carrier_code"
            )
            or segment.get(
                "carrier_code"
            )
        ),
        normalize(
            segment.get(
                "flight_number"
            )
        ),
        normalize(
            segment.get(
                "departure_time_local"
            )
            or segment.get(
                "departure_time"
            )
            or segment.get(
                "departure_datetime"
            )
        ),
    ])


def flight_key(
    origin,
    destination,
    outbound,
    inbound=None
):
    out_segments = leg_segments(
        outbound
    )

    in_segments = leg_segments(
        inbound
    )

    signatures = [
        segment_sig(s)
        for s in out_segments
    ]

    if inbound is not None:
        signatures += ["RETURN"]
        signatures += [
            segment_sig(s)
            for s in in_segments
        ]

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
        checked = (
            int(checked)
            if checked is not None
            else None
        )
    except (TypeError, ValueError):
        checked = None

    try:
        carry = (
            int(carry)
            if carry is not None
            else None
        )
    except (TypeError, ValueError):
        carry = None

    return (
        json.dumps(
            bags,
            ensure_ascii=False
        ),
        checked,
        carry,
    )


def itinerary_to_flight(
    data,
    itinerary,
    origin,
    destination,
    departure_date,
    return_date=None
):
    price_info = (
        itinerary.get("price")
        or {}
    )

    outbound = (
        itinerary.get("outbound")
        or {}
    )

    inbound = (
        itinerary.get("inbound")
        or None
    )

    segments = leg_segments(
        outbound
    )

    first = first_segment(
        outbound
    )

    last = last_segment(
        outbound
    )

    airline = normalize(
        first.get(
            "marketing_carrier_code"
        )
        or outbound.get(
            "carrier"
        )
    )

    flight_number = normalize(
        first.get(
            "flight_number"
        )
    )

    duration = outbound.get(
        "duration_minutes"
    )

    try:
        duration = (
            int(duration)
            if duration is not None
            else None
        )
    except (TypeError, ValueError):
        duration = None

    stops = max(
        0,
        len(segments) - 1
    )

    baggage, checked, carry = (
        baggage_info(itinerary)
    )

    key = flight_key(
        origin,
        destination,
        outbound,
        inbound
    )

    return {
        "flight_key": key,
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "return_date": return_date,
        "trip_type": (
            "round-trip"
            if return_date
            else "one-way"
        ),
        "price": safe_float(
            price_info.get("amount"),
            0
        ),
        "currency": normalize(
            price_info.get("currency")
        ),
        "airline": airline,
        "flight_number": flight_number,
        "duration_minutes": duration,
        "baggage": baggage,
        "checked_bags": checked,
        "carry_on_bags": carry,
        "self_transfer": bool(
            itinerary.get(
                "requires_self_transfer",
                False
            )
        ),
        "price_status": normalize(
            price_info.get("status")
        ),
        "ignav_id": itinerary.get(
            "ignav_id"
        ),
        "stops": stops,
        "departure_time": (
            first.get(
                "departure_time_local"
            )
            or first.get(
                "departure_time"
            )
        ),
        "arrival_time": (
            last.get(
                "arrival_time_local"
            )
            or last.get(
                "arrival_time"
            )
        ),
        "inbound": inbound,
    }


def extract_flights(
    data,
    origin,
    destination,
    departure_date,
    return_date=None
):
    if not data or not isinstance(data, dict):
        return []

    result = []
    seen = set()

    for itinerary in (
        data.get("itineraries", [])
        or []
    ):
        if not isinstance(
            itinerary,
            dict
        ):
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
            int(
                flight["self_transfer"]
            ),
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        result.append(flight)

    return result


# ============================================================
# ROTA / TARİH ROTASYONU
# ============================================================

def build_routes():
    airports = settings.get(
        "airports",
        {}
    )

    priority_origins = (
        airports.get(
            "priority_origins",
            []
        )
    )

    priority_destinations = (
        airports.get(
            "priority_destinations",
            []
        )
    )

    domestic = (
        airports.get(
            "domestic_destinations",
            []
        )
    )

    europe = (
        airports.get(
            "europe_destinations",
            []
        )
    )

    routes = []

    def add(a, b):
        if (
            a
            and b
            and a != b
            and (a, b) not in routes
        ):
            routes.append(
                (a, b)
            )

    # Öncelikli yönler
    for destination in (
        priority_destinations
        + domestic
        + europe
    ):
        for origin in priority_origins:
            add(origin, destination)
            add(destination, origin)

    # Tüm destinasyonlar
    all_destinations = []

    for code in domestic + europe:
        if code not in all_destinations:
            all_destinations.append(
                code
            )

    # İç hatlar
    if airports.get(
        "domestic_enabled",
        True
    ):
        for a in domestic:
            for b in domestic:
                if a != b:
                    add(a, b)

    # Avrupa
    if airports.get(
        "europe_enabled",
        True
    ):
        for a in priority_origins:
            for b in all_destinations:
                add(a, b)
                add(b, a)

    return routes


def route_batch(
    routes,
    batch_size
):
    # ÖNEMLİ:
    # Hiç rota yoksa modulo 0 hatası oluşmasın.
    if not routes:
        print(
            "UYARI: Aranacak rota bulunamadi."
        )
        return []

    try:
        batch_size = int(
            batch_size or 1
        )
    except (
        TypeError,
        ValueError
    ):
        batch_size = 1

    batch_size = max(
        1,
        batch_size
    )

    conn = get_connection()

    row = conn.execute(
        """
        SELECT route_index
        FROM radar_state
        WHERE id=1
        """
    ).fetchone()

    index = (
        int(row[0] or 0)
        if row
        else 0
    )

    total = len(routes)

    selected = []

    for i in range(
        min(batch_size, total)
    ):
        selected.append(
            routes[
                (index + i) % total
            ]
        )

    new_index = (
        index + len(selected)
    ) % total

    conn.execute(
        """
        UPDATE radar_state
        SET route_index=?,
            updated_at=?
        WHERE id=1
        """,
        (
            new_index,
            utc_iso()
        )
    )

    conn.commit()
    conn.close()

    return selected


def search_dates():
    radar = settings.get(
        "radar",
        {}
    )

    raw = radar.get(
        "dates_days_ahead",
        [30, 60, 90]
    )

    today = utc_now().date()

    dates = []

    for days in raw:
        try:
            d = (
                today
                + timedelta(
                    days=int(days)
                )
            )

            dates.append(
                d.isoformat()
            )

        except (
            TypeError,
            ValueError
        ):
            continue

    return dates


def round_trip_return_date(
    departure_date
):
    radar = settings.get(
        "radar",
        {}
    )

    try:
        days = int(
            radar.get(
                "round_trip_return_days",
                7
            )
            or 7
        )
    except (
        TypeError,
        ValueError
    ):
        days = 7

    departure = datetime.strptime(
        departure_date,
        "%Y-%m-%d"
    ).date()

    return (
        departure
        + timedelta(days=days)
    ).isoformat()


# ============================================================
# DATABASE KAYIT
# ============================================================

def save_observation(flight):
    conn = get_connection()

    conn.execute("""
        INSERT INTO price_observations (
            flight_key,
            origin,
            destination,
            departure_date,
            return_date,
            trip_type,
            price,
            currency,
            airline,
            flight_number,
            duration_minutes,
            baggage,
            checked_bags,
            carry_on_bags,
            self_transfer,
            price_status,
            ignav_id,
            stops,
            observed_at
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?
        )
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
        int(
            flight["self_transfer"]
        ),
        flight["price_status"],
        flight["ignav_id"],
        flight["stops"],
        utc_iso(),
    ))

    conn.commit()
    conn.close()


def save_stable_price(flight):
    conn = get_connection()

    exists = conn.execute(
        """
        SELECT 1
        FROM prices
        WHERE flight_key=?
        AND departure_date=?
        AND COALESCE(
            return_date,
            ''
        ) = COALESCE(
            ?,
            ''
        )
        AND price=?
        AND currency=?
        LIMIT 1
        """,
        (
            flight["flight_key"],
            flight["departure_date"],
            flight["return_date"],
            flight["price"],
            flight["currency"],
        )
    ).fetchone()

    if not exists:

        now = utc_iso()

        conn.execute("""
            INSERT INTO prices (
                flight_key,
                origin,
                destination,
                departure_date,
                return_date,
                trip_type,
                price,
                currency,
                airline,
                flight_number,
                duration_minutes,
                baggage,
                checked_bags,
                carry_on_bags,
                self_transfer,
                price_status,
                ignav_id,
                stops,
                seen_at,
                recorded_at
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?
            )
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
            int(
                flight["self_transfer"]
            ),
            flight["price_status"],
            flight["ignav_id"],
            flight["stops"],
            now,
            now,
        ))

    conn.commit()
    conn.close()


# ============================================================
# FİYAT GEÇMİŞİ
# ============================================================

def same_flight_history(
    flight,
    limit=40
):
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT price
        FROM price_observations
        WHERE flight_key=?
        AND departure_date=?
        AND COALESCE(
            return_date,
            ''
        ) = COALESCE(
            ?,
            ''
        )
        AND currency=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            flight["flight_key"],
            flight["departure_date"],
            flight["return_date"],
            flight["currency"],
            limit,
        )
    ).fetchall()

    conn.close()

    return [
        float(r[0])
        for r in rows
        if r[0] is not None
    ]


def route_history(
    flight,
    limit=100
):
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT price
        FROM price_observations
        WHERE origin=?
        AND destination=?
        AND departure_date=?
        AND COALESCE(
            return_date,
            ''
        ) = COALESCE(
            ?,
            ''
        )
        AND currency=?
        AND price > 0
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["return_date"],
            flight["currency"],
            limit,
        )
    ).fetchall()

    conn.close()

    return [
        float(r[0])
        for r in rows
        if r[0] is not None
    ]


def baseline_for(flight):
    same = same_flight_history(
        flight
    )

    if len(same) >= 3:
        return (
            statistics.median(same),
            len(same),
            "same-flight"
        )

    route = route_history(
        flight
    )

    if len(route) >= 5:
        return (
            statistics.median(route),
            len(route),
            "route-date"
        )

    return (
        None,
        len(same) or len(route),
        "none"
    )


def previous_price(flight):
    history = same_flight_history(
        flight,
        5
    )

    return (
        history[0]
        if history
        else None
    )


# ============================================================
# SKORLAMA
# ============================================================

def historical_anomaly(flight):
    baseline, _, _ = baseline_for(
        flight
    )

    if not baseline or baseline <= 0:
        return 0

    ratio = (
        flight["price"]
        / baseline
    )

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
    baseline, _, _ = baseline_for(
        flight
    )

    if not baseline or baseline <= 0:
        return 0

    ratio = (
        flight["price"]
        / baseline
    )

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
    previous = previous_price(
        flight
    )

    if not previous or previous <= 0:
        return 0

    drop = (
        previous - flight["price"]
    ) / previous

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

    checked = flight.get(
        "checked_bags"
    )

    if (
        checked is not None
        and checked == 0
    ):
        points += 1

    if flight.get(
        "stops",
        0
    ) >= 2:
        points += 1

    return min(
        points,
        5
    )


def currency_anomaly(flight):
    expected = market()

    if (
        expected == "TR"
        and flight["currency"]
        not in ("TRY", "")
    ):
        return 5

    return 0


def short_lived_persistence(
    flight
):
    conn = get_connection()

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM price_observations
        WHERE flight_key=?
        AND departure_date=?
        AND COALESCE(
            return_date,
            ''
        ) = COALESCE(
            ?,
            ''
        )
        """,
        (
            flight["flight_key"],
            flight["departure_date"],
            flight["return_date"],
        )
    ).fetchone()

    conn.close()

    count = int(
        row[0] or 0
    )

    return (
        2
        if count == 1
        else 0
    )


def calculate_score(
    flight,
    verification
):
    components = {
        "historical_anomaly":
            historical_anomaly(flight),

        "market_divergence":
            market_divergence(flight),

        "sudden_drop":
            sudden_drop(flight),

        "source_disagreement":
            verification.get(
                "source_disagreement_points",
                0
            ),

        "tax_anomaly":
            0,

        "currency_anomaly":
            currency_anomaly(flight),

        "fare_anomaly":
            fare_anomaly(flight),

        "short_lived_persistence":
            short_lived_persistence(flight),

        "source_reliability":
            verification.get(
                "reliability_points",
                0
            ),
    }

    score = sum(
        components.values()
    )

    if (
        flight.get(
            "price_status"
        ) == "UNVERIFIED"
    ):
        score -= 3

    score = max(
        0,
        min(
            100,
            score
        )
    )

    return (
        score,
        components
    )


def opportunity_score(flight):
    score = 0

    if (
        flight.get(
            "checked_bags"
        ) is not None
        and flight["checked_bags"] > 0
    ):
        score += 15

    if not flight[
        "self_transfer"
    ]:
        score += 15

    if flight.get(
        "stops",
        0
    ) == 0:
        score += 15

    elif flight.get(
        "stops",
        0
    ) == 1:
        score += 8

    duration = flight.get(
        "duration_minutes"
    )

    if duration is not None:

        if duration <= 180:
            score += 15

        elif duration <= 300:
            score += 10

        elif duration <= 480:
            score += 5

    baseline, _, _ = baseline_for(
        flight
    )

    if baseline and baseline > 0:

        ratio = (
            flight["price"]
            / baseline
        )

        if ratio <= 0.60:
            score += 40

        elif ratio <= 0.75:
            score += 30

        elif ratio <= 0.85:
            score += 20

        elif ratio <= 0.95:
            score += 10

    return min(
        100,
        score
    )


# ============================================================
# DOĞRULAMA
# ============================================================

def matching_flight(
    flight,
    candidates
):
    for candidate in candidates:

        if (
            candidate["flight_key"]
            == flight["flight_key"]
            and abs(
                candidate["price"]
                - flight["price"]
            ) < 0.01
            and candidate["currency"]
            == flight["currency"]
            and candidate["self_transfer"]
            == flight["self_transfer"]
        ):
            return candidate

    return None


def booking_analysis(
    booking_data,
    flight
):
    if not booking_data:
        return (
            False,
            0,
            None
        )

    options = (
        booking_data.get(
            "booking_options"
        )
        or []
    )

    prices = []
    urls = []

    for option in options:

        for link in (
            option.get(
                "links",
                []
            )
            or []
        ):

            price = safe_float(
                (
                    link.get("price")
                    or {}
                ).get("amount")
            )

            currency = normalize(
                (
                    link.get("price")
                    or {}
                ).get("currency")
            )

            if (
                price is not None
                and currency
                == flight["currency"]
            ):
                prices.append(
                    price
                )

            url = link.get(
                "url"
            )

            if url:
                urls.append(
                    url
                )

    if not prices:
        return (
            False,
            0,
            urls[0]
            if urls
            else None
        )

    spread = (
        max(prices)
        - min(prices)
    )

    base = min(prices)

    points = 0

    if base > 0:

        pct = (
            spread
            / base
        )

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
        urls[0]
        if urls
        else None
    )


def save_verification(
    flight,
    verified,
    booking_verified,
    disagreement,
    reason
):
    conn = get_connection()

    now = utc_iso()

    conn.execute("""
        INSERT INTO verifications (
            flight_key,
            origin,
            destination,
            departure_date,
            return_date,
            trip_type,
            price,
            currency,
            airline,
            flight_number,
            duration_minutes,
            baggage,
            self_transfer,
            price_status,
            ignav_id,
            stops,
            verified,
            booking_verified,
            source_disagreement_points,
            verification_reason,
            checked_at,
            verification_time
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?
        )
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
        int(
            flight["self_transfer"]
        ),
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


def verify_flight(
    flight,
    do_booking=False
):
    result = {
        "verified": False,
        "booking_verified": False,
        "source_disagreement_points": 0,
        "reliability_points": 0,
        "booking_url": None,
        "reason": "",
    }

    # --------------------------------------------------------
    # Billing block aktifse ikinci sorgu yapma.
    # --------------------------------------------------------

    if (
        ignav_billing_is_blocked()
        and not api_budget()["allowed"]
    ):
        result["reason"] = (
            "IGNAV 402 billing_required "
            "nedeniyle dogrulama yapilmadi."
        )

        save_verification(
            flight,
            False,
            False,
            0,
            result["reason"]
        )

        return result

    # --------------------------------------------------------
    # İkinci IGNAV sorgusu
    # --------------------------------------------------------

    data = ignav_search(
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
    )

    # Eğer sorgu sırasında 402 geldiyse
    # booking sorgusuna geçme.
    if (
        data is None
        and ignav_billing_is_blocked()
        and not api_budget()["allowed"]
    ):
        result["reason"] = (
            "IGNAV 402 billing_required. "
            "Dogrulama durduruldu."
        )

        save_verification(
            flight,
            False,
            False,
            0,
            result["reason"]
        )

        return result

    candidates = extract_flights(
        data,
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["return_date"],
    )

    matched = matching_flight(
        flight,
        candidates
    )

    if matched:

        result["verified"] = True

        result[
            "reliability_points"
        ] += 3

        result["reason"] = (
            "Ayni ucus ve fiyat "
            "ikinci IGNAV aramasinda "
            "tekrar bulundu."
        )

    else:

        result["reason"] = (
            "Ikinci IGNAV aramasinda "
            "ayni ucus/fiyat "
            "dogrulanamadi."
        )

    # --------------------------------------------------------
    # Booking doğrulaması
    # --------------------------------------------------------

    if (
        do_booking
        and matched
        and matched.get("ignav_id")
    ):

        if (
            ignav_billing_is_blocked()
            and not api_budget()["allowed"]
        ):
            print(
                "Booking kontrolu atlandi: "
                "IGNAV billing block aktif."
            )

        else:

            booking = get_booking_links(
                matched["ignav_id"]
            )

            booking_ok, disagreement, url = (
                booking_analysis(
                    booking,
                    matched
                )
            )

            result[
                "booking_verified"
            ] = booking_ok

            result[
                "source_disagreement_points"
            ] = disagreement

            result[
                "booking_url"
            ] = url

            if booking_ok:
                result[
                    "reliability_points"
                ] += 2

    save_verification(
        flight,
        result["verified"],
        result["booking_verified"],
        result[
            "source_disagreement_points"
        ],
        result["reason"],
    )

    return result


# ============================================================
# ALERT
# ============================================================

def thresholds():
    return (
        settings.get(
            "scoring",
            {}
        ).get(
            "thresholds",
            {}
        )
    )


def alert_level(score):
    t = thresholds()

    if score >= int(
        t.get(
            "high_confidence",
            90
        )
    ):
        return "KRITIK HATA FIYATI"

    if score >= int(
        t.get(
            "candidate",
            85
        )
    ):
        return "HATA FIYATI ADAYI"

    if score >= int(
        t.get(
            "suspicious",
            70
        )
    ):
        return "SUPHELI FIYAT"

    if score >= int(
        t.get(
            "good_deal",
            50
        )
    ):
        return "IYI FIRSAT"

    return "NORMAL"


def already_alerted(flight):
    hours = float(
        settings.get(
            "radar",
            {}
        ).get(
            "duplicate_alert_window_hours",
            24
        )
        or 24
    )

    since = (
        utc_now()
        - timedelta(
            hours=hours
        )
    ).isoformat()

    conn = get_connection()

    row = conn.execute(
        """
        SELECT 1
        FROM alerts
        WHERE flight_key=?
        AND departure_date=?
        AND COALESCE(
            return_date,
            ''
        ) = COALESCE(
            ?,
            ''
        )
        AND price=?
        AND currency=?
        AND sent_at >= ?
        LIMIT 1
        """,
        (
            flight["flight_key"],
            flight["departure_date"],
            flight["return_date"],
            flight["price"],
            flight["currency"],
            since,
        )
    ).fetchone()

    conn.close()

    return row is not None


def telegram_configured():
    return bool(
        os.getenv(
            "TELEGRAM_BOT_TOKEN"
        )
        and os.getenv(
            "TELEGRAM_CHAT_ID"
        )
    )


def telegram_send(message):
    if not settings.get(
        "alerts",
        {}
    ).get(
        "telegram_enabled",
        True
    ):
        print(
            "Telegram devre disi."
        )
        return False

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        print(
            "Telegram BASARISIZ: "
            "secret eksik."
        )
        return False

    try:

        response = requests.post(
            f"https://api.telegram.org/"
            f"bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message[:4090],
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        if response.status_code == 200:
            print(
                "Telegram: mesaj gonderildi."
            )
            return True

        print(
            "Telegram HTTP hata:",
            response.status_code,
            response.text[:500]
        )

        return False

    except Exception as exc:

        print(
            "Telegram hata:",
            repr(exc)
        )

        return False


def telegram_test():
    if not telegram_configured():
        print(
            "TELEGRAM TEST: "
            "BASARISIZ - secret eksik."
        )
        return False

    print(
        "TELEGRAM TEST: "
        "secretlar mevcut."
    )

    message = (
        "🚨 UÇUŞ HATA FİYATI RADARI\n"
        "Telegram bağlantı testi başarılı.\n"
        "Bildirim kanalı aktif."
    )

    ok = telegram_send(
        message
    )

    print(
        "TELEGRAM TEST SONUCU:",
        "BAŞARILI"
        if ok
        else "BAŞARISIZ"
    )

    return ok


def make_alert_message(
    flight,
    score,
    opportunity,
    verification
):
    level = alert_level(
        score
    )

    trip = (
        "Gidiş-Dönüş"
        if flight["return_date"]
        else "Tek Yön"
    )

    baseline, sample, baseline_type = (
        baseline_for(flight)
    )

    drop = previous_price(
        flight
    )

    lines = [
        f"✈️ {level}",
        "",
        (
            f"🛫 {flight['origin']} "
            f"→ {flight['destination']}"
        ),
        (
            f"📅 Gidiş: "
            f"{flight['departure_date']}"
        ),
    ]

    if flight["return_date"]:
        lines.append(
            f"🔄 Dönüş: "
            f"{flight['return_date']}"
        )

    lines += [
        f"🧳 {trip}",
        (
            f"💰 Fiyat: "
            f"{flight['price']:.0f} "
            f"{flight['currency']}"
        ),
        (
            f"🏷️ Havayolu/Uçuş: "
            f"{flight['airline']} "
            f"{flight['flight_number']}"
        ),
        (
            f"🛑 Aktarma: "
            f"{flight['stops']}"
        ),
        (
            f"🧳 Bagaj: "
            f"{flight['checked_bags'] "
            if False else ''}"
        ),
    ]

    # Bagaj satırını ayrı oluştur.
    lines[-1] = (
        f"🧳 Bagaj: "
        f"{flight['checked_bags'] "
        if False else ''}"
    )

    # Python f-string içinde karmaşık ifade kullanmamak
    # için bagaj değerini burada hazırlıyoruz.
    checked_bags = (
        flight["checked_bags"]
        if flight["checked_bags"] is not None
        else "?"
    )

    lines[-1] = (
        f"🧳 Bagaj: "
        f"{checked_bags} checked"
    )

    lines += [
        f"🔎 Error Score: {score}/100",
        (
            f"⭐ Opportunity Score: "
            f"{opportunity}/100"
        ),
        (
            f"✅ Doğrulama: "
            f"{'EVET' if verification['verified'] else 'HAYIR'}"
        ),
    ]

    if baseline:
        lines.append(
            (
                f"📊 Baz fiyat: "
                f"{baseline:.0f} "
                f"{flight['currency']} "
                f"({sample} gözlem, "
                f"{baseline_type})"
            )
        )

    if drop:
        lines.append(
            (
                f"📉 Son bilinen fiyat: "
                f"{drop:.0f} "
                f"{flight['currency']}"
            )
        )

    if verification.get(
        "booking_verified"
    ):
        lines.append(
            "🔗 Booking provider kontrolü: VAR"
        )

    if verification.get(
        "booking_url"
    ):
        lines.append(
            (
                f"🔗 "
                f"{verification['booking_url']}"
            )
        )

    lines += [
        "",
        "NEDEN DİKKAT ÇEKTİ?",
        (
            "• Doğrulama: "
            f"{'başarılı' if verification['verified'] else 'başarısız'}"
        ),
        (
            "• Kaynak farkı puanı: "
            f"{verification.get('source_disagreement_points', 0)}"
        ),
        (
            "• Fiyat durumu: "
            f"{flight.get('price_status', '')}"
        ),
        "",
        (
            "Bu alarm otomatik analizdir; "
            "satın almadan önce fiyat ve "
            "koşulları son kez kontrol edin."
        ),
    ]

    return "\n".join(
        lines
    )


def create_and_send_alert(
    flight,
    score,
    opportunity,
    verification
):
    alerts_cfg = settings.get(
        "alerts",
        {}
    )

    minimum = int(
        alerts_cfg.get(
            "minimum_score",
            50
        )
        or 50
    )

    if score < minimum:
        return False

    if not verification.get(
        "verified"
    ):
        print(
            "Alarm atlandi: "
            "fiyat dogrulanmadi."
        )
        return False

    if already_alerted(
        flight
    ):
        print(
            "Alarm atlandi: "
            "ayni alarm yakin zamanda "
            "gonderilmis."
        )
        return False

    message = make_alert_message(
        flight,
        score,
        opportunity,
        verification,
    )

    sent = telegram_send(
        message
    )

    if sent:

        conn = get_connection()

        conn.execute("""
            INSERT INTO alerts (
                flight_key,
                origin,
                destination,
                departure_date,
                return_date,
                trip_type,
                price,
                currency,
                airline,
                flight_number,
                score,
                opportunity_score,
                alert_level,
                booking_url,
                sent_at
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?
            )
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
            verification.get(
                "booking_url"
            ),
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
    print(
        "UÇUŞ HATA FİYATI RADARI V4.0"
    )
    print("=" * 60)

    init_db()

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    telegram_ready = (
        telegram_configured()
    )

    print(
        "Telegram yapilandirilmis:",
        "EVET"
        if telegram_ready
        else "HAYIR"
    )

    telegram_test_result = None

    if os.getenv(
        "TELEGRAM_TEST",
        "0"
    ) == "1":
        telegram_test_result = (
            telegram_test()
        )

    # --------------------------------------------------------
    # IGNAV durumunu göster
    # --------------------------------------------------------

    billing = (
        ignav_billing_status()
    )

    print(
        "IGNAV billing durumu:",
        billing["status"]
    )

    if billing["blocked"]:
        print(
            "UYARI: IGNAV daha once "
            "402 billing_required verdi."
        )

        if not api_budget()["allowed"]:
            print(
                "IGNAV sorgulari "
                "engellendi."
            )

    # --------------------------------------------------------
    # Rotalar
    # --------------------------------------------------------

    routes = build_routes()

    batch_size = int(
        settings.get(
            "radar",
            {}
        ).get(
            "routes_per_run",
            10
        )
        or 10
    )

    selected_routes = route_batch(
        routes,
        batch_size
    )

    dates = search_dates()

    trip_cfg = settings.get(
        "trip",
        {}
    )

    one_way_enabled = (
        trip_cfg.get(
            "one_way",
            True
        )
    )

    round_trip_enabled = (
        trip_cfg.get(
            "round_trip",
            True
        )
    )

    print(
        f"Toplam rota: {len(routes)}"
    )

    print(
        f"Bu calisma: "
        f"{len(selected_routes)} rota"
    )

    print(
        "Tarihler:",
        ", ".join(dates)
    )

    print(
        f"Market: {market()}"
    )

    print(
        f"Tek yon: "
        f"{one_way_enabled} | "
        f"Gidis-donus: "
        f"{round_trip_enabled}"
    )

    # --------------------------------------------------------
    # Sayaçlar
    # --------------------------------------------------------

    searches = 0
    flights_found = 0
    verified = 0
    booking_checks = 0
    alerts = 0
    blocked_searches = 0

    verification_cfg = (
        settings.get(
            "radar",
            {}
        ).get(
            "verification",
            {}
        )
    )

    verification_enabled = (
        verification_cfg.get(
            "enabled",
            True
        )
    )

    verification_minimum = int(
        verification_cfg.get(
            "minimum_score_for_verification",
            30
        )
        or 30
    )

    booking_minimum = int(
        verification_cfg.get(
            "booking_minimum_score",
            70
        )
        or 70
    )

    # --------------------------------------------------------
    # RADAR DÖNGÜSÜ
    # --------------------------------------------------------

    for origin, destination in (
        selected_routes
    ):

        for departure_date in dates:

            search_jobs = []

            if one_way_enabled:
                search_jobs.append(
                    (
                        "one-way",
                        None
                    )
                )

            if round_trip_enabled:
                search_jobs.append(
                    (
                        "round-trip",
                        round_trip_return_date(
                            departure_date
                        )
                    )
                )

            for trip_type, return_date in (
                search_jobs
            ):

                print(
                    "\nARANIYOR: "
                    f"{origin}->{destination} "
                    f"{departure_date}"
                    + (
                        f" / {return_date}"
                        if return_date
                        else ""
                    )
                )

                # ------------------------------------------------
                # Kalıcı 402 block aktifse yeni sorgu yapma.
                # ------------------------------------------------

                if (
                    ignav_billing_is_blocked()
                    and not api_budget()["allowed"]
                ):
                    print(
                        "ARAMA ATLANDI: "
                        "IGNAV billing_required "
                        "block aktif."
                    )

                    blocked_searches += 1

                    continue

                data = ignav_search(
                    origin,
                    destination,
                    departure_date,
                    return_date,
                )

                searches += 1

                # 402 bu arama sırasında geldiyse
                # sonraki tüm aramalar zaten atlanacak.
                if (
                    data is None
                    and ignav_billing_is_blocked()
                    and not api_budget()["allowed"]
                ):
                    print(
                        "IGNAV 402 sonrasi "
                        "radar API sorgulari "
                        "durduruldu."
                    )

                    blocked_searches += 1

                    continue

                flights = extract_flights(
                    data,
                    origin,
                    destination,
                    departure_date,
                    return_date,
                )

                flights_found += len(
                    flights
                )

                print(
                    "Bulunan benzersiz "
                    f"itinerary: {len(flights)}"
                )

                # ------------------------------------------------
                # UÇUŞLAR
                # ------------------------------------------------

                for flight in flights:

                    preliminary_verification = {
                        "source_disagreement_points": 0,
                        "reliability_points": 0,
                    }

                    preliminary_score, _ = (
                        calculate_score(
                            flight,
                            preliminary_verification
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

                    # ------------------------------------------------
                    # DOĞRULAMA
                    # ------------------------------------------------

                    if (
                        verification_enabled
                        and preliminary_score
                        >= verification_minimum
                    ):

                        # 402 block oluşmuşsa verification yapma.
                        if (
                            ignav_billing_is_blocked()
                            and not api_budget()["allowed"]
                        ):
                            verification[
                                "reason"
                            ] = (
                                "IGNAV 402 "
                                "nedeniyle "
                                "dogrulama "
                                "atlandi."
                            )

                        else:

                            do_booking = (
                                preliminary_score
                                >= booking_minimum
                            )

                            if do_booking:
                                booking_checks += 1

                            verification = (
                                verify_flight(
                                    flight,
                                    do_booking=do_booking
                                )
                            )

                            if verification[
                                "verified"
                            ]:
                                verified += 1

                    # ------------------------------------------------
                    # SON SKOR
                    # ------------------------------------------------

                    score, components = (
                        calculate_score(
                            flight,
                            verification
                        )
                    )

                    opportunity = (
                        opportunity_score(
                            flight
                        )
                    )

                    print(
                        f"SKOR {score:02d} | "
                        f"FIRSAT {opportunity:02d} | "
                        f"{flight['origin']}"
                        f"->{flight['destination']} | "
                        f"{flight['departure_date']} | "
                        f"{flight['price']:.0f} "
                        f"{flight['currency']} | "
                        f"{flight['airline']} "
                        f"{flight['flight_number']} | "
                        f"{'DOGRULANDI' if verification['verified'] else 'BEKLIYOR'}"
                    )

                    # ------------------------------------------------
                    # VERİ KAYDI
                    # ------------------------------------------------

                    save_observation(
                        flight
                    )

                    save_stable_price(
                        flight
                    )

                    # ------------------------------------------------
                    # TELEGRAM ALARMI
                    # ------------------------------------------------

                    if create_and_send_alert(
                        flight,
                        score,
                        opportunity,
                        verification,
                    ):
                        alerts += 1

    # ========================================================
    # ÖZET
    # ========================================================

    final_billing = (
        ignav_billing_status()
    )

    print(
        "\n" + "=" * 60
    )

    print(
        "V4.0 CALISMA OZETI"
    )

    print("=" * 60)

    print(
        f"API aramasi: {searches}"
    )

    print(
        f"Billing nedeniyle atlanan: "
        f"{blocked_searches}"
    )

    print(
        f"Ucus/itinerary: "
        f"{flights_found}"
    )

    print(
        f"Dogrulama: "
        f"{verified}"
    )

    print(
        f"Booking kontrolu: "
        f"{booking_checks}"
    )

    print(
        f"Telegram alarmi: "
        f"{alerts}"
    )

    print(
        "IGNAV faturalandirma:",
        (
            "GEREKLI - 402"
            if final_billing["blocked"]
            else "NORMAL"
        )
    )

    if final_billing["updated_at"]:
        print(
            "IGNAV durum zamani:",
            final_billing["updated_at"]
        )

    if telegram_test_result is not None:
        print(
            "Telegram test:",
            (
                "BASARILI"
                if telegram_test_result
                else "BASARISIZ"
            )
        )

    print("=" * 60)


# ============================================================
# ÇALIŞTIR
# ============================================================

if __name__ == "__main__":
    main()
