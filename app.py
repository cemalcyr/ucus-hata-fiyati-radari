import os
import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

import requests


DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"


# ============================================================
# GENEL
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


def get_connection():
    return sqlite3.connect(DB_FILE)


def table_info(conn, table_name):
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table_name})")
    return cur.fetchall()


def table_columns(conn, table_name):
    return {
        row[1]
        for row in table_info(conn, table_name)
    }


def add_column_if_missing(
    conn,
    table_name,
    column_name,
    definition
):
    columns = table_columns(conn, table_name)

    if column_name not in columns:
        print(
            f"Veritabani guncelleniyor: "
            f"{table_name}.{column_name}"
        )

        conn.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {definition}
            """
        )


# ============================================================
# VERITABANI
# ============================================================

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    # --------------------------------------------------------
    # PRICES
    # --------------------------------------------------------

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            price REAL NOT NULL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            self_transfer INTEGER DEFAULT 0,
            seen_at TEXT,
            recorded_at TEXT
        )
        """
    )

    price_columns = {
        row[1]
        for row in table_info(conn, "prices")
    }

    price_fields = {
        "flight_key": "TEXT",
        "origin": "TEXT",
        "destination": "TEXT",
        "departure_date": "TEXT",
        "price": "REAL",
        "currency": "TEXT",
        "airline": "TEXT",
        "flight_number": "TEXT",
        "duration_minutes": "INTEGER",
        "baggage": "TEXT",
        "self_transfer": "INTEGER DEFAULT 0",
        "seen_at": "TEXT",
        "recorded_at": "TEXT"
    }

    for name, definition in price_fields.items():
        if name not in price_columns:
            cur.execute(
                f"""
                ALTER TABLE prices
                ADD COLUMN {name} {definition}
                """
            )

    now = utc_iso()

    cur.execute(
        """
        UPDATE prices
        SET seen_at = recorded_at
        WHERE seen_at IS NULL
          AND recorded_at IS NOT NULL
        """
    )

    cur.execute(
        """
        UPDATE prices
        SET recorded_at = seen_at
        WHERE recorded_at IS NULL
          AND seen_at IS NOT NULL
        """
    )

    cur.execute(
        """
        UPDATE prices
        SET seen_at = ?
        WHERE seen_at IS NULL
        """,
        (now,)
    )

    cur.execute(
        """
        UPDATE prices
        SET recorded_at = ?
        WHERE recorded_at IS NULL
        """,
        (now,)
    )

    cur.execute(
        """
        UPDATE prices
        SET self_transfer = 0
        WHERE self_transfer IS NULL
        """
    )

    # --------------------------------------------------------
    # PRICE OBSERVATIONS
    # --------------------------------------------------------

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            price REAL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            self_transfer INTEGER DEFAULT 0,
            observed_at TEXT
        )
        """
    )

    observation_fields = {
        "flight_key": "TEXT",
        "origin": "TEXT",
        "destination": "TEXT",
        "departure_date": "TEXT",
        "price": "REAL",
        "currency": "TEXT",
        "airline": "TEXT",
        "flight_number": "TEXT",
        "duration_minutes": "INTEGER",
        "baggage": "TEXT",
        "self_transfer": "INTEGER DEFAULT 0",
        "observed_at": "TEXT"
    }

    for name, definition in observation_fields.items():
        if name not in table_columns(
            conn,
            "price_observations"
        ):
            cur.execute(
                f"""
                ALTER TABLE price_observations
                ADD COLUMN {name} {definition}
                """
            )

    # --------------------------------------------------------
    # VERIFICATIONS
    # --------------------------------------------------------

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            price REAL NOT NULL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            baggage TEXT,
            self_transfer INTEGER DEFAULT 0,
            verified INTEGER DEFAULT 0,
            verification_reason TEXT,
            checked_at TEXT,
            verification_time TEXT
        )
        """
    )

    verification_fields = {
        "flight_key": "TEXT",
        "origin": "TEXT",
        "destination": "TEXT",
        "departure_date": "TEXT",
        "price": "REAL",
        "currency": "TEXT",
        "airline": "TEXT",
        "flight_number": "TEXT",
        "duration_minutes": "INTEGER",
        "baggage": "TEXT",
        "self_transfer": "INTEGER DEFAULT 0",
        "verified": "INTEGER DEFAULT 0",
        "verification_reason": "TEXT",
        "checked_at": "TEXT",
        "verification_time": "TEXT"
    }

    for name, definition in verification_fields.items():
        if name not in table_columns(
            conn,
            "verifications"
        ):
            cur.execute(
                f"""
                ALTER TABLE verifications
                ADD COLUMN {name} {definition}
                """
            )

    now = utc_iso()

    cur.execute(
        """
        UPDATE verifications
        SET checked_at = ?
        WHERE checked_at IS NULL
        """,
        (now,)
    )

    cur.execute(
        """
        UPDATE verifications
        SET verification_time = checked_at
        WHERE verification_time IS NULL
          AND checked_at IS NOT NULL
        """
    )

    cur.execute(
        """
        UPDATE verifications
        SET verified = 0
        WHERE verified IS NULL
        """
    )

    cur.execute(
        """
        UPDATE verifications
        SET self_transfer = 0
        WHERE self_transfer IS NULL
        """
    )

    # --------------------------------------------------------
    # ALERTS
    # --------------------------------------------------------

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT,
            destination TEXT,
            departure_date TEXT,
            price REAL NOT NULL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            score REAL,
            alert_level TEXT,
            sent_at TEXT
        )
        """
    )

    alert_fields = {
        "flight_key": "TEXT",
        "origin": "TEXT",
        "destination": "TEXT",
        "departure_date": "TEXT",
        "price": "REAL",
        "currency": "TEXT",
        "airline": "TEXT",
        "flight_number": "TEXT",
        "score": "REAL",
        "alert_level": "TEXT",
        "sent_at": "TEXT"
    }

    for name, definition in alert_fields.items():
        if name not in table_columns(
            conn,
            "alerts"
        ):
            cur.execute(
                f"""
                ALTER TABLE alerts
                ADD COLUMN {name} {definition}
                """
            )

    cur.execute(
        """
        UPDATE alerts
        SET sent_at = ?
        WHERE sent_at IS NULL
        """,
        (now,)
    )

    # --------------------------------------------------------
    # RADAR STATE
    # --------------------------------------------------------

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY,
            route_index INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )

    cur.execute(
        """
        SELECT id
        FROM radar_state
        WHERE id = 1
        """
    )

    if cur.fetchone() is None:
        cur.execute(
            """
            INSERT INTO radar_state (
                id,
                route_index,
                updated_at
            )
            VALUES (1, 0, ?)
            """,
            (now,)
        )

    conn.commit()
    conn.close()

    print("Veritabani kontrolu tamamlandi.")


# ============================================================
# IGNAV
# ============================================================

def ignav_search(
    origin,
    destination,
    departure_date
):
    api_key = os.getenv("IGNAV_API_KEY")

    if not api_key:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    url = "https://ignav.com/api/fares/one-way"

    passengers = settings.get(
        "passengers",
        {}
    )

    connections = settings.get(
        "connections",
        {}
    )

    cabin = "economy"

    if settings.get(
        "cabin",
        {}
    ).get(
        "business",
        False
    ):
        cabin = "business"

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": passengers.get(
            "adults",
            1
        ),
        "children": passengers.get(
            "children",
            0
        ),
        "infants_in_seat": 0,
        "infants_on_lap": passengers.get(
            "infants",
            0
        ),
        "cabin_class": cabin,
        "max_stops": connections.get(
            "max_connections",
            1
        ),
        "allow_self_transfer": connections.get(
            "self_transfer",
            False
        )
    }

    max_price = settings.get(
        "price",
        {}
    ).get(
        "maximum_try",
        0
    )

    if max_price and max_price > 0:
        payload["max_price"] = max_price

    disabled_airlines = settings.get(
        "airlines",
        {}
    ).get(
        "disabled",
        []
    )

    if disabled_airlines:
        payload["airlines_exclude"] = disabled_airlines

    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=60
        )

        print(
            f"Ignav: {origin}->{destination} "
            f"{departure_date} "
            f"HTTP {response.status_code}"
        )

        if response.status_code != 200:
            print(
                "Ignav API cevabi:",
                response.text[:1000]
            )
            return None

        return response.json()

    except Exception as e:
        print(
            "Ignav hatasi:",
            repr(e)
        )
        return None


# ============================================================
# UCUS ANAHTARI
# ============================================================

def create_flight_key(
    origin,
    destination,
    flight_number,
    airline,
    duration
):
    return (
        f"{origin}-"
        f"{destination}-"
        f"{airline}-"
        f"{flight_number}-"
        f"{duration}"
    )


# ============================================================
# BAGAJ
# ============================================================

def baggage_to_text(bags):
    if bags is None:
        return ""

    if isinstance(bags, str):
        return bags

    try:
        return json.dumps(
            bags,
            ensure_ascii=False
        )
    except Exception:
        return str(bags)


def has_checked_baggage(text):
    text = str(
        text or ""
    ).lower()

    checked_words = [
        "checked",
        "checked_bag",
        "checked bag",
        "bagaj",
        "valiz"
    ]

    for word in checked_words:
        if word in text:
            if "0" not in text:
                return True

            if (
                "1" in text
                or "2" in text
                or "included" in text
            ):
                return True

    return False


# ============================================================
# UCUSLARI AYIKLA
# ============================================================

def extract_flights(data):
    if not data:
        return []

    itineraries = data.get(
        "itineraries",
        []
    )

    if not isinstance(
        itineraries,
        list
    ):
        return []

    results = []
    seen = set()

    for item in itineraries:

        if not isinstance(
            item,
            dict
        ):
            continue

        price_info = item.get(
            "price",
            {}
        )

        outbound = item.get(
            "outbound",
            {}
        )

        if not isinstance(
            price_info,
            dict
        ):
            continue

        if not isinstance(
            outbound,
            dict
        ):
            continue

        price = price_info.get(
            "amount"
        )

        currency = price_info.get(
            "currency"
        )

        if (
            price is None
            or currency is None
        ):
            continue

        try:
            numeric_price = float(price)
        except Exception:
            continue

        segments = outbound.get(
            "segments",
            []
        )

        if not isinstance(
            segments,
            list
        ):
            continue

        if not segments:
            continue

        first_segment = segments[0]
        last_segment = segments[-1]

        if not isinstance(
            first_segment,
            dict
        ):
            continue

        if not isinstance(
            last_segment,
            dict
        ):
            continue

        origin = first_segment.get(
            "departure_airport",
            ""
        )

        destination = last_segment.get(
            "arrival_airport",
            ""
        )

        airline = (
            outbound.get("carrier")
            or first_segment.get(
                "marketing_carrier_code"
            )
            or ""
        )

        flight_number = first_segment.get(
            "flight_number",
            ""
        )

        duration = outbound.get(
            "duration_minutes",
            0
        )

        try:
            duration = int(
                duration or 0
            )
        except Exception:
            duration = 0

        baggage_text = baggage_to_text(
            item.get(
                "bags",
                {}
            )
        )

        self_transfer = bool(
            item.get(
                "requires_self_transfer",
                False
            )
        )

        flight_key = create_flight_key(
            origin,
            destination,
            flight_number,
            airline,
            duration
        )

        duplicate_key = (
            flight_key,
            round(
                numeric_price,
                2
            ),
            currency,
            self_transfer
        )

        if duplicate_key in seen:
            continue

        seen.add(
            duplicate_key
        )

        results.append(
            {
                "flight_key": flight_key,
                "origin": origin,
                "destination": destination,
                "price": numeric_price,
                "currency": currency,
                "airline": airline,
                "flight_number": flight_number,
                "duration_minutes": duration,
                "baggage": baggage_text,
                "self_transfer": self_transfer
            }
        )

    return results


# ============================================================
# FIYAT KAYDI
# ============================================================

def save_flight(
    flight,
    departure_date
):
    conn = get_connection()
    timestamp = utc_iso()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM prices
        WHERE flight_key = ?
          AND departure_date = ?
          AND price = ?
          AND currency = ?
        LIMIT 1
        """,
        (
            flight["flight_key"],
            departure_date,
            flight["price"],
            flight["currency"]
        )
    )

    existing = cur.fetchone()

    if existing:
        cur.execute(
            """
            UPDATE prices
            SET seen_at = ?,
                recorded_at = ?
            WHERE id = ?
            """,
            (
                timestamp,
                timestamp,
                existing[0]
            )
        )
    else:
        cur.execute(
            """
            INSERT INTO prices (
                flight_key,
                origin,
                destination,
                departure_date,
                price,
                currency,
                airline,
                flight_number,
                duration_minutes,
                baggage,
                self_transfer,
                seen_at,
                recorded_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?
            )
            """,
            (
                flight["flight_key"],
                flight["origin"],
                flight["destination"],
                departure_date,
                flight["price"],
                flight["currency"],
                flight["airline"],
                flight["flight_number"],
                flight["duration_minutes"],
                flight["baggage"],
                int(
                    flight["self_transfer"]
                ),
                timestamp,
                timestamp
            )
        )

    # Her taramayi ayri gozlem olarak tut.
    cur.execute(
        """
        INSERT INTO price_observations (
            flight_key,
            origin,
            destination,
            departure_date,
            price,
            currency,
            airline,
            flight_number,
            duration_minutes,
            baggage,
            self_transfer,
            observed_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        """,
        (
            flight["flight_key"],
            flight["origin"],
            flight["destination"],
            departure_date,
            flight["price"],
            flight["currency"],
            flight["airline"],
            flight["flight_number"],
            flight["duration_minutes"],
            flight["baggage"],
            int(
                flight["self_transfer"]
            ),
            timestamp
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# ROTA FIYAT GECMISI
# ============================================================

def get_route_history(
    origin,
    destination,
    currency,
    current_price=None
):
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT price
        FROM price_observations
        WHERE origin = ?
          AND destination = ?
          AND currency = ?
    """

    params = [
        origin,
        destination,
        currency
    ]

    if current_price is not None:
        query += """
            AND price != ?
        """
        params.append(
            current_price
        )

    query += """
        ORDER BY observed_at DESC
        LIMIT 200
    """

    cur.execute(
        query,
        params
    )

    rows = cur.fetchall()
    conn.close()

    return [
        float(row[0])
        for row in rows
        if row[0] is not None
    ]


def calculate_normal_price(
    origin,
    destination,
    currency,
    current_price=None
):
    history = get_route_history(
        origin,
        destination,
        currency,
        current_price
    )

    if len(history) < 3:
        return {
            "normal_price": None,
            "sample_count": len(history),
            "confidence": "yetersiz_veri"
        }

    normal = statistics.median(
        history
    )

    if len(history) >= 20:
        confidence = "yuksek"
    elif len(history) >= 8:
        confidence = "orta"
    else:
        confidence = "dusuk"

    return {
        "normal_price": round(
            normal,
            2
        ),
        "sample_count": len(history),
        "confidence": confidence
    }


# ============================================================
# AYNI UCUSUN ONCEKI FIYATI
# ============================================================

def get_previous_flight_price(
    flight,
    departure_date
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT price
        FROM price_observations
        WHERE flight_key = ?
          AND departure_date = ?
          AND currency = ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (
            flight["flight_key"],
            departure_date,
            flight["currency"]
        )
    )

    row = cur.fetchone()

    conn.close()

    if row and row[0] is not None:
        return float(row[0])

    return None


def get_observation_count(
    flight,
    departure_date
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM price_observations
        WHERE flight_key = ?
          AND departure_date = ?
          AND currency = ?
        """,
        (
            flight["flight_key"],
            departure_date,
            flight["currency"]
        )
    )

    row = cur.fetchone()

    conn.close()

    return int(
        row[0]
        if row and row[0] is not None
        else 0
    )


# ============================================================
# ANI DUSUS
# ============================================================

def calculate_sudden_drop(
    current_price,
    previous_price
):
    if (
        previous_price is None
        or previous_price <= 0
    ):
        return {
            "drop_percent": 0,
            "points": 0
        }

    drop = (
        (
            previous_price -
            current_price
        )
        /
        previous_price
    ) * 100

    if drop >= 50:
        points = 10
    elif drop >= 30:
        points = 8
    elif drop >= 20:
        points = 6
    elif drop >= 10:
        points = 3
    elif drop > 0:
        points = 1
    else:
        points = 0

    return {
        "drop_percent": round(
            drop,
            2
        ),
        "points": points
    }


# ============================================================
# PIYASA SAPMASI
# ============================================================

def calculate_market_divergence(
    current_price,
    normal_price
):
    if (
        normal_price is None
        or normal_price <= 0
    ):
        return {
            "drop_percent": 0,
            "market_points": 0
        }

    drop = (
        (
            normal_price -
            current_price
        )
        /
        normal_price
    ) * 100

    if drop >= 70:
        points = 25
    elif drop >= 60:
        points = 22
    elif drop >= 50:
        points = 19
    elif drop >= 40:
        points = 16
    elif drop >= 30:
        points = 13
    elif drop >= 20:
        points = 9
    elif drop >= 10:
        points = 5
    elif drop > 0:
        points = 2
    else:
        points = 0

    return {
        "drop_percent": round(
            drop,
            2
        ),
        "market_points": points
    }


# ============================================================
# DIGER ANOMALILER
# ============================================================

def calculate_source_disagreement(
    flight,
    verification
):
    # Su anda ikinci kaynak yok.
    # Ayni Ignav aramasinin tekraridir.
    return 0


def calculate_tax_anomaly(flight):
    # Ignav cevabinda ayrintili vergi kalemleri
    # her zaman bulunmadigi icin uydurma puan vermiyoruz.
    return 0


def calculate_currency_anomaly(currency):
    # Kur servisi eklenene kadar 0.
    return 0


def calculate_fare_anomaly(flight):
    points = 0

    baggage = str(
        flight.get(
            "baggage",
            ""
        )
    ).lower()

    if (
        "0" in baggage
        and (
            "checked" in baggage
            or "bag" in baggage
        )
    ):
        points += 2

    if flight.get(
        "self_transfer",
        False
    ):
        points += 3

    return min(
        points,
        5
    )


def calculate_short_lived_persistence(
    flight,
    departure_date
):
    count = get_observation_count(
        flight,
        departure_date
    )

    # Ilk gozlem icin puan vermiyoruz.
    # Iki gozlem varsa az miktarda puan.
    # Uzun sure goruluyorsa hata fiyatindan
    # ziyade kalici kampanya olma ihtimali artar.

    if count <= 1:
        return 0

    if count == 2:
        return 2

    return 0


def calculate_source_reliability(
    verification
):
    if verification.get(
        "verified",
        False
    ):
        return 5

    return 0


# ============================================================
# HATA SKORU
# ============================================================

def calculate_error_score(
    flight,
    normal_data,
    previous_price=None,
    verification=None,
    departure_date=None
):
    if verification is None:
        verification = {
            "verified": False
        }

    normal_price = normal_data.get(
        "normal_price"
    )

    # 1 - Tarihsel anomali / 20
    historical_points = 0

    if (
        normal_price is not None
        and normal_price > 0
    ):
        drop = (
            (
                normal_price -
                flight["price"]
            )
            /
            normal_price
        ) * 100

        if drop >= 70:
            historical_points = 20
        elif drop >= 60:
            historical_points = 17
        elif drop >= 50:
            historical_points = 14
        elif drop >= 40:
            historical_points = 11
        elif drop >= 30:
            historical_points = 8
        elif drop >= 20:
            historical_points = 5
        elif drop >= 10:
            historical_points = 2

    # 2 - Piyasa sapmasi / 25
    market = calculate_market_divergence(
        flight["price"],
        normal_price
    )

    # 3 - Ani dusus / 10
    sudden = calculate_sudden_drop(
        flight["price"],
        previous_price
    )

    # 4 - Kaynak farki / 10
    source_points = calculate_source_disagreement(
        flight,
        verification
    )

    # 5 - Vergi / 10
    tax_points = calculate_tax_anomaly(
        flight
    )

    # 6 - Kur / 5
    currency_points = calculate_currency_anomaly(
        flight["currency"]
    )

    # 7 - Fare / 5
    fare_points = calculate_fare_anomaly(
        flight
    )

    # 8 - Kisa sure / 5
    persistence_points = 0

    if departure_date:
        persistence_points = (
            calculate_short_lived_persistence(
                flight,
                departure_date
            )
        )

    # 9 - Kaynak guvenilirligi / 5
    reliability_points = calculate_source_reliability(
        verification
    )

    score = (
        historical_points
        + market["market_points"]
        + sudden["points"]
        + source_points
        + tax_points
        + currency_points
        + fare_points
        + persistence_points
        + reliability_points
    )

    score = min(
        score,
        100
    )

    return {
        "score": round(
            score,
            2
        ),
        "historical_points": historical_points,
        "market_points": market["market_points"],
        "sudden_drop_points": sudden["points"],
        "source_disagreement_points": source_points,
        "tax_points": tax_points,
        "currency_points": currency_points,
        "fare_points": fare_points,
        "persistence_points": persistence_points,
        "reliability_points": reliability_points,
        "normal_price": normal_price,
        "drop_percent": market["drop_percent"],
        "previous_drop_percent": sudden["drop_percent"],
        "sample_count": normal_data.get(
            "sample_count",
            0
        ),
        "confidence": normal_data.get(
            "confidence",
            "yetersiz_veri"
        )
    }


# ============================================================
# FIRSAT SKORU
# ============================================================

def calculate_opportunity_score(
    flight,
    normal_data,
    previous_price=None
):
    score = 0

    normal_price = normal_data.get(
        "normal_price"
    )

    if (
        normal_price is not None
        and normal_price > 0
    ):
        drop = (
            (
                normal_price -
                flight["price"]
            )
            /
            normal_price
        ) * 100

        if drop >= 50:
            score += 50
        elif drop >= 40:
            score += 40
        elif drop >= 30:
            score += 30
        elif drop >= 20:
            score += 20
        elif drop >= 10:
            score += 10

    if (
        previous_price is not None
        and previous_price > 0
    ):
        drop = (
            (
                previous_price -
                flight["price"]
            )
            /
            previous_price
        ) * 100

        if drop >= 30:
            score += 20
        elif drop >= 20:
            score += 15
        elif drop >= 10:
            score += 10

    if not flight.get(
        "self_transfer",
        False
    ):
        score += 10

    if has_checked_baggage(
        flight.get(
            "baggage",
            ""
        )
    ):
        score += 10

    duration = flight.get(
        "duration_minutes",
        0
    )

    if duration and duration <= 180:
        score += 10

    return round(
        min(
            score,
            100
        ),
        2
    )


# ============================================================
# DOGRULAMA
# ============================================================

def verify_flight(
    flight,
    departure_date
):
    data = ignav_search(
        flight["origin"],
        flight["destination"],
        departure_date
    )

    if not data:
        return {
            "verified": False,
            "reason": "Ikinci Ignav aramasi basarisiz."
        }

    flights = extract_flights(
        data
    )

    for item in flights:

        if item["flight_key"] != flight["flight_key"]:
            continue

        price_match = (
            abs(
                item["price"] -
                flight["price"]
            ) < 0.01
        )

        currency_match = (
            item["currency"] ==
            flight["currency"]
        )

        self_transfer_match = (
            item["self_transfer"] ==
            flight["self_transfer"]
        )

        if (
            price_match
            and currency_match
            and self_transfer_match
        ):
            return {
                "verified": True,
                "reason": (
                    "Ayni ucus, fiyat, para birimi "
                    "ve self-transfer durumu ikinci "
                    "Ignav aramasinda tekrar bulundu."
                )
            }

    return {
        "verified": False,
        "reason": (
            "Ikinci aramada ayni fiyatli "
            "ucus bulunamadi."
        )
    }


# ============================================================
# VERIFICATION KAYDI
# ============================================================

def save_verification(
    flight,
    departure_date,
    verified,
    reason
):
    conn = get_connection()

    try:
        columns_info = table_info(
            conn,
            "verifications"
        )

        columns = {
            row[1]
            for row in columns_info
        }

        now = utc_iso()

        values = {
            "flight_key": flight.get(
                "flight_key"
            ),
            "origin": flight.get(
                "origin"
            ),
            "destination": flight.get(
                "destination"
            ),
            "departure_date": departure_date,
            "price": flight.get(
                "price"
            ),
            "currency": flight.get(
                "currency"
            ),
            "airline": flight.get(
                "airline"
            ),
            "flight_number": flight.get(
                "flight_number"
            ),
            "duration_minutes": flight.get(
                "duration_minutes"
            ),
            "baggage": flight.get(
                "baggage"
            ),
            "self_transfer": int(
                bool(
                    flight.get(
                        "self_transfer",
                        False
                    )
                )
            ),
            "verified": int(
                bool(verified)
            ),
            "verification_reason": reason,
            "checked_at": now,
            "verification_time": now
        }

        insert_columns = []
        insert_values = []

        for name, value in values.items():

            if name in columns:
                insert_columns.append(
                    name
                )
                insert_values.append(
                    value
                )

        # Eski veritabani yapisinda
        # checked_at zorunluysa mutlaka yaz.
        if (
            "checked_at" in columns
            and "checked_at" not in insert_columns
        ):
            insert_columns.append(
                "checked_at"
            )
            insert_values.append(
                now
            )

        if not insert_columns:
            return

        columns_sql = ", ".join(
            insert_columns
        )

        placeholders = ", ".join(
            "?"
            for _ in insert_columns
        )

        conn.execute(
            f"""
            INSERT INTO verifications (
                {columns_sql}
            )
            VALUES (
                {placeholders}
            )
            """,
            insert_values
        )

        conn.commit()

    except sqlite3.IntegrityError as e:
        print(
            "Verification kayit hatasi:",
            repr(e)
        )

        conn.rollback()

    finally:
        conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        settings.get(
            "alerts",
            {}
        ).get(
            "telegram_enabled",
            True
        )
        and os.getenv(
            "TELEGRAM_BOT_TOKEN"
        )
        and os.getenv(
            "TELEGRAM_CHAT_ID"
        )
    )


