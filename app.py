import os
import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import requests


DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"

IGNAV_URL = "https://ignav.com/api/fares/one-way"
TELEGRAM_URL = "https://api.telegram.org/bot{}/sendMessage"


# =========================================================
# ZAMAN
# =========================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def parse_datetime(value):
    if not value:
        return None

    try:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt
    except Exception:
        return None


# =========================================================
# AYARLAR
# =========================================================

def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


SETTINGS = load_settings()


# =========================================================
# VERİTABANI
# =========================================================

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table_name):
    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {row["name"] for row in rows}


def add_column_if_missing(conn, table_name, column_name, column_type):
    columns = table_columns(conn, table_name)

    if column_name not in columns:
        conn.execute(
            f"ALTER TABLE {table_name} "
            f"ADD COLUMN {column_name} {column_type}"
        )


def init_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            airline TEXT,
            flight_number TEXT,
            price REAL,
            currency TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            self_transfer INTEGER DEFAULT 0,
            recorded_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            airline TEXT,
            flight_number TEXT,
            price REAL,
            currency TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            self_transfer INTEGER DEFAULT 0,
            observed_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            price REAL,
            currency TEXT,
            verified INTEGER DEFAULT 0,
            verification_time TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            price REAL,
            currency TEXT,
            score INTEGER,
            level TEXT,
            sent_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            route_index INTEGER DEFAULT 0
        )
    """)

    # Eski veritabanları için migration
    for column, column_type in [
        ("flight_key", "TEXT"),
        ("origin", "TEXT"),
        ("destination", "TEXT"),
        ("departure_date", "TEXT"),
        ("airline", "TEXT"),
        ("flight_number", "TEXT"),
        ("price", "REAL"),
        ("currency", "TEXT"),
        ("duration_minutes", "INTEGER"),
        ("baggage", "TEXT"),
        ("self_transfer", "INTEGER DEFAULT 0"),
        ("recorded_at", "TEXT"),
    ]:
        add_column_if_missing(
            conn,
            "prices",
            column,
            column_type
        )

    for column, column_type in [
        ("flight_key", "TEXT"),
        ("origin", "TEXT"),
        ("destination", "TEXT"),
        ("departure_date", "TEXT"),
        ("price", "REAL"),
        ("currency", "TEXT"),
        ("verified", "INTEGER DEFAULT 0"),
        ("verification_time", "TEXT"),
    ]:
        add_column_if_missing(
            conn,
            "verifications",
            column,
            column_type
        )

    for column, column_type in [
        ("flight_key", "TEXT"),
        ("origin", "TEXT"),
        ("destination", "TEXT"),
        ("departure_date", "TEXT"),
        ("price", "REAL"),
        ("currency", "TEXT"),
        ("score", "INTEGER"),
        ("level", "TEXT"),
        ("sent_at", "TEXT"),
    ]:
        add_column_if_missing(
            conn,
            "alerts",
            column,
            column_type
        )

    conn.execute("""
        INSERT OR IGNORE INTO radar_state (id, route_index)
        VALUES (1, 0)
    """)

    conn.commit()
    conn.close()


# =========================================================
# IGNAV
# =========================================================

def ignav_search(
    origin,
    destination,
    departure_date
):
    api_key = os.getenv("IGNAV_API_KEY")

    if not api_key:
        print("IGNAV_API_KEY bulunamadı.")
        return None

    passengers = SETTINGS.get("passengers", {})

    cabin = SETTINGS.get("cabin", {})

    if cabin.get("business", False):
        cabin_class = "business"
    else:
        cabin_class = "economy"

    connections = SETTINGS.get("connections", {})

    max_connections = connections.get(
        "max_connections",
        1
    )

    if connections.get("two_connections", False):
        max_connections = max(
            max_connections,
            2
        )

    if not connections.get("one_connection", True):
        max_connections = 0

    params = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": passengers.get("adults", 1),
        "children": passengers.get("children", 0),
        "infants_in_seat": 0,
        "infants_on_lap": passengers.get("infants", 0),
        "cabin_class": cabin_class,
        "max_stops": max_connections,
        "allow_self_transfer": connections.get(
            "self_transfer",
            False
        ),
    }

    max_price = SETTINGS.get(
        "price",
        {}
    ).get(
        "maximum_try",
        0
    )

    if max_price and max_price > 0:
        params["max_price"] = max_price

    airlines = SETTINGS.get(
        "airlines",
        {}
    ).get(
        "disabled",
        []
    )

    if airlines:
        params["airlines_exclude"] = airlines

    try:
        response = requests.post(
            IGNAV_URL,
            headers={
                "X-Api-Key": api_key,
                "Content-Type": "application/json"
            },
            json=params,
            timeout=60
        )

        if response.status_code != 200:
            print(
                f"Ignav hata: "
                f"{response.status_code} "
                f"{response.text[:500]}"
            )
            return None

        return response.json()

    except Exception as e:
        print(f"Ignav bağlantı hatası: {e}")
        return None


# =========================================================
# UÇUŞ ANAHTARI
# =========================================================

def create_flight_key(
    origin,
    destination,
    airline,
    flight_number,
    duration_minutes
):
    parts = [
        str(origin or ""),
        str(destination or ""),
        str(airline or ""),
        str(flight_number or ""),
        str(duration_minutes or "")
    ]

    return "|".join(parts)


# =========================================================
# BAGAJ
# =========================================================

def baggage_to_text(value):
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, dict):
        parts = []

        for key, val in value.items():
            parts.append(
                f"{key}:{baggage_to_text(val)}"
            )

        return " ".join(parts)

    if isinstance(value, list):
        return " ".join(
            baggage_to_text(x)
            for x in value
        )

    return str(value)


def has_checked_baggage(baggage):
    text = baggage_to_text(baggage).lower()

    if not text:
        return False

    negative_words = [
        "0 checked",
        "0 bag",
        "no checked",
        "none",
        "false"
    ]

    for word in negative_words:
        if word in text:
            return False

    return (
        "checked" in text
        or "checked_bag" in text
        or "checked bag" in text
    )


# =========================================================
# UÇUŞLARI ÇIKAR
# =========================================================

def extract_flights(
    data,
    origin,
    destination,
    departure_date
):
    flights = []

    if not data:
        return flights

    itineraries = data.get(
        "itineraries",
        []
    )

    seen = set()

    for itinerary in itineraries:

        outbound = itinerary.get(
            "outbound"
        )

        if not outbound:
            continue

        segments = outbound.get(
            "segments",
            []
        )

        if not segments:
            continue

        first_segment = segments[0]
        last_segment = segments[-1]

        carrier = (
            first_segment.get("carrier")
            or first_segment.get("airline")
            or ""
        )

        flight_number = (
            first_segment.get("flight_number")
            or first_segment.get("flightNumber")
            or ""
        )

        duration = outbound.get(
            "duration_minutes"
        )

        if duration is None:
            duration = outbound.get(
                "duration"
            )

        try:
            duration = int(duration or 0)
        except Exception:
            duration = 0

        price_obj = itinerary.get(
            "price",
            {}
        )

        price = price_obj.get(
            "amount"
        )

        currency = price_obj.get(
            "currency",
            "EUR"
        )

        if price is None:
            continue

        try:
            price = float(price)
        except Exception:
            continue

        baggage = (
            itinerary.get("bags")
            or itinerary.get("baggage")
            or {}
        )

        baggage_text = baggage_to_text(
            baggage
        )

        self_transfer = bool(
            itinerary.get(
                "requires_self_transfer",
                False
            )
        )

        flight_key = create_flight_key(
            origin,
            destination,
            carrier,
            flight_number,
            duration
        )

        duplicate_key = (
            flight_key,
            price,
            currency,
            self_transfer
        )

        if duplicate_key in seen:
            continue

        seen.add(duplicate_key)

        flights.append({
            "flight_key": flight_key,
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "airline": carrier,
            "flight_number": flight_number,
            "price": price,
            "currency": currency,
            "duration_minutes": duration,
            "baggage": baggage_text,
            "self_transfer": self_transfer,
        })

    return flights


# =========================================================
# FİYAT KAYDI
# =========================================================

def save_flight(flight):
    conn = get_connection()

    existing = conn.execute("""
        SELECT id
        FROM prices
        WHERE flight_key = ?
          AND departure_date = ?
          AND price = ?
          AND currency = ?
        LIMIT 1
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["price"],
        flight["currency"]
    )).fetchone()

    if not existing:
        conn.execute("""
            INSERT INTO prices (
                flight_key,
                origin,
                destination,
                departure_date,
                airline,
                flight_number,
                price,
                currency,
                duration_minutes,
                baggage,
                self_transfer,
                recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            flight["flight_key"],
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["airline"],
            flight["flight_number"],
            flight["price"],
            flight["currency"],
            flight["duration_minutes"],
            flight["baggage"],
            int(flight["self_transfer"]),
            utc_iso()
        ))

    # Her taramayı gözlem olarak ayrıca kaydet.
    conn.execute("""
        INSERT INTO price_observations (
            flight_key,
            origin,
            destination,
            departure_date,
            airline,
            flight_number,
            price,
            currency,
            duration_minutes,
            baggage,
            self_transfer,
            observed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        flight["flight_key"],
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["airline"],
        flight["flight_number"],
        flight["price"],
        flight["currency"],
        flight["duration_minutes"],
        flight["baggage"],
        int(flight["self_transfer"]),
        utc_iso()
    ))

    conn.commit()
    conn.close()


