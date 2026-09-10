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


def utc_now_iso():
    return utc_now().isoformat()


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


def get_connection():
    return sqlite3.connect(DB_FILE)


def get_table_info(cursor, table_name):
    cursor.execute(
        f"PRAGMA table_info({table_name})"
    )
    return cursor.fetchall()


def get_existing_columns(cursor, table_name):
    return {
        row[1]
        for row in get_table_info(
            cursor,
            table_name
        )
    }


def add_missing_column(
    cursor,
    table_name,
    column_name,
    definition,
    existing_columns
):
    if column_name not in existing_columns:

        print(
            f"Veritabani guncelleniyor: "
            f"{table_name}.{column_name} ekleniyor."
        )

        cursor.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {definition}
            """
        )

        existing_columns.add(column_name)


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

    price_columns = get_existing_columns(
        cur,
        "prices"
    )

    price_required = {
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

    for column_name, definition in price_required.items():

        add_missing_column(
            cur,
            "prices",
            column_name,
            definition,
            price_columns
        )

    now = utc_now_iso()

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
            checked_at TEXT
        )
        """
    )

    verification_columns = get_existing_columns(
        cur,
        "verifications"
    )

    verification_required = {
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
        "checked_at": "TEXT"
    }

    for column_name, definition in verification_required.items():

        add_missing_column(
            cur,
            "verifications",
            column_name,
            definition,
            verification_columns
        )

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

    alert_columns = get_existing_columns(
        cur,
        "alerts"
    )

    alert_required = {
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

    for column_name, definition in alert_required.items():

        add_missing_column(
            cur,
            "alerts",
            column_name,
            definition,
            alert_columns
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

    state = cur.fetchone()

    if state is None:

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

    print(
        "Veritabani kontrolu ve guncellemesi tamamlandi."
    )


# ============================================================
# IGNAV
# ============================================================

def ignav_search(
    origin,
    destination,
    departure_date
):

    api_key = os.getenv(
        "IGNAV_API_KEY"
    )

    if not api_key:

        print(
            "IGNAV_API_KEY bulunamadi."
        )

        return None

    url = (
        "https://ignav.com/api/fare"
        "s/one-way"
    )

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": settings[
            "passengers"
        ][
            "adults"
        ],
        "children": settings[
            "passengers"
        ][
            "children"
        ],
        "infants_in_seat": 0,
        "infants_on_lap": settings[
            "passengers"
        ][
            "infants"
        ],
        "cabin_class": "economy",
        "max_stops": settings[
            "connections"
        ][
            "max_connections"
        ],
        "allow_self_transfer": settings[
            "connections"
        ][
            "self_transfer"
        ]
    }

    if settings[
        "cabin"
    ][
        "business"
    ]:

        payload[
            "cabin_class"
        ] = "business"

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
            f"Ignav: "
            f"{origin}->{destination} "
            f"{departure_date} "
            f"HTTP "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Ignav API cevabi:"
            )

            print(
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

            numeric_price = float(
                price
            )

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

        if len(segments) == 0:
            continue

        first_segment = segments[0]
        last_segment = segments[-1]

        origin = first_segment.get(
            "departure_airport",
            ""
        )

        destination = last_segment.get(
            "arrival_airport",
            ""
        )

        airline = (
            outbound.get(
                "carrier"
            )
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

        baggage_info = item.get(
            "bags",
            {}
        )

        baggage_text = str(
            baggage_info
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
    cur = conn.cursor()

    timestamp = utc_now_iso()

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

    exists = cur.fetchone()

    if exists:

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
                exists[0]
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

    conn.commit()
    conn.close()


# ============================================================
# FIYAT GECMISI
# ============================================================

def get_route_history(
    origin,
    destination,
    currency,
    exclude_price=None
):

    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT price
        FROM prices
        WHERE origin = ?
          AND destination = ?
          AND currency = ?
    """

    params = [
        origin,
        destination,
        currency
    ]

    if exclude_price is not None:

        query += (
            " AND price != ?"
        )

        params.append(
            exclude_price
        )

    query += """
        ORDER BY
            COALESCE(
                seen_at,
                recorded_at
            ) DESC
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
        exclude_price=current_price
    )

    if len(history) < 3:

        return {
            "normal_price": None,
            "sample_count": len(history),
            "confidence": "yetersiz_veri"
        }

    median_price = statistics.median(
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
            median_price,
            2
        ),
        "sample_count": len(history),
        "confidence": confidence
    }


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

    if drop <= 0:
        points = 0

    elif drop >= 50:
        points = 10

    elif drop >= 30:
        points = 8

    elif drop >= 20:
        points = 6

    elif drop >= 10:
        points = 3

    else:
        points = 1

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

    difference = (
        normal_price -
        current_price
    )

    drop_percent = (
        difference /
        normal_price
    ) * 100

    if drop_percent <= 0:
        points = 0

    elif drop_percent >= 70:
        points = 25

    elif drop_percent >= 60:
        points = 22

    elif drop_percent >= 50:
        points = 19

    elif drop_percent >= 40:
        points = 16

    elif drop_percent >= 30:
        points = 13

    elif drop_percent >= 20:
        points = 9

    elif drop_percent >= 10:
        points = 5

    else:
        points = 2

    return {
        "drop_percent": round(
            drop_percent,
            2
        ),
        "market_points": points
    }


# ============================================================
# KAYNAK UYUSMAZLIGI
# ============================================================

def calculate_source_disagreement(
    flight,
    verification
):

    # Su anda kullandigimiz ana kaynak Ignav.
    # Ikinci arama ayni sonucu veriyorsa
    # kaynaklar arasinda uyusmazlik yoktur.
    #
    # Ileride ikinci bagimsiz fiyat kaynagi
    # eklendiginde bu fonksiyon genisletilecektir.

    if not verification.get(
        "verified",
        False
    ):

        return 0

    return 0


# ============================================================
# VERGI / UCRET ANOMALISI
# ============================================================

def calculate_tax_anomaly(
    flight
):

    # Mevcut Ignav cevabinda fiyat,
    # zorunlu vergi ve ucretlerin ayri kalemleri
    # her zaman gelmedigi icin burada
    # uydurma vergi hesaplamasi yapilmiyor.
    #
    # Fiyat yapisinda ayri bir tax/fee bilgisi
    # varsa ileride gercek veriyle hesaplanabilir.

    return 0


# ============================================================
# KUR ANOMALISI
# ============================================================

def calculate_currency_anomaly(
    currency
):

    # TRY disindaki fiyatlari cezalandirmiyoruz.
    # Kur bilgisi olmadan sahte puan uretilmemesi icin
    # bu asamada 0 puan kullaniliyor.

    return 0


# ============================================================
# FARE ANOMALISI
# ============================================================

def calculate_fare_anomaly(
    flight
):

    points = 0

    baggage_text = (
        flight.get(
            "baggage",
            ""
        )
        or ""
    ).lower()

    if (
        "0" in baggage_text
        and (
            "checked" in baggage_text
            or "bag" in baggage_text
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


# ============================================================
# KISA SURELI FIYAT DAVRANISI
# ============================================================

def calculate_short_lived_persistence(
    flight,
    departure_date
):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM prices
        WHERE flight_key = ?
          AND departure_date = ?
        """,
        (
            flight["flight_key"],
            departure_date
        )
    )

    row = cur.fetchone()

    conn.close()

    count = int(
        row[0]
        if row and row[0] is not None
        else 0
    )

    # Fiyat ilk kez goruluyorsa
    # kisa sureli davranis icin puan vermiyoruz.
    #
    # Ayni ucus sonraki taramalarda gorulmeye devam ederse
    # bunun hata fiyatindan ziyade kalici kampanya
    # olma ihtimali artar.

    if count <= 1:
        return 5

    if count == 2:
        return 3

    return 0


# ============================================================
# KAYNAK GUVENILIRLIGI
# ============================================================

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

    normal_price = normal_data[
        "normal_price"
    ]

    # --------------------------------------------------------
    # 1. Tarihsel anomali - 20
    # --------------------------------------------------------

    historical_points = 0

    if normal_price is not None:

        if normal_price > 0:

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

    # --------------------------------------------------------
    # 2. Piyasa sapmasi - 25
    # --------------------------------------------------------

    market = calculate_market_divergence(
        flight["price"],
        normal_price
    )

    market_points = market[
        "market_points"
    ]

    # --------------------------------------------------------
    # 3. Ani dusus - 10
    # --------------------------------------------------------

    sudden = calculate_sudden_drop(
        flight["price"],
        previous_price
    )

    sudden_drop_points = sudden[
        "points"
    ]

    # --------------------------------------------------------
    # 4. Kaynaklar arasi uyusmazlik - 10
    # --------------------------------------------------------

    source_disagreement_points = (
        calculate_source_disagreement(
            flight,
            verification
        )
    )

    # --------------------------------------------------------
    # 5. Vergi / ucret anomalisi - 10
    # --------------------------------------------------------

    tax_points = calculate_tax_anomaly(
        flight
    )

    # --------------------------------------------------------
    # 6. Kur anomalisi - 5
    # --------------------------------------------------------

    currency_points = calculate_currency_anomaly(
        flight["currency"]
    )

    # --------------------------------------------------------
    # 7. Fare anomalisi - 5
    # --------------------------------------------------------

    fare_points = calculate_fare_anomaly(
        flight
    )

    # --------------------------------------------------------
    # 8. Kisa sureli fiyat davranisi - 5
    # --------------------------------------------------------

    persistence_points = 0

    if departure_date is not None:

        persistence_points = (
            calculate_short_lived_persistence(
                flight,
                departure_date
            )
        )

    # --------------------------------------------------------
    # 9. Kaynak guvenilirligi - 5
    # --------------------------------------------------------

    reliability_points = (
        calculate_source_reliability(
            verification
        )
    )

    # --------------------------------------------------------
    # TOPLAM
    # --------------------------------------------------------

    score = (
        historical_points
        + market_points
        + sudden_drop_points
        + source_disagreement_points
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
        "market_points": market_points,
        "sudden_drop_points": sudden_drop_points,
        "source_disagreement_points": source_disagreement_points,
        "tax_points": tax_points,
        "currency_points": currency_points,
        "fare_points": fare_points,
        "persistence_points": persistence_points,
        "reliability_points": reliability_points,
        "normal_price": normal_price,
        "drop_percent": market[
            "drop_percent"
        ],
        "previous_drop_percent": sudden[
            "drop_percent"
        ],
        "sample_count": normal_data[
            "sample_count"
        ],
        "confidence": normal_data[
            "confidence"
        ]
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

    normal_price = normal_data[
        "normal_price"
    ]

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

    if previous_price is not None:

        if previous_price > 0:

            previous_drop = (
                (
                    previous_price -
                    flight["price"]
                )
                /
                previous_price
            ) * 100

            if previous_drop >= 30:
                score += 20

            elif previous_drop >= 20:
                score += 15

            elif previous_drop >= 10:
                score += 10

    if not flight.get(
        "self_transfer",
        False
    ):

        score += 10

    baggage_text = str(
        flight.get(
            "baggage",
            ""
        )
    ).lower()

    if (
        "checked" in baggage_text
        or "bag" in baggage_text
    ):

        score += 10

    if flight.get(
        "duration_minutes",
        0
    ):

        if flight[
            "duration_minutes"
        ] <= 180:

            score += 10

    return round(
        min(
            score,
            100
        ),
        2
    )


# ============================================================
# ONCEKI FIYAT
# ============================================================

def get_previous_route_price(
    origin,
    destination,
    currency,
    current_price
):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT price
        FROM prices
        WHERE origin = ?
          AND destination = ?
          AND currency = ?
          AND price != ?
        ORDER BY
            COALESCE(
                seen_at,
                recorded_at
            ) DESC
        LIMIT 1
        """,
        (
            origin,
            destination,
            currency,
            current_price
        )
    )

    row = cur.fetchone()

    conn.close()

    if row and row[0] is not None:

        return float(
            row[0]
        )

    return None


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
            "reason": "Ikinci arama basarisiz."
        }

    flights = extract_flights(
        data
    )

    for item in flights:

        if (
            item["flight_key"]
            != flight["flight_key"]
        ):
            continue

        price_match = (
            abs(
                item["price"] -
                flight["price"]
            ) < 0.01
        )

        currency_match = (
            item["currency"]
            == flight["currency"]
        )

        self_transfer_match = (
            item["self_transfer"]
            == flight["self_transfer"]
        )

        if (
            price_match
            and currency_match
            and self_transfer_match
        ):

            return {
                "verified": True,
                "price": item["price"],
                "currency": item["currency"],
                "reason": (
                    "Ayni ucus ve ayni fiyat "
                    "ikinci aramada goruldu."
                )
            }

    return {
        "verified": False,
        "reason": (
            "Ayni ucus/fiyat ikinci aramada "
            "bulunamadi."
        )
    }


# ============================================================
# VERIFICATION KAYDI
# ============================================================

def save_verification(
    flight,
    verification,
    departure_date
):

    conn = get_connection()
    cur = conn.cursor()

    timestamp = utc_now_iso()

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
        "duration_minutes": flight[
            "duration_minutes"
        ],
        "baggage": flight[
            "baggage"
        ],
        "self_transfer": int(
            flight[
                "self_transfer"
            ]
        ),
        "verified": int(
            verification[
                "verified"
            ]
        ),
        "verification_reason": verification.get(
            "reason",
            ""
        ),
        "checked_at": timestamp,
        "recorded_at": timestamp,
        "seen_at": timestamp
    }

    table_info = get_table_info(
        cur,
        "verifications"
    )

    insert_columns = []
    insert_values = []

    for row in table_info:

        column_name = row[1]
        not_null = row[3]
        default_value = row[4]
        primary_key = row[5]

        if primary_key:
            continue

        if column_name in values:

            insert_columns.append(
                column_name
            )

            insert_values.append(
                values[column_name]
            )

        elif (
            not_null
            and default_value is None
        ):

            insert_columns.append(
                column_name
            )

            insert_values.append("")

    placeholders = ", ".join(
        "?"
        for _ in insert_columns
    )

    columns_sql = ", ".join(
        insert_columns
    )

    sql = f"""
        INSERT INTO verifications (
            {columns_sql}
        )
        VALUES (
            {placeholders}
        )
    """

    cur.execute(
        sql,
        insert_values
    )

    conn.commit()
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
            False
        )
    )