def send_telegram_message(message):
    if not telegram_enabled():
        print(
            "Telegram aktif degil."
        )
        return False

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message
            },
            timeout=30
        )

        if response.status_code != 200:
            print(
                "Telegram hatasi:",
                response.text[:500]
            )
            return False

        return True

    except Exception as e:
        print(
            "Telegram baglanti hatasi:",
            repr(e)
        )
        return False


# ============================================================
# ALERT SEVIYESI
# ============================================================

def get_threshold(
    name,
    default
):
    return settings.get(
        "scoring",
        {}
    ).get(
        "thresholds",
        {}
    ).get(
        name,
        default
    )


def get_alert_level(score):
    normal = get_threshold(
        "normal",
        30
    )

    good = get_threshold(
        "good_deal",
        50
    )

    suspicious = get_threshold(
        "suspicious",
        70
    )

    candidate = get_threshold(
        "candidate",
        85
    )

    high = get_threshold(
        "high_confidence",
        90
    )

    if score >= high:
        return "KRITIK HATA FIYATI"

    if score >= candidate:
        return "HATA FIYATI ADAYI"

    if score >= suspicious:
        return "SUPHELI FIYAT"

    if score >= good:
        return "IYI FIRSAT"

    if score >= normal:
        return "NORMAL USTU"

    return "NORMAL"