# =========================================================
# ROTA GEÇMİŞİ
# =========================================================

def get_route_history(
    origin,
    destination,
    currency=None,
    limit=200
):
    conn = get_connection()

    if currency:
        rows = conn.execute("""
            SELECT price
            FROM prices
            WHERE origin = ?
              AND destination = ?
              AND currency = ?
            ORDER BY recorded_at DESC
            LIMIT ?
        """, (
            origin,
            destination,
            currency,
            limit
        )).fetchall()
    else:
        rows = conn.execute("""
            SELECT price
            FROM prices
            WHERE origin = ?
              AND destination = ?
            ORDER BY recorded_at DESC
            LIMIT ?
        """, (
            origin,
            destination,
            limit
        )).fetchall()

    conn.close()

    return [
        float(row["price"])
        for row in rows
        if row["price"] is not None
    ]


# =========================================================
# AYNI UÇUŞ FİYAT GEÇMİŞİ
# =========================================================

def get_previous_flight_price(flight):
    conn = get_connection()

    row = conn.execute("""
        SELECT price
        FROM price_observations
        WHERE flight_key = ?
          AND departure_date = ?
          AND currency = ?
        ORDER BY observed_at DESC
        LIMIT 1 OFFSET 1
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["currency"]
    )).fetchone()

    conn.close()

    if not row:
        return None

    try:
        return float(row["price"])
    except Exception:
        return None


def get_observation_count(flight):
    conn = get_connection()

    row = conn.execute("""
        SELECT COUNT(*) AS count
        FROM price_observations
        WHERE flight_key = ?
          AND departure_date = ?
          AND currency = ?
    """, (
        flight["flight_key"],
        flight["departure_date"],
        flight["currency"]
    )).fetchone()

    conn.close()

    return int(row["count"] or 0)


# =========================================================
# NORMAL FİYAT
# =========================================================

def calculate_normal_price(
    origin,
    destination,
    currency
):
    history = get_route_history(
        origin,
        destination,
        currency
    )

    if not history:
        return {
            "normal_price": None,
            "confidence": "low",
            "sample_count": 0
        }

    normal = statistics.median(history)

    count = len(history)

    if count >= 20:
        confidence = "high"
    elif count >= 8:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "normal_price": normal,
        "confidence": confidence,
        "sample_count": count
    }


# =========================================================
# PUANLAMA
# =========================================================

def calculate_historical_anomaly(
    current_price,
    normal_price
):
    if not normal_price or normal_price <= 0:
        return 0

    ratio = current_price / normal_price

    if ratio <= 0.40:
        return 20

    if ratio <= 0.50:
        return 17

    if ratio <= 0.60:
        return 14

    if ratio <= 0.70:
        return 11

    if ratio <= 0.80:
        return 8

    if ratio <= 0.90:
        return 4

    return 0


def calculate_market_divergence(
    current_price,
    normal_price
):
    if not normal_price or normal_price <= 0:
        return 0

    ratio = current_price / normal_price

    if ratio <= 0.45:
        return 25

    if ratio <= 0.55:
        return 21

    if ratio <= 0.65:
        return 17

    if ratio <= 0.75:
        return 13

    if ratio <= 0.85:
        return 8

    if ratio <= 0.95:
        return 4

    return 0


def calculate_sudden_drop(
    current_price,
    previous_price
):
    if not previous_price or previous_price <= 0:
        return 0

    drop = (
        (previous_price - current_price)
        / previous_price
    ) * 100

    if drop >= 60:
        return 10

    if drop >= 45:
        return 9

    if drop >= 35:
        return 7

    if drop >= 25:
        return 5

    if drop >= 15:
        return 3

    return 0


def calculate_source_disagreement():
    # Şimdilik bağımsız ikinci kaynak yok.
    # Bu nedenle puan uydurulmuyor.
    return 0


def calculate_tax_anomaly():
    # Ignav toplam fiyat döndürüyor ancak
    # vergi bileşenleri ayrı bir karşılaştırma
    # olarak elimizde değil.
    return 0


def calculate_currency_anomaly():
    # Şimdilik güvenilir bir ikinci kur kaynağı
    # kullanılmadığı için puan verilmez.
    return 0


def calculate_fare_anomaly(flight):
    points = 0

    if has_checked_baggage(
        flight.get("baggage")
    ):
        points += 2

    if flight.get("self_transfer"):
        points += 3

    return min(points, 5)


def calculate_short_lived_persistence(flight):
    count = get_observation_count(flight)

    # İlk gözlemde "kısa ömürlü" puanı verilmez.
    # Yeterli geçmiş oluşmadan yapay puan üretmiyoruz.
    if count <= 1:
        return 0

    if count == 2:
        return 2

    return 0


def calculate_source_reliability(verified):
    return 5 if verified else 0


def calculate_error_score(
    flight,
    normal_price,
    previous_price,
    verified
):
    historical = calculate_historical_anomaly(
        flight["price"],
        normal_price
    )

    market = calculate_market_divergence(
        flight["price"],
        normal_price
    )

    sudden = calculate_sudden_drop(
        flight["price"],
        previous_price
    )

    source_disagreement = (
        calculate_source_disagreement()
    )

    tax_anomaly = calculate_tax_anomaly()

    currency_anomaly = (
        calculate_currency_anomaly()
    )

    fare_anomaly = calculate_fare_anomaly(
        flight
    )

    persistence = (
        calculate_short_lived_persistence(
            flight
        )
    )

    reliability = calculate_source_reliability(
        verified
    )

    score = (
        historical
        + market
        + sudden
        + source_disagreement
        + tax_anomaly
        + currency_anomaly
        + fare_anomaly
        + persistence
        + reliability
    )

    score = min(
        int(round(score)),
        100
    )

    components = {
        "historical_anomaly": historical,
        "market_divergence": market,
        "sudden_drop": sudden,
        "source_disagreement": source_disagreement,
        "tax_anomaly": tax_anomaly,
        "currency_anomaly": currency_anomaly,
        "fare_anomaly": fare_anomaly,
        "short_lived_persistence": persistence,
        "source_reliability": reliability,
    }

    return score, components


# =========================================================
# FIRSAT PUANI
# =========================================================

def calculate_opportunity_score(
    flight,
    normal_price,
    previous_price
):
    points = 0

    if normal_price and normal_price > 0:
        drop = (
            (normal_price - flight["price"])
            / normal_price
        ) * 100

        if drop >= 50:
            points += 50
        elif drop >= 40:
            points += 40
        elif drop >= 30:
            points += 30
        elif drop >= 20:
            points += 20
        elif drop >= 10:
            points += 10

    if previous_price and previous_price > 0:
        previous_drop = (
            (previous_price - flight["price"])
            / previous_price
        ) * 100

        if previous_drop >= 30:
            points += 20
        elif previous_drop >= 20:
            points += 15
        elif previous_drop >= 10:
            points += 10

    if not flight.get("self_transfer"):
        points += 10

    if has_checked_baggage(
        flight.get("baggage")
    ):
        points += 10

    duration = flight.get(
        "duration_minutes",
        0
    )

    if duration and duration <= 180:
        points += 10

    return min(points, 100)


# =========================================================
# SEVİYELER
# =========================================================

def get_alert_level(score):
    thresholds = SETTINGS.get(
        "scoring",
        {}
    ).get(
        "thresholds",
        {}
    )

    normal = thresholds.get(
        "normal",
        30
    )

    good = thresholds.get(
        "good_deal",
        50
    )

    suspicious = thresholds.get(
        "suspicious",
        70
    )

    candidate = thresholds.get(
        "candidate",
        85
    )

    critical = thresholds.get(
        "high_confidence",
        90
    )

    if score >= critical:
        return "KRİTİK HATA FİYATI"

    if score >= candidate:
        return "HATA FİYATI ADAYI"

    if score >= suspicious:
        return "ŞÜPHELİ FİYAT"

    if score >= good:
        return "İYİ FIRSAT"

    if score >= normal:
        return "NORMALİN ALTINDA"

    return "NORMAL"


# =========================================================
# NEDEN HATA FİYATI?
# =========================================================

def explain_error_fare(
    components,
    verified
):
    reasons = []

    if components.get(
        "historical_anomaly",
        0
    ) >= 8:
        reasons.append(
            "normal fiyatın belirgin altında"
        )

    if components.get(
        "market_divergence",
        0
    ) >= 13:
        reasons.append(
            "rota geçmişine göre olağandışı ucuz"
        )

    if components.get(
        "sudden_drop",
        0
    ) >= 6:
        reasons.append(
            "aynı uçuşta ani fiyat düşüşü"
        )

    if components.get(
        "fare_anomaly",
        0
    ) >= 3:
        reasons.append(
            "fare/self-transfer anomalisi"
        )
    elif components.get(
        "fare_anomaly",
        0
    ) > 0:
        reasons.append(
            "bagaj/fare avantajı"
        )

    if components.get(
        "source_reliability",
        0
    ) > 0 and verified:
        reasons.append(
            "ikinci Ignav aramasında doğrulandı"
        )

    if not reasons:
        reasons.append(
            "yeterli fiyat geçmişi oluşuyor"
        )

    return reasons


# =========================================================
# DOĞRULAMA
# =========================================================

def verification_cache_key(flight):
    return (
        flight["flight_key"],
        flight["departure_date"],
        flight["price"],
        flight["currency"],
        bool(flight["self_transfer"])
    )


def verify_flight(flight):
    data = ignav_search(
        flight["origin"],
        flight["destination"],
        flight["departure_date"]
    )

    if not data:
        save_verification(
            flight,
            False
        )
        return False

    flights = extract_flights(
        data,
        flight["origin"],
        flight["destination"],
        flight["departure_date"]
    )

    for candidate in flights:
        if (
            candidate["flight_key"]
            == flight["flight_key"]
            and abs(
                candidate["price"]
                - flight["price"]
            ) < 0.01
            and candidate["currency"]
            == flight["currency"]
            and bool(
                candidate["self_transfer"]
            )
            == bool(
                flight["self_transfer"]
            )
        ):
            save_verification(
                flight,
                True
            )
            return True

    save_verification(
        flight,
        False
    )

    return False


def save_verification(
    flight,
    verified
):
    conn = get_connection()

    columns = table_columns(
        conn,
        "verifications"
    )

    values = {}

    mapping = {
        "flight_key": flight.get(
            "flight_key"
        ),
        "origin": flight.get(
            "origin"
        ),
        "destination": flight.get(
            "destination"
        ),
        "departure_date": flight.get(
            "departure_date"
        ),
        "price": flight.get(
            "price"
        ),
        "currency": flight.get(
            "currency"
        ),
        "verified": int(
            bool(verified)
        ),
        "verification_time": utc_iso(),
    }

    for key, value in mapping.items():
        if key in columns:
            values[key] = value

    if not values:
        conn.close()
        return

    column_names = ", ".join(
        values.keys()
    )

    placeholders = ", ".join(
        "?" for _ in values
    )

    conn.execute(
        f"""
        INSERT INTO verifications (
            {column_names}
        )
        VALUES (
            {placeholders}
        )
        """,
        tuple(values.values())
    )

    conn.commit()
    conn.close()


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    enabled = SETTINGS.get(
        "alerts",
        {}
    ).get(
        "telegram_enabled",
        True
    )

    if not enabled:
        return False

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        print(
            "Telegram bilgileri bulunamadı."
        )
        return False

    url = TELEGRAM_URL.format(
        token
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True
            },
            timeout=30
        )

        if response.status_code != 200:
            print(
                "Telegram hata:",
                response.text[:500]
            )
            return False

        return True

    except Exception as e:
        print(
            f"Telegram bağlantı hatası: {e}"
        )
        return False


# =========================================================
# ALERT FİLTRESİ
# =========================================================

def alert_allowed_by_level(score):
    alerts = SETTINGS.get(
        "alerts",
        {}
    )

    minimum_score = alerts.get(
        "minimum_score",
        50
    )

    candidate_score = alerts.get(
        "candidate_score",
        85
    )

    critical_score = alerts.get(
        "critical_score",
        90
    )

    if score < minimum_score:
        return False

    if score >= critical_score:
        return alerts.get(
            "send_high_confidence_error_fares",
            True
        )

    if score >= candidate_score:
        return alerts.get(
            "send_error_fare_candidates",
            True
        )

    if score >= 70:
        return alerts.get(
            "send_suspicious_prices",
            True
        )

    return alerts.get(
        "send_good_deals",
        True
    )


# =========================================================
# TEKRAR ALERT KONTROLÜ
# =========================================================

def alert_already_sent(flight):
    hours = SETTINGS.get(
        "radar",
        {}
    ).get(
        "duplicate_alert_window_hours",
        24
    )

    cutoff = (
        utc_now()
        - timedelta(hours=hours)
    ).isoformat()

    conn = get_connection()

    row = conn.execute("""
        SELECT id
        FROM alerts
        WHERE origin = ?
          AND destination = ?
          AND departure_date = ?
          AND price = ?
          AND currency = ?
          AND sent_at >= ?
        LIMIT 1
    """, (
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["price"],
        flight["currency"],
        cutoff
    )).fetchone()

    conn.close()

    return row is not None


# =========================================================
# ALERT KAYDI
# =========================================================

def save_alert(
    flight,
    score,
    level
):
    conn = get_connection()

    conn.execute("""
        INSERT INTO alerts (
            flight_key,
            origin,
            destination,
            departure_date,
            price,
            currency,
            score,
            level,
            sent_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        flight["flight_key"],
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
        flight["price"],
        flight["currency"],
        score,
        level,
        utc_iso()
    ))

    conn.commit()
    conn.close()