def send_telegram_message(
    message
):

    if not telegram_enabled():

        print(
            "Telegram ayari kapali."
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
            "Telegram bilgileri bulunamadi."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        if response.status_code == 200:

            print(
                "Telegram bildirimi gonderildi."
            )

            return True

        print(
            "Telegram hatasi:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:

        print(
            "Telegram baglanti hatasi:",
            repr(e)
        )

    return False


def get_alert_level(
    score
):

    if score >= 90:
        return "KRITIK HATA FIYATI"

    if score >= 85:
        return "HATA FIYATI ADAYI"

    if score >= 70:
        return "SUPHELI FIYAT"

    if score >= 50:
        return "IYI FIRSAT"

    return "NORMAL"


# ============================================================
# ALERT
# ============================================================

def alert_already_sent(
    flight_key,
    price,
    departure_date
):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM alerts
        WHERE flight_key = ?
          AND price = ?
          AND departure_date = ?
        LIMIT 1
        """,
        (
            flight_key,
            price,
            departure_date
        )
    )

    row = cur.fetchone()

    conn.close()

    return row is not None


def create_and_send_alert(
    flight,
    departure_date,
    score_data,
    opportunity_score,
    verification
):

    score = score_data[
        "score"
    ]

    if not verification[
        "verified"
    ]:

        return False

    if score < 50:

        return False

    if alert_already_sent(
        flight["flight_key"],
        flight["price"],
        departure_date
    ):

        print(
            "Bu fiyat icin daha once "
            "uyari gonderilmis."
        )

        return False

    level = get_alert_level(
        score
    )

    normal_price = score_data[
        "normal_price"
    ]

    drop_percent = score_data[
        "drop_percent"
    ]

    sample_count = score_data[
        "sample_count"
    ]

    message = (
        f"{level}\n\n"
        f"Ucus: "
        f"{flight['origin']} -> "
        f"{flight['destination']}\n"
        f"Tarih: "
        f"{departure_date}\n\n"
        f"Fiyat: "
        f"{flight['price']:.0f} "
        f"{flight['currency']}\n"
    )

    if normal_price is not None:

        message += (
            f"Normal fiyat: "
            f"{normal_price:.0f} "
            f"{flight['currency']}\n"
            f"Normalden sapma: "
            f"%{drop_percent:.1f}\n"
            f"Gecmis veri: "
            f"{sample_count} kayit\n"
        )

    message += (
        f"\nHata Skoru: "
        f"{score:.1f}/100\n"
        f"Firsat Skoru: "
        f"{opportunity_score:.1f}/100\n"
        f"\n"
        f"Skor detaylari:\n"
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
        f"{flight['baggage']}\n"
        f"Dogrulama: BASARILI"
    )

    sent = send_telegram_message(
        message
    )

    if not sent:

        return False

    conn = get_connection()
    cur = conn.cursor()

    timestamp = utc_now_iso()

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
        "sent_at": timestamp,
        "recorded_at": timestamp,
        "seen_at": timestamp
    }

    table_info = get_table_info(
        cur,
        "alerts"
    )

    insert_columns = []
    insert_values = []

    for row in table_info:

        column_name = row[1]
        not_null = row[3]
        default_value = row[4]
        primary_key = row[5]

        if primary_key:
            continue

        if column_name in values:

            insert_columns.append(
                column_name
            )

            insert_values.append(
                values[column_name]
            )

        elif (
            not_null
            and default_value is None
        ):

            insert_columns.append(
                column_name
            )

            insert_values.append("")

    columns_sql = ", ".join(
        insert_columns
    )

    placeholders = ", ".join(
        "?"
        for _ in insert_columns
    )

    sql = f"""
        INSERT INTO alerts (
            {columns_sql}
        )
        VALUES (
            {placeholders}
        )
    """

    cur.execute(
        sql,
        insert_values
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# ROTALAR
# ============================================================

def build_routes():

    routes = []

    airports = settings[
        "airports"
    ]

    origins = airports.get(
        "priority_origins",
        []
    )

    domestic_destinations = airports.get(
        "domestic_destinations",
        []
    )

    europe_destinations = airports.get(
        "europe_destinations",
        []
    )

    # --------------------------------------------------------
    # SZF -> TURKIYE
    # --------------------------------------------------------

    if airports.get(
        "domestic_enabled",
        True
    ):

        for origin in origins:

            for destination in domestic_destinations:

                if origin != destination:

                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

    # --------------------------------------------------------
    # SZF -> AVRUPA
    # --------------------------------------------------------

    if airports.get(
        "europe_enabled",
        True
    ):

        for origin in origins:

            for destination in europe_destinations:

                if origin != destination:

                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

    # --------------------------------------------------------
    # AVRUPA -> SZF
    # --------------------------------------------------------

    if airports.get(
        "europe_enabled",
        True
    ):

        for destination in origins:

            for origin in europe_destinations:

                if origin != destination:

                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

    unique_routes = []
    seen_routes = set()

    for route in routes:

        if route in seen_routes:
            continue

        seen_routes.add(
            route
        )

        unique_routes.append(
            route
        )

    return unique_routes


# ============================================================
# DONEN ROTA GRUBU
# ============================================================

def get_next_routes(
    all_routes,
    route_count=10
):

    if not all_routes:

        return [], 0

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

    if row is None:

        current_index = 0

        cur.execute(
            """
            INSERT OR REPLACE INTO radar_state (
                id,
                route_index,
                updated_at
            )
            VALUES (1, 0, ?)
            """,
            (utc_now_iso(),)
        )

    else:

        try:

            current_index = int(
                row[0] or 0
            )

        except Exception:

            current_index = 0

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
            utc_now_iso()
        )
    )

    conn.commit()
    conn.close()

    return selected, current_index


# ============================================================
# TARAMA TARIHLERI
# ============================================================

def build_departure_dates():

    base = utc_now()

    days = [
        30,
        60,
        90
    ]

    dates = []

    for day_count in days:

        departure_date = (
            base +
            timedelta(
                days=day_count
            )
        ).strftime(
            "%Y-%m-%d"
        )

        dates.append(
            departure_date
        )

    return dates


# ============================================================
# ANA PROGRAM
# ============================================================

def main():

    print("")
    print("=" * 60)
    print(
        "UCUS HATA FIYATI RADARI"
    )
    print(
        "100 PUANLIK HATA SKORU MOTORU"
    )
    print("=" * 60)

    init_db()

    all_routes = build_routes()

    routes, route_start = (
        get_next_routes(
            all_routes,
            route_count=10
        )
    )

    departure_dates = (
        build_departure_dates()
    )

    total_saved = 0
    total_verified = 0
    total_alerts = 0
    total_api_searches = 0

    print(
        f"Toplam rota havuzu: "
        f"{len(all_routes)}"
    )

    print(
        f"Bu calismada taranacak rota: "
        f"{len(routes)}"
    )

    print(
        f"Baslangic rota sirasi: "
        f"{route_start}"
    )

    print(
        "Taranan tarihler: "
        + ", ".join(
            departure_dates
        )
    )

    print(
        "Tahmini API aramasi: "
        f"{len(routes) * len(departure_dates)}"
    )

    print(
        "Telegram: "
        + (
            "AKTIF"
            if telegram_enabled()
            else "KAPALI"
        )
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

            total_api_searches += 1

            flights = extract_flights(
                data
            )

            print(
                f"Bulunan ucus: "
                f"{len(flights)}"
            )

            for flight in flights:

                normal_data = (
                    calculate_normal_price(
                        flight["origin"],
                        flight["destination"],
                        flight["currency"],
                        current_price=flight["price"]
                    )
                )

                previous_price = (
                    get_previous_route_price(
                        flight["origin"],
                        flight["destination"],
                        flight["currency"],
                        flight["price"]
                    )
                )

                # Ilk skor.
                #
                # 30+ oldugunda ikinci arama
                # ile dogrulama yapilir.
                preliminary_score = (
                    calculate_error_score(
                        flight,
                        normal_data,
                        previous_price,
                        verification={
                            "verified": False
                        },
                        departure_date=departure_date
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
                    f"  "
                    f"{flight['origin']} -> "
                    f"{flight['destination']} | "
                    f"{flight['price']:.0f} "
                    f"{flight['currency']} | "
                    f"Normal: "
                    f"{normal_data['normal_price']} | "
                    f"Hata Skoru: "
                    f"{preliminary_score['score']} | "
                    f"Firsat: "
                    f"{opportunity_score}"
                )

                save_flight(
                    flight,
                    departure_date
                )

                total_saved += 1

                if preliminary_score[
                    "score"
                ] >= 30:

                    print(
                        "    "
                        "Dogrulama yapiliyor..."
                    )

                    verification = (
                        verify_flight(
                            flight,
                            departure_date
                        )
                    )

                    save_verification(
                        flight,
                        verification,
                        departure_date
                    )

                    # Dogrulama sonucu ile
                    # kaynak guvenilirligi ve
                    # diger dogrulama bagimli
                    # puanlar yeniden hesaplanir.
                    final_score = (
                        calculate_error_score(
                            flight,
                            normal_data,
                            previous_price,
                            verification,
                            departure_date
                        )
                    )

                    if verification[
                        "verified"
                    ]:

                        total_verified += 1

                        print(
                            "    "
                            "DOGRULAMA BASARILI"
                        )

                        print(
                            "    "
                            f"Final Hata Skoru: "
                            f"{final_score['score']}"
                        )

                        print(
                            "    "
                            f"Firsat Skoru: "
                            f"{opportunity_score}"
                        )

                        if create_and_send_alert(
                            flight,
                            departure_date,
                            final_score,
                            opportunity_score,
                            verification
                        ):

                            total_alerts += 1

                    else:

                        print(
                            "    "
                            "DOGRULAMA BASARISIZ"
                        )

            print(
                f"  Toplam API aramasi: "
                f"{total_api_searches}"
            )

    print("")
    print("=" * 60)
    print(
        "TARAMA TAMAMLANDI"
    )
    print("=" * 60)

    print(
        f"Toplam rota havuzu: "
        f"{len(all_routes)}"
    )

    print(
        f"Bu calismada taranan rota: "
        f"{len(routes)}"
    )

    print(
        f"API aramasi: "
        f"{total_api_searches}"
    )

    print(
        f"Kaydedilen fiyat: "
        f"{total_saved}"
    )

    print(
        f"Dogrulanan ucus: "
        f"{total_verified}"
    )

    print(
        f"Gonderilen Telegram uyarisi: "
        f"{total_alerts}"
    )

    print("=" * 60)


# ============================================================
# HATA YAKALAMA
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        import traceback

        print("")
        print("=" * 60)
        print(
            "RADAR KRITIK HATA"
        )
        print("=" * 60)

        print(
            f"Hata tipi: "
            f"{type(e).__name__}"
        )

        print(
            f"Hata: "
            f"{e}"
        )

        print("=" * 60)

        traceback.print_exc()

        raise