def alert_allowed_by_level(
    score
):
    alerts = settings.get(
        "alerts",
        {}
    )

    minimum = alerts.get(
        "minimum_score",
        50
    )

    if score < minimum:
        return False

    if score >= get_threshold(
        "high_confidence",
        90
    ):
        return alerts.get(
            "send_high_confidence_error_fares",
            True
        )

    if score >= get_threshold(
        "candidate",
        85
    ):
        return alerts.get(
            "send_error_fare_candidates",
            True
        )

    if score >= get_threshold(
        "suspicious",
        70
    ):
        return alerts.get(
            "send_suspicious_prices",
            True
        )

    return alerts.get(
        "send_good_deals",
        True
    )


# ============================================================
# ALERT TEKRAR KONTROLU
# ============================================================

def alert_already_sent(
    flight,
    departure_date
):
    conn = get_connection()
    cur = conn.cursor()

    window_hours = settings.get(
        "radar",
        {}
    ).get(
        "duplicate_alert_window_hours",
        24
    )

    cutoff = (
        utc_now()
        -
        timedelta(
            hours=window_hours
        )
    ).isoformat()

    cur.execute(
        """
        SELECT id
        FROM alerts
        WHERE origin = ?
          AND destination = ?
          AND departure_date = ?
          AND price = ?
          AND currency = ?
          AND sent_at >= ?
        LIMIT 1
        """,
        (
            flight["origin"],
            flight["destination"],
            departure_date,
            flight["price"],
            flight["currency"],
            cutoff
        )
    )

    found = cur.fetchone()

    conn.close()

    return found is not None