# =========================================================
# TELEGRAM MESAJI
# =========================================================

def format_alert_message(
    flight,
    error_score,
    opportunity_score,
    normal_price,
    previous_price,
    components,
    verified
):
    level = get_alert_level(
        error_score
    )

    reasons = explain_error_fare(
        components,
        verified
    )

    reason_text = "\n".join(
        f"• {reason}"
        for reason in reasons
    )

    baggage = flight.get(
        "baggage"
    ) or "Belirtilmemiş"

    self_transfer = (
        "Evet"
        if flight.get("self_transfer")
        else "Hayır"
    )

    normal_text = (
        f"{normal_price:.2f} "
        f"{flight['currency']}"
        if normal_price
        else "Yeterli geçmiş yok"
    )

    previous_text = (
        f"{previous_price:.2f} "
        f"{flight['currency']}"
        if previous_price
        else "Yok"
    )

    verification_text = (
        "DOĞRULANDI"
        if verified
        else "DOĞRULANMADI"
    )

    return (
        f"✈️ UÇUŞ HATA FİYATI RADARI\n"
        f"\n"
        f"🚨 {level}\n"
        f"\n"
        f"📍 {flight['origin']} → "
        f"{flight['destination']}\n"
        f"📅 {flight['departure_date']}\n"
        f"🏷️ {flight['airline']} "
        f"{flight['flight_number']}\n"
        f"\n"
        f"💰 Fiyat: "
        f"{flight['price']:.2f} "
        f"{flight['currency']}\n"
        f"📊 Normal: {normal_text}\n"
        f"📉 Önceki: {previous_text}\n"
        f"\n"
        f"🎯 Hata Skoru: "
        f"{error_score}/100\n"
        f"⭐ Fırsat Skoru: "
        f"{opportunity_score}/100\n"
        f"🔎 Doğrulama: "
        f"{verification_text}\n"
        f"\n"
        f"❓ Neden dikkat çekti?\n"
        f"{reason_text}\n"
        f"\n"
        f"🧳 Bagaj: {baggage}\n"
        f"🔄 Self-transfer: "
        f"{self_transfer}\n"
        f"⏱️ Süre: "
        f"{flight['duration_minutes']} dk\n"
        f"\n"
        f"⚠️ Not: Bu skor bir hata fiyatı "
        f"olasılığıdır; biletin gerçekten "
        f"satın alınabilir olduğunu garanti etmez."
    )


# =========================================================
# ALERT OLUŞTUR
# =========================================================

def create_and_send_alert(
    flight,
    error_score,
    opportunity_score,
    normal_price,
    previous_price,
    components,
    verified
):
    if not verified:
        return False

    if not alert_allowed_by_level(
        error_score
    ):
        return False

    if alert_already_sent(
        flight
    ):
        print(
            "Tekrarlanan alert atlandı:",
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["price"]
        )
        return False

    message = format_alert_message(
        flight,
        error_score,
        opportunity_score,
        normal_price,
        previous_price,
        components,
        verified
    )

    sent = send_telegram(
        message
    )

    if sent:
        save_alert(
            flight,
            error_score,
            get_alert_level(
                error_score
            )
        )

        print(
            "Telegram alert gönderildi:",
            flight["origin"],
            "→",
            flight["destination"],
            error_score
        )

    return sent


# =========================================================
# ROTALAR
# =========================================================

def build_routes():
    airports = SETTINGS.get(
        "airports",
        {}
    )

    priority_origins = airports.get(
        "priority_origins",
        ["SZF"]
    )

    priority_destinations = airports.get(
        "priority_destinations",
        ["SZF"]
    )

    domestic_destinations = airports.get(
        "domestic_destinations",
        []
    )

    europe_destinations = airports.get(
        "europe_destinations",
        []
    )

    routes = []

    if airports.get(
        "domestic_enabled",
        True
    ):
        for origin in priority_origins:
            for destination in domestic_destinations:
                if origin != destination:
                    routes.append(
                        (origin, destination)
                    )

    if airports.get(
        "europe_enabled",
        True
    ):
        for origin in priority_origins:
            for destination in europe_destinations:
                if origin != destination:
                    routes.append(
                        (origin, destination)
                    )

        for destination in priority_destinations:
            for origin in europe_destinations:
                if origin != destination:
                    routes.append(
                        (origin, destination)
                    )

    # Aynı rotaları kaldır
    unique_routes = []
    seen = set()

    for route in routes:
        if route not in seen:
            seen.add(route)
            unique_routes.append(route)

    return unique_routes