# ============================================================
# HATA FIYATI ACIKLAMASI
# ============================================================

def explain_error_fare(
    score_data
):
    reasons = []

    if score_data["historical_points"] >= 11:
        reasons.append(
            "normal fiyata gore cok yuksek tarihsel sapma"
        )

    if score_data["market_points"] >= 13:
        reasons.append(
            "rota normal fiyatina gore ciddi piyasa sapmasi"
        )

    if score_data["sudden_drop_points"] >= 6:
        reasons.append(
            "onceki gozleme gore ani fiyat dususu"
        )

    if score_data["fare_points"] >= 2:
        reasons.append(
            "fare yapisinda dikkat cekici ozellik"
        )

    if score_data["persistence_points"] > 0:
        reasons.append(
            "fiyat davranisi henuz kalici gorunmuyor"
        )

    if not reasons:
        reasons.append(
            "fiyat normal seviyenin belirgin altinda"
        )

    return reasons


# ============================================================
# ALERT OLUSTUR
# ============================================================

def create_and_send_alert(
    flight,
    departure_date,
    score_data,
    opportunity_score,
    verification
):
    score = score_data["score"]

    if not verification.get(
        "verified",
        False
    ):
        return False

    if not alert_allowed_by_level(
        score
    ):
        return False

    if alert_already_sent(
        flight,
        departure_date
    ):
        print(
            "Ayni alert daha once gonderilmis."
        )
        return False

    level = get_alert_level(
        score
    )

    normal_price = score_data[
        "normal_price"
    ]

    reasons = explain_error_fare(
        score_data
    )

    message = (
        f"{level}\n\n"
        f"Ucus: "
        f"{flight['origin']} -> "
        f"{flight['destination']}\n"
        f"Tarih: {departure_date}\n\n"
        f"Fiyat: "
        f"{flight['price']:.2f} "
        f"{flight['currency']}\n"
    )

    if normal_price is not None:
        message += (
            f"Normal fiyat: "
            f"{normal_price:.2f} "
            f"{flight['currency']}\n"
            f"Normalden sapma: "
            f"%{score_data['drop_percent']:.1f}\n"
            f"Gecmis veri: "
            f"{score_data['sample_count']} kayit\n"
        )

    message += (
        f"\nHATA SKORU: "
        f"{score:.1f}/100\n"
        f"FIRSAT SKORU: "
        f"{opportunity_score:.1f}/100\n\n"
        f"Neden dikkat cekti?\n"
    )

    for reason in reasons:
        message += (
            f"- {reason}\n"
        )

    message += (
        f"\nSkor detaylari:\n"
        f"Tarihsel anomali: "
        f"{score_data['historical_points']}/20\n"
        f"Piyasa sapmasi: "
        f"{score_data['market_points']}/25\n"
        f"Ani dusus: "
        f"{score_data['sudden_drop_points']}/10\n"
        f"Kaynak farki: "
        f"{score_data['source_disagreement_points']}/10\n"
        f"Vergi/ucret: "
        f"{score_data['tax_points']}/10\n"
        f"Kur anomalisi: "
        f"{score_data['currency_points']}/5\n"
        f"Fare anomalisi: "
        f"{score_data['fare_points']}/5\n"
        f"Kisa sureli davranis: "
        f"{score_data['persistence_points']}/5\n"
        f"Kaynak guvenilirligi: "
        f"{score_data['reliability_points']}/5\n\n"
        f"Havayolu: "
        f"{flight['airline']}\n"
        f"Ucus No: "
        f"{flight['flight_number']}\n"
        f"Sure: "
        f"{flight['duration_minutes']} dk\n"
        f"Bagaj: "
        f"{flight['baggage']}\n\n"
        f"Dogrulama: BASARILI"
    )

    if not send_telegram_message(
        message
    ):
        return False

    conn = get_connection()

    try:
        columns_info = table_info(
            conn,
            "alerts"
        )

        columns = {
            row[1]
            for row in columns_info
        }

        values = {
            "flight_key": flight[
                "flight_key"
            ],
            "origin": flight[
                "origin"
            ],
            "destination": flight[
                "destination"
            ],
            "departure_date": departure_date,
            "price": flight[
                "price"
            ],
            "currency": flight[
                "currency"
            ],
            "airline": flight[
                "airline"
            ],
            "flight_number": flight[
                "flight_number"
            ],
            "score": score,
            "alert_level": level,
            "sent_at": utc_iso()
        }

        insert_columns = []
        insert_values = []

        for name, value in values.items():
            if name in columns:
                insert_columns.append(
                    name
                )
                insert_values.append(
                    value
                )

        if insert_columns:
            columns_sql = ", ".join(
                insert_columns
            )

            placeholders = ", ".join(
                "?"
                for _ in insert_columns
            )

            conn.execute(
                f"""
                INSERT INTO alerts (
                    {columns_sql}
                )
                VALUES (
                    {placeholders}
                )
                """,
                insert_values
            )

            conn.commit()

    except Exception as e:
        print(
            "Alert veritabani kayit hatasi:",
            repr(e)
        )

    finally:
        conn.close()

    return True


# ============================================================
# ROTALAR
# ============================================================

def build_routes():
    airports = settings.get(
        "airports",
        {}
    )

    priority_origins = airports.get(
        "priority_origins",
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
                        (
                            origin,
                            destination
                        )
                    )

    if airports.get(
        "europe_enabled",
        True
    ):
        for origin in priority_origins:
            for destination in europe_destinations:
                if origin != destination:
                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

        for destination in priority_origins:
            for origin in europe_destinations:
                if origin != destination:
                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

    unique = []
    seen = set()

    for route in routes:
        if route in seen:
            continue

        seen.add(route)
        unique.append(route)

    return unique


# ============================================================
# ROTA DONUSUMU
# ============================================================

def get_next_routes(
    all_routes
):
    if not all_routes:
        return [], 0

    radar = settings.get(
        "radar",
        {}
    )

    route_count = int(
        radar.get(
            "routes_per_run",
            10
        )
    )

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT route_index
        FROM radar_state
        WHERE id = 1
        """
    )

    row = cur.fetchone()

    if row:
        try:
            current_index = int(
                row[0] or 0
            )
        except Exception:
            current_index = 0
    else:
        current_index = 0

        cur.execute(
            """
            INSERT OR REPLACE INTO radar_state (
                id,
                route_index,
                updated_at
            )
            VALUES (
                1, 0, ?
            )
            """,
            (
                utc_iso(),
            )
        )

    total = len(
        all_routes
    )

    selected = []

    for offset in range(
        min(
            route_count,
            total
        )
    ):
        index = (
            current_index +
            offset
        ) % total

        selected.append(
            all_routes[index]
        )

    next_index = (
        current_index +
        len(selected)
    ) % total

    cur.execute(
        """
        UPDATE radar_state
        SET route_index = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            next_index,
            utc_iso()
        )
    )

    conn.commit()
    conn.close()

    return selected, current_index