# =========================================================
# ROTA ROTASYONU
# =========================================================

def get_routes_for_this_run():
    routes = build_routes()

    if not routes:
        return []

    radar = SETTINGS.get(
        "radar",
        {}
    )

    routes_per_run = int(
        radar.get(
            "routes_per_run",
            10
        )
    )

    conn = get_connection()

    row = conn.execute("""
        SELECT route_index
        FROM radar_state
        WHERE id = 1
    """).fetchone()

    start_index = (
        int(row["route_index"])
        if row
        else 0
    )

    selected = []

    for i in range(
        min(routes_per_run, len(routes))
    ):
        index = (
            start_index + i
        ) % len(routes)

        selected.append(
            routes[index]
        )

    new_index = (
        start_index
        + len(selected)
    ) % len(routes)

    conn.execute("""
        INSERT OR REPLACE INTO radar_state (
            id,
            route_index
        )
        VALUES (1, ?)
    """, (
        new_index,
    ))

    conn.commit()
    conn.close()

    return selected


# =========================================================
# TARİHLER
# =========================================================

def get_scan_dates():
    radar = SETTINGS.get(
        "radar",
        {}
    )

    days_ahead = radar.get(
        "dates_days_ahead",
        [30, 60, 90]
    )

    today = utc_now().date()

    dates = []

    for days in days_ahead:
        date_value = (
            today
            + timedelta(days=int(days))
        ).isoformat()

        dates.append(
            date_value
        )

    return dates