# ============================================================
# TARAMA TARIHLERI
# ============================================================

def build_departure_dates():
    radar = settings.get(
        "radar",
        {}
    )

    configured_days = radar.get(
        "dates_days_ahead",
        [30, 60, 90]
    )

    base = utc_now()

    dates = []

    for day_count in configured_days:

        try:
            day_count = int(
                day_count
            )
        except Exception:
            continue

        date_value = (
            base +
            timedelta(
                days=day_count
            )
        ).strftime(
            "%Y-%m-%d"
        )

        dates.append(
            date_value
        )

    return dates


# ============================================================
# ANA PROGRAM
# ============================================================

def main():

    print("")
    print("=" * 60)
    print(
        "UÇUŞ HATA FİYATI RADARI V2"
    )
    print("=" * 60)

    init_db()

    routes_all = build_routes()

    routes, route_start = get_next_routes(
        routes_all
    )

    departure_dates = build_departure_dates()

    print(
        f"Toplam rota havuzu: "
        f"{len(routes_all)}"
    )

    print(
        f"Bu çalışmada taranacak rota: "
        f"{len(routes)}"
    )

    print(
        f"Başlangıç rota sırası: "
        f"{route_start}"
    )

    print(
        "Tarihler: "
        +
        ", ".join(
            departure_dates
        )
    )

    print(
        f"Tahmini ilk API araması: "
        f"{len(routes) * len(departure_dates)}"
    )

    print(
        "Telegram: "
        +
        (
            "AKTIF"
            if telegram_enabled()
            else "KAPALI"
        )
    )

    total_searches = 0
    total_flights = 0
    total_verified = 0
    total_alerts = 0

    verification_minimum = settings.get(
        "radar",
        {}
    ).get(
        "verification",
        {}
    ).get(
        "minimum_score_for_verification",
        30
    )

    for origin, destination in routes:

        for departure_date in departure_dates:

            print("")
            print(
                f"Araniyor: "
                f"{origin} -> "
                f"{destination} | "
                f"{departure_date}"
            )

            data = ignav_search(
                origin,
                destination,
                departure_date
            )

            total_searches += 1

            flights = extract_flights(
                data
            )

            print(
                f"Bulunan ucus: "
                f"{len(flights)}"
            )

            total_flights += len(
                flights
            )

            for flight in flights:

                previous_price = (
                    get_previous_flight_price(
                        flight,
                        departure_date
                    )
                )

                normal_data = (
                    calculate_normal_price(
                        flight["origin"],
                        flight["destination"],
                        flight["currency"],
                        flight["price"]
                    )
                )

                # İlk skor.
                preliminary = (
                    calculate_error_score(
                        flight,
                        normal_data,
                        previous_price,
                        {
                            "verified": False
                        },
                        departure_date
                    )
                )

                opportunity_score = (
                    calculate_opportunity_score(
                        flight,
                        normal_data,
                        previous_price
                    )
                )

                print(
                    f"  {flight['airline']} "
                    f"{flight['flight_number']} "
                    f"{flight['price']:.2f} "
                    f"{flight['currency']} "
                    f"-> on skor "
                    f"{preliminary['score']:.1f}"
                )

                save_flight(
                    flight,
                    departure_date
                )

                # Yeterince dikkat cekmiyorsa
                # ikinci API aramasi yapma.
                if (
                    preliminary["score"]
                    <
                    verification_minimum
                ):
                    continue

                print(
                    "    -> DOGRULAMA BASLIYOR"
                )

                verification = verify_flight(
                    flight,
                    departure_date
                )

                save_verification(
                    flight,
                    departure_date,
                    verification.get(
                        "verified",
                        False
                    ),
                    verification.get(
                        "reason",
                        ""
                    )
                )

                if verification.get(
                    "verified",
                    False
                ):
                    total_verified += 1

                # Dogrulama sonrasi gercek skor.
                final_score_data = (
                    calculate_error_score(
                        flight,
                        normal_data,
                        previous_price,
                        verification,
                        departure_date
                    )
                )

                final_score = final_score_data[
                    "score"
                ]

                final_opportunity = (
                    calculate_opportunity_score(
                        flight,
                        normal_data,
                        previous_price
                    )
                )

                print(
                    f"    -> final skor: "
                    f"{final_score:.1f}"
                )

                if create_and_send_alert(
                    flight,
                    departure_date,
                    final_score_data,
                    final_opportunity,
                    verification
                ):
                    total_alerts += 1

    print("")
    print("=" * 60)
    print("TARAMA TAMAMLANDI")
    print("=" * 60)

    print(
        f"API aramasi: {total_searches}"
    )

    print(
        f"Bulunan ucus: {total_flights}"
    )

    print(
        f"Dogrulanan: {total_verified}"
    )

    print(
        f"Gonderilen alert: {total_alerts}"
    )

    print("=" * 60)


if __name__ == "__main__":
    try:
        main()

    except Exception as e:
        print("")
        print(
            "RADAR HATASI:"
        )
        print(
            repr(e)
        )
        raise