# =========================================================
# MAIN
# =========================================================

def main():
    print("=" * 60)
    print("UÇUŞ HATA FİYATI RADARI V2")
    print("=" * 60)

    init_db()

    routes = get_routes_for_this_run()
    dates = get_scan_dates()

    print(
        f"Toplam rota havuzu: "
        f"{len(build_routes())}"
    )

    print(
        f"Bu çalışmada taranacak rota: "
        f"{len(routes)}"
    )

    print(
        f"Tarihler: "
        f"{', '.join(dates)}"
    )

    radar = SETTINGS.get(
        "radar",
        {}
    )

    verification_settings = radar.get(
        "verification",
        {}
    )

    verification_enabled = (
        verification_settings.get(
            "enabled",
            True
        )
    )

    minimum_verification_score = (
        verification_settings.get(
            "minimum_score_for_verification",
            30
        )
    )

    total_searches = 0
    saved_prices = 0
    verified_count = 0
    alerts_sent = 0

    verification_cache = {}

    for origin, destination in routes:

        print(
            f"\nROTA: {origin} → {destination}"
        )

        for departure_date in dates:

            print(
                f"  Tarih: {departure_date}"
            )

            total_searches += 1

            data = ignav_search(
                origin,
                destination,
                departure_date
            )

            if not data:
                print(
                    "  Sonuç alınamadı."
                )
                continue

            flights = extract_flights(
                data,
                origin,
                destination,
                departure_date
            )

            print(
                f"  Bulunan uçuş: "
                f"{len(flights)}"
            )

            for flight in flights:

                normal_info = (
                    calculate_normal_price(
                        origin,
                        destination,
                        flight["currency"]
                    )
                )

                normal_price = (
                    normal_info["normal_price"]
                )

                previous_price = (
                    get_previous_flight_price(
                        flight
                    )
                )

                preliminary_score, _ = (
                    calculate_error_score(
                        flight,
                        normal_price,
                        previous_price,
                        False
                    )
                )

                save_flight(
                    flight
                )

                saved_prices += 1

                print(
                    f"    {flight['airline']} "
                    f"{flight['flight_number']} "
                    f"{flight['price']:.2f} "
                    f"{flight['currency']} "
                    f"| ön skor "
                    f"{preliminary_score}"
                )

                verified = False

                if (
                    verification_enabled
                    and preliminary_score
                    >= minimum_verification_score
                ):
                    cache_key = (
                        verification_cache_key(
                            flight
                        )
                    )

                    if cache_key in (
                        verification_cache
                    ):
                        verified = (
                            verification_cache[
                                cache_key
                            ]
                        )
                    else:
                        verified = (
                            verify_flight(
                                flight
                            )
                        )

                        verification_cache[
                            cache_key
                        ] = verified

                    if verified:
                        verified_count += 1

                final_score, components = (
                    calculate_error_score(
                        flight,
                        normal_price,
                        previous_price,
                        verified
                    )
                )

                opportunity_score = (
                    calculate_opportunity_score(
                        flight,
                        normal_price,
                        previous_price
                    )
                )

                print(
                    f"      SON SKOR: "
                    f"{final_score} "
                    f"| FIRSAT: "
                    f"{opportunity_score} "
                    f"| doğrulama: "
                    f"{verified}"
                )

                if (
                    final_score
                    >= SETTINGS.get(
                        "alerts",
                        {}
                    ).get(
                        "minimum_score",
                        50
                    )
                ):
                    sent = (
                        create_and_send_alert(
                            flight,
                            final_score,
                            opportunity_score,
                            normal_price,
                            previous_price,
                            components,
                            verified
                        )
                    )

                    if sent:
                        alerts_sent += 1

    print("\n" + "=" * 60)
    print("TARAMA TAMAMLANDI")
    print("=" * 60)

    print(
        f"API aramaları: {total_searches}"
    )

    print(
        f"Kaydedilen fiyatlar: "
        f"{saved_prices}"
    )

    print(
        f"Doğrulanan uçuşlar: "
        f"{verified_count}"
    )

    print(
        f"Telegram alert: "
        f"{alerts_sent}"
    )

    print("=" * 60)


# =========================================================
# HATA YAKALAMA
# =========================================================

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(
            "\nRADAR HATASI:"
        )
        print(
            repr(e)
        )
        raise
