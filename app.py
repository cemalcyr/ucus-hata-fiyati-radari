import os
import json
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests


DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"

IGNAV_BASE = "https://ignav.com/api"


# ============================================================
# ZAMAN
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


# ============================================================
# AYARLAR
# ============================================================

def load_settings():
    with open(
        SETTINGS_FILE,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


settings = load_settings()


# ============================================================
# VERITABANI YARDIMCILARI
# ============================================================

def get_connection():
    return sqlite3.connect(DB_FILE)


def table_info(
    conn,
    table_name
):
    cur = conn.cursor()

    cur.execute(
        f"PRAGMA table_info({table_name})"
    )

    return cur.fetchall()


def table_columns(
    conn,
    table_name
):
    return {
        row[1]
        for row in table_info(
            conn,
            table_name
        )
    }


def add_column_if_missing(
    conn,
    table_name,
    column_name,
    definition
):
    columns = table_columns(
        conn,
        table_name
    )

    if column_name not in columns:
        conn.execute(
            f"""
            ALTER TABLE {table_name}
            ADD COLUMN {column_name} {definition}
            """
        )


# ============================================================
# VERITABANI KURULUM / MIGRATION
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
            price_status TEXT,
            ignav_id TEXT,
            stops INTEGER DEFAULT 0,
            seen_at TEXT,
            recorded_at TEXT,
            base_fare REAL,
            taxes REAL,
            fees REAL,
            tax_ratio REAL
        )
        """
    )

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
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "seen_at": "TEXT",
        "recorded_at": "TEXT",

        # V3
        "base_fare": "REAL",
        "taxes": "REAL",
        "fees": "REAL",
        "tax_ratio": "REAL"
    }

    for name, definition in price_fields.items():
        add_column_if_missing(
            conn,
            "prices",
            name,
            definition
        )

    # --------------------------------------------------------
    # OBSERVATIONS
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
            price_status TEXT,
            ignav_id TEXT,
            stops INTEGER DEFAULT 0,
            observed_at TEXT,
            base_fare REAL,
            taxes REAL,
            fees REAL,
            tax_ratio REAL
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
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "observed_at": "TEXT",

        # V3
        "base_fare": "REAL",
        "taxes": "REAL",
        "fees": "REAL",
        "tax_ratio": "REAL"
    }

    for name, definition in observation_fields.items():
        add_column_if_missing(
            conn,
            "price_observations",
            name,
            definition
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
        "price_status": "TEXT",
        "ignav_id": "TEXT",
        "stops": "INTEGER DEFAULT 0",
        "verified": "INTEGER DEFAULT 0",
        "booking_verified": "INTEGER DEFAULT 0",
        "source_disagreement_points": "INTEGER DEFAULT 0",
        "verification_reason": "TEXT",
        "checked_at": "TEXT",
        "verification_time": "TEXT"
    }

    for name, definition in verification_fields.items():
        add_column_if_missing(
            conn,
            "verifications",
            name,
            definition
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
        add_column_if_missing(
            conn,
            "alerts",
            name,
            definition
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
            VALUES (
                1,
                0,
                ?
            )
            """,
            (utc_iso(),)
        )

    # --------------------------------------------------------
    # ESKI BOS ALANLARI DOLDUR
    # --------------------------------------------------------

    now = utc_iso()

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
        SET recorded_at = seen_at
        WHERE recorded_at IS NULL
          AND seen_at IS NOT NULL
        """
    )

    cur.execute(
        """
        UPDATE price_observations
        SET observed_at = ?
        WHERE observed_at IS NULL
        """,
        (now,)
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
        SET verification_time = checked_at
        WHERE verification_time IS NULL
          AND checked_at IS NOT NULL
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

    conn.commit()
    conn.close()

    print(
        "Veritabani kontrolu tamamlandi."
    )


# ============================================================
# IGNAV API
# ============================================================

def ignav_headers():

    api_key = os.getenv(
        "IGNAV_API_KEY"
    )

    if not api_key:
        return None

    return {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }


def get_market():

    return (
        settings.get(
            "system",
            {}
        ).get(
            "market",
            "TR"
        )
        or "TR"
    )


def ignav_search(
    origin,
    destination,
    departure_date
):

    headers = ignav_headers()

    if not headers:

        print(
            "IGNAV_API_KEY bulunamadi."
        )

        return None

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
        ),
        "market": get_market()
    }

    price_settings = settings.get(
        "price",
        {}
    )

    max_price = price_settings.get(
        "maximum_try",
        0
    )

    if max_price and max_price > 0:
        payload["max_price"] = max_price

    airlines = settings.get(
        "airlines",
        {}
    )

    disabled = airlines.get(
        "disabled",
        []
    )

    if disabled:
        payload[
            "airlines_exclude"
        ] = disabled

    try:

        response = requests.post(
            f"{IGNAV_BASE}/fares/one-way",
            headers=headers,
            json=payload,
            timeout=60
        )

        print(
            f"Ignav: "
            f"{origin}->{destination} "
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
            "Ignav arama hatasi:",
            repr(e)
        )

        return None


# ============================================================
# BOOKING LINKS
# ============================================================

def get_booking_links(
    ignav_id
):

    if not ignav_id:
        return None

    headers = ignav_headers()

    if not headers:
        return None

    try:

        response = requests.post(
            f"{IGNAV_BASE}/fares/booking-links",
            headers=headers,
            json={
                "ignav_id": ignav_id
            },
            timeout=60
        )

        print(
            f"Booking links: "
            f"HTTP {response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Booking links cevabi:",
                response.text[:800]
            )

            return None

        return response.json()

    except Exception as e:

        print(
            "Booking links hatasi:",
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
    duration,
    stops
):

    return (
        f"{origin}-"
        f"{destination}-"
        f"{airline}-"
        f"{flight_number}-"
        f"{duration}-"
        f"{stops}"
    )


# ============================================================
# BAGAJ
# ============================================================

def baggage_to_text(
    bags
):

    if bags is None:
        return ""

    if isinstance(
        bags,
        str
    ):
        return bags

    try:

        return json.dumps(
            bags,
            ensure_ascii=False
        )

    except Exception:

        return str(
            bags
        )


def get_checked_bags(
    bags
):

    if not isinstance(
        bags,
        dict
    ):
        return None

    checked = bags.get(
        "checked"
    )

    try:

        if checked is None:
            return None

        return int(
            checked
        )

    except Exception:

        return None


def get_carry_on_bags(
    bags
):

    if not isinstance(
        bags,
        dict
    ):
        return None

    carry = bags.get(
        "carry_on"
    )

    try:

        if carry is None:
            return None

        return int(
            carry
        )

    except Exception:

        return None


def has_checked_baggage(
    flight
):

    checked = flight.get(
        "checked_bags"
    )

    if checked is not None:
        return checked > 0

    text = str(
        flight.get(
            "baggage",
            ""
        )
    ).lower()

    if (
        "checked" in text
        and "0" not in text
    ):
        return True

    if (
        "checked" in text
        and "included" in text
    ):
        return True

    return False


# ============================================================
# V3 - SAYISAL YARDIMCILAR
# ============================================================

def safe_float(
    value
):

    if value is None:
        return None

    if isinstance(
        value,
        bool
    ):
        return None

    if isinstance(
        value,
        (int, float)
    ):
        return float(
            value
        )

    if isinstance(
        value,
        str
    ):

        value = value.strip()

        if not value:
            return None

        try:

            return float(
                value.replace(
                    ",",
                    "."
                )
            )

        except Exception:

            return None

    return None


# ============================================================
# V3 - PARA ALANI OKUMA
# ============================================================

def extract_money_value(
    value
):

    direct = safe_float(
        value
    )

    if direct is not None:
        return direct

    if isinstance(
        value,
        dict
    ):

        # Bir nesnenin toplam amount'u varsa
        # alt elemanlari tekrar toplamiyoruz.
        for key in (
            "amount",
            "value",
            "total",
            "price"
        ):

            if key in value:

                number = safe_float(
                    value.get(
                        key
                    )
                )

                if number is not None:
                    return number

        for key in (
            "items",
            "components",
            "details",
            "breakdown"
        ):

            if key in value:

                number = extract_money_value(
                    value.get(
                        key
                    )
                )

                if number is not None:
                    return number

    if isinstance(
        value,
        list
    ):

        numbers = []

        for item in value:

            number = extract_money_value(
                item
            )

            if number is not None:
                numbers.append(
                    number
                )

        if numbers:
            return sum(
                numbers
            )

    return None


# ============================================================
# V3 - FIYAT BILESENI ARAMA
# ============================================================

def search_component_recursive(
    value,
    names
):

    if isinstance(
        value,
        dict
    ):

        label = str(
            value.get("type")
            or value.get("category")
            or value.get("name")
            or value.get("label")
            or value.get("description")
            or ""
        ).lower()

        for name in names:

            if (
                name in label
                or label in name
            ):

                number = extract_money_value(
                    value
                )

                if number is not None:
                    return number

        for key, item in value.items():

            key_lower = str(
                key
            ).lower()

            if key_lower in names:

                number = extract_money_value(
                    item
                )

                if number is not None:
                    return number

        for item in value.values():

            found = search_component_recursive(
                item,
                names
            )

            if found is not None:
                return found

    elif isinstance(
        value,
        list
    ):

        for item in value:

            found = search_component_recursive(
                item,
                names
            )

            if found is not None:
                return found

    return None


def find_price_component(
    price_info,
    component_names
):

    if not isinstance(
        price_info,
        dict
    ):
        return None

    names = {
        str(name).lower()
        for name in component_names
    }

    # --------------------------------------------------------
    # Dogrudan alanlar
    # --------------------------------------------------------

    for key, value in price_info.items():

        key_lower = str(
            key
        ).lower()

        if key_lower in names:

            number = extract_money_value(
                value
            )

            if number is not None:
                return number

    # --------------------------------------------------------
    # Nested alanlar
    # --------------------------------------------------------

    nested_keys = (
        "breakdown",
        "fare_breakdown",
        "price_breakdown",
        "components",
        "details",
        "fare_details"
    )

    for container_key in nested_keys:

        container = price_info.get(
            container_key
        )

        if container is None:
            continue

        found = search_component_recursive(
            container,
            names
        )

        if found is not None:
            return found

    return None


# ============================================================
# V3 - FIYAT AYRISTIRMA
# ============================================================

def extract_price_breakdown(
    price_info,
    total_price
):

    if not isinstance(
        price_info,
        dict
    ):
        return {
            "base_fare": None,
            "taxes": None,
            "fees": None,
            "tax_ratio": None
        }

    base_fare = find_price_component(
        price_info,
        (
            "base_fare",
            "basefare",
            "base",
            "fare",
            "fare_amount",
            "net",
            "net_amount",
            "subtotal"
        )
    )

    taxes = find_price_component(
        price_info,
        (
            "taxes",
            "tax",
            "tax_amount",
            "taxes_amount"
        )
    )

    fees = find_price_component(
        price_info,
        (
            "fees",
            "fee",
            "fee_amount",
            "surcharges",
            "surcharge",
            "service_fee",
            "booking_fee"
        )
    )

    total = safe_float(
        total_price
    )

    # --------------------------------------------------------
    # Tutarlilik kontrolu
    # --------------------------------------------------------

    if total is not None:

        if (
            base_fare is not None
            and taxes is not None
            and fees is not None
        ):

            known_sum = (
                base_fare
                + taxes
                + fees
            )

            difference = (
                total -
                known_sum
            )

            # Buyuk fark varsa parserin
            # yanlis alani yakalamis olabilecegini
            # varsayiyoruz.
            if abs(
                difference
            ) > (
                total * 0.10
            ):

                base_fare = None
                taxes = None
                fees = None

        elif (
            base_fare is None
            and taxes is not None
            and fees is not None
        ):

            remaining = (
                total -
                taxes -
                fees
            )

            if remaining >= 0:

                base_fare = round(
                    remaining,
                    2
                )

    tax_ratio = None

    if (
        taxes is not None
        and total is not None
        and total > 0
        and taxes >= 0
    ):

        tax_ratio = (
            taxes /
            total
        )

    return {
        "base_fare": (
            round(
                base_fare,
                2
            )
            if base_fare is not None
            else None
        ),
        "taxes": (
            round(
                taxes,
                2
            )
            if taxes is not None
            else None
        ),
        "fees": (
            round(
                fees,
                2
            )
            if fees is not None
            else None
        ),
        "tax_ratio": (
            round(
                tax_ratio,
                6
            )
            if tax_ratio is not None
            else None
        )
    }


# ============================================================
# UCUSLARI AYIKLA
# ============================================================

def extract_flights(
    data
):

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

        bags = item.get(
            "bags",
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

        price_status = price_info.get(
            "status",
            "unknown"
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

        # ----------------------------------------------------
        # V3 FIYAT AYRISTIRMA
        # ----------------------------------------------------

        price_breakdown = (
            extract_price_breakdown(
                price_info,
                numeric_price
            )
        )

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

        first = segments[0]
        last = segments[-1]

        if not isinstance(
            first,
            dict
        ):
            continue

        if not isinstance(
            last,
            dict
        ):
            continue

        origin = first.get(
            "departure_airport",
            ""
        )

        destination = last.get(
            "arrival_airport",
            ""
        )

        airline = (
            first.get(
                "marketing_carrier_code"
            )
            or outbound.get(
                "carrier"
            )
            or ""
        )

        flight_number = first.get(
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

        stops = max(
            len(segments) - 1,
            0
        )

        self_transfer = bool(
            item.get(
                "requires_self_transfer",
                False
            )
        )

        baggage_text = baggage_to_text(
            bags
        )

        checked_bags = get_checked_bags(
            bags
        )

        carry_on_bags = get_carry_on_bags(
            bags
        )

        ignav_id = item.get(
            "ignav_id"
        )

        flight_key = create_flight_key(
            origin,
            destination,
            flight_number,
            airline,
            duration,
            stops
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
                "price_status": price_status,
                "airline": airline,
                "flight_number": flight_number,
                "duration_minutes": duration,
                "stops": stops,
                "baggage": baggage_text,
                "checked_bags": checked_bags,
                "carry_on_bags": carry_on_bags,
                "self_transfer": self_transfer,
                "ignav_id": ignav_id,

                # V3
                "base_fare": price_breakdown[
                    "base_fare"
                ],
                "taxes": price_breakdown[
                    "taxes"
                ],
                "fees": price_breakdown[
                    "fees"
                ],
                "tax_ratio": price_breakdown[
                    "tax_ratio"
                ]
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

    now = utc_iso()

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
                recorded_at = ?,
                ignav_id = ?,
                price_status = ?,
                base_fare = ?,
                taxes = ?,
                fees = ?,
                tax_ratio = ?
            WHERE id = ?
            """,
            (
                now,
                now,
                flight.get(
                    "ignav_id"
                ),
                flight.get(
                    "price_status"
                ),
                flight.get(
                    "base_fare"
                ),
                flight.get(
                    "taxes"
                ),
                flight.get(
                    "fees"
                ),
                flight.get(
                    "tax_ratio"
                ),
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
                price_status,
                ignav_id,
                stops,
                base_fare,
                taxes,
                fees,
                tax_ratio,
                seen_at,
                recorded_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
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
                flight.get(
                    "price_status"
                ),
                flight.get(
                    "ignav_id"
                ),
                flight.get(
                    "stops",
                    0
                ),
                flight.get(
                    "base_fare"
                ),
                flight.get(
                    "taxes"
                ),
                flight.get(
                    "fees"
                ),
                flight.get(
                    "tax_ratio"
                ),
                now,
                now
            )
        )

    # --------------------------------------------------------
    # HER GOZLEMI AYRI KAYDET
    # --------------------------------------------------------

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
            price_status,
            ignav_id,
            stops,
            base_fare,
            taxes,
            fees,
            tax_ratio,
            observed_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?
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
            flight.get(
                "price_status"
            ),
            flight.get(
                "ignav_id"
            ),
            flight.get(
                "stops",
                0
            ),
            flight.get(
                "base_fare"
            ),
            flight.get(
                "taxes"
            ),
            flight.get(
                "fees"
            ),
            flight.get(
                "tax_ratio"
            ),
            now
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# GECMIS FIYAT
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

    if row:
        return float(
            row[0]
        )

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

    if row:
        return int(
            row[0]
        )

    return 0


# ============================================================
# ROTA NORMAL FIYATI
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
            "sample_count": len(
                history
            ),
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
        "sample_count": len(
            history
        ),
        "confidence": confidence
    }


# ============================================================
# V3 - VERGI GECMISI
# ============================================================

def get_tax_history(
    origin,
    destination,
    currency,
    current_tax_ratio=None
):

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT tax_ratio
        FROM price_observations
        WHERE origin = ?
          AND destination = ?
          AND currency = ?
          AND tax_ratio IS NOT NULL
        ORDER BY observed_at DESC
        LIMIT 200
        """,
        (
            origin,
            destination,
            currency
        )
    )

    rows = cur.fetchall()

    conn.close()

    values = []

    for row in rows:

        if row[0] is None:
            continue

        try:

            value = float(
                row[0]
            )

        except Exception:

            continue

        if (
            current_tax_ratio is not None
            and abs(
                value -
                current_tax_ratio
            ) < 0.000001
        ):
            continue

        values.append(
            value
        )

    return values


def calculate_tax_anomaly(
    flight
):

    current_ratio = flight.get(
        "tax_ratio"
    )

    taxes = flight.get(
        "taxes"
    )

    if (
        current_ratio is None
        or taxes is None
    ):

        return {
            "points": 0,
            "current_ratio": None,
            "normal_ratio": None,
            "deviation": 0,
            "sample_count": 0,
            "confidence": "veri_yok"
        }

    history = get_tax_history(
        flight["origin"],
        flight["destination"],
        flight["currency"],
        current_ratio
    )

    if len(history) < 3:

        return {
            "points": 0,
            "current_ratio": current_ratio,
            "normal_ratio": None,
            "deviation": 0,
            "sample_count": len(
                history
            ),
            "confidence": "yetersiz_veri"
        }

    normal_ratio = statistics.median(
        history
    )

    deviation = (
        normal_ratio -
        current_ratio
    )

    points = 0

    # --------------------------------------------------------
    # Beklenenden cok daha dusuk vergi
    # --------------------------------------------------------

    if deviation >= 0.20:
        points = 8

    elif deviation >= 0.12:
        points = 6

    elif deviation >= 0.07:
        points = 4

    elif deviation >= 0.04:
        points = 2

    # --------------------------------------------------------
    # Beklenenden yuksek vergi
    # --------------------------------------------------------

    elif deviation <= -0.25:
        points = 4

    elif deviation <= -0.15:
        points = 3

    elif deviation <= -0.08:
        points = 2

    if len(history) >= 20:
        confidence = "yuksek"

    elif len(history) >= 8:
        confidence = "orta"

    else:
        confidence = "dusuk"

    return {
        "points": min(
            points,
            10
        ),
        "current_ratio": round(
            current_ratio,
            4
        ),
        "normal_ratio": round(
            normal_ratio,
            4
        ),
        "deviation": round(
            deviation,
            4
        ),
        "sample_count": len(
            history
        ),
        "confidence": confidence
    }


# ============================================================
# ANI FIYAT DUSUSU
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

    if drop >= 60:
        points = 10

    elif drop >= 50:
        points = 9

    elif drop >= 40:
        points = 8

    elif drop >= 30:
        points = 7

    elif drop >= 20:
        points = 5

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
# SAGLAYICI / BOOKING FIYAT KARSILASTIRMASI
# ============================================================

def analyze_booking_sources(
    booking_data,
    search_price,
    currency
):

    if not booking_data:

        return {
            "points": 0,
            "provider_count": 0,
            "price_point_count": 0,
            "min_price": None,
            "max_price": None,
            "spread_percent": 0,
            "search_gap_percent": 0,
            "providers": []
        }

    options = booking_data.get(
        "booking_options",
        []
    )

    prices = []
    providers = []

    for option in options:

        if not isinstance(
            option,
            dict
        ):
            continue

        links = option.get(
            "links",
            []
        )

        if not isinstance(
            links,
            list
        ):
            continue

        for link in links:

            if not isinstance(
                link,
                dict
            ):
                continue

            price_info = link.get(
                "price",
                {}
            )

            if not isinstance(
                price_info,
                dict
            ):
                continue

            amount = price_info.get(
                "amount"
            )

            link_currency = price_info.get(
                "currency"
            )

            if amount is None:
                continue

            if link_currency != currency:
                continue

            try:

                amount = float(
                    amount
                )

            except Exception:

                continue

            # V3 provider tespiti
            provider = (
                link.get(
                    "provider_name"
                )
                or link.get(
                    "provider"
                )
                or link.get(
                    "supplier"
                )
                or link.get(
                    "agent"
                )
                or link.get(
                    "name"
                )
            )

            # URL varsa ve provider yoksa
            # domain adini kullan.
            if not provider:

                url = (
                    link.get("url")
                    or link.get("booking_url")
                    or link.get("href")
                    or ""
                )

                if url:

                    try:

                        hostname = urlparse(
                            url
                        ).hostname

                        if hostname:
                            provider = hostname

                    except Exception:
                        pass

            if not provider:
                provider = "Bilinmeyen"

            prices.append(
                amount
            )

            providers.append(
                str(
                    provider
                )
            )

    unique_prices = sorted(
        set(
            round(
                p,
                2
            )
            for p in prices
        )
    )

    unique_providers = list(
        dict.fromkeys(
            providers
        )
    )

    if not unique_prices:

        return {
            "points": 0,
            "provider_count": 0,
            "price_point_count": 0,
            "min_price": None,
            "max_price": None,
            "spread_percent": 0,
            "search_gap_percent": 0,
            "providers": []
        }

    minimum = min(
        unique_prices
    )

    maximum = max(
        unique_prices
    )

    if minimum <= 0:

        spread = 0

    else:

        spread = (
            (
                maximum -
                minimum
            )
            /
            minimum
        ) * 100

    if minimum > 0:

        search_gap = (
            (
                search_price -
                minimum
            )
            /
            minimum
        ) * 100

    else:

        search_gap = 0

    points = 0

    # --------------------------------------------------------
    # Fiyat kaynaklari arasindaki fark
    # --------------------------------------------------------

    if len(unique_prices) >= 3:

        if spread >= 30:
            points = 10

        elif spread >= 20:
            points = 8

        elif spread >= 10:
            points = 5

        elif spread >= 5:
            points = 2

    elif len(unique_prices) >= 2:

        if spread >= 30:
            points = 8

        elif spread >= 20:
            points = 6

        elif spread >= 10:
            points = 3

        elif spread >= 5:
            points = 1

    # --------------------------------------------------------
    # Arama sonucu ile booking farki
    # --------------------------------------------------------

    if abs(
        search_gap
    ) >= 30:

        points = min(
            10,
            points + 3
        )

    elif abs(
        search_gap
    ) >= 20:

        points = min(
            10,
            points + 2
        )

    return {
        "points": points,

        # Gercek provider sayisi
        "provider_count": len(
            unique_providers
        ),

        # Ayrica kac farkli fiyat gorduk
        "price_point_count": len(
            unique_prices
        ),

        "min_price": minimum,

        "max_price": maximum,

        "spread_percent": round(
            spread,
            2
        ),

        "search_gap_percent": round(
            search_gap,
            2
        ),

        "providers": unique_providers
    }


# ============================================================
# KUR ANOMALISI
# ============================================================

def calculate_currency_anomaly(
    flight
):

    expected_market = get_market()

    expected_currency = "TRY"

    if (
        expected_market == "TR"
        and flight.get(
            "currency"
        ) != expected_currency
    ):

        return 5

    return 0


# ============================================================
# FARE ANOMALISI
# ============================================================

def calculate_fare_anomaly(
    flight
):

    points = 0

    if flight.get(
        "self_transfer",
        False
    ):

        points += 3

    checked = flight.get(
        "checked_bags"
    )

    carry_on = flight.get(
        "carry_on_bags"
    )

    if (
        checked == 0
        and carry_on == 0
    ):

        points += 1

    return min(
        points,
        5
    )


# ============================================================
# KISA SURELI DAVRANIS
# ============================================================

def calculate_short_lived_persistence(
    flight,
    departure_date
):

    count = get_observation_count(
        flight,
        departure_date
    )

    if count <= 1:
        return 0

    if count == 2:
        return 2

    return 0


# ============================================================
# KAYNAK GUVENILIRLIGI
# ============================================================

def calculate_source_reliability(
    verification
):

    points = 0

    if verification.get(
        "verified",
        False
    ):

        points += 3

    if verification.get(
        "booking_verified",
        False
    ):

        points += 2

    return points


# ============================================================
# VERIFICATION
# ============================================================

def verify_flight(
    flight,
    departure_date,
    run_booking_check=False
):

    # --------------------------------------------------------
    # 1. Ayni Ignav aramasiyla fiyat teyidi
    # --------------------------------------------------------

    data = ignav_search(
        flight["origin"],
        flight["destination"],
        departure_date
    )

    if not data:

        return {
            "verified": False,
            "booking_verified": False,
            "source_disagreement_points": 0,
            "reason": (
                "Ikinci Ignav aramasi basarisiz."
            )
        }

    flights = extract_flights(
        data
    )

    matched = None

    for item in flights:

        if (
            item["flight_key"]
            !=
            flight["flight_key"]
        ):
            continue

        price_match = (
            abs(
                item["price"]
                -
                flight["price"]
            )
            < 0.01
        )

        currency_match = (
            item["currency"]
            ==
            flight["currency"]
        )

        self_transfer_match = (
            item["self_transfer"]
            ==
            flight["self_transfer"]
        )

        if (
            price_match
            and currency_match
            and self_transfer_match
        ):

            matched = item
            break

    if matched is None:

        return {
            "verified": False,
            "booking_verified": False,
            "source_disagreement_points": 0,
            "reason": (
                "Ikinci Ignav aramasinda "
                "ayni fiyatli ayni ucus bulunamadi."
            )
        }

    result = {
        "verified": True,
        "booking_verified": False,
        "source_disagreement_points": 0,
        "booking_data": None,
        "reason": (
            "Ayni ucus ve fiyat ikinci "
            "Ignav aramasinda tekrar bulundu."
        )
    }

    # --------------------------------------------------------
    # 2. Booking provider kontrolu
    # --------------------------------------------------------

    if run_booking_check:

        ignav_id = (
            flight.get(
                "ignav_id"
            )
            or
            matched.get(
                "ignav_id"
            )
        )

        booking_data = get_booking_links(
            ignav_id
        )

        result[
            "booking_data"
        ] = booking_data

        if booking_data:

            booking_analysis = (
                analyze_booking_sources(
                    booking_data,
                    flight["price"],
                    flight["currency"]
                )
            )

            result[
                "source_disagreement_points"
            ] = booking_analysis[
                "points"
            ]

            if booking_analysis[
                "provider_count"
            ] >= 1:

                result[
                    "booking_verified"
                ] = True

                result[
                    "booking_analysis"
                ] = booking_analysis

                result["reason"] += (
                    " Booking kaynaklari da "
                    "kontrol edildi."
                )

    return result


# ============================================================
# VERIFICATION KAYDI
# ============================================================

def save_verification(
    flight,
    departure_date,
    verification
):

    conn = get_connection()

    try:

        columns = table_columns(
            conn,
            "verifications"
        )

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
            "price_status": flight.get(
                "price_status"
            ),
            "ignav_id": flight.get(
                "ignav_id"
            ),
            "stops": flight.get(
                "stops",
                0
            ),
            "verified": int(
                bool(
                    verification.get(
                        "verified",
                        False
                    )
                )
            ),
            "booking_verified": int(
                bool(
                    verification.get(
                        "booking_verified",
                        False
                    )
                )
            ),
            "source_disagreement_points": int(
                verification.get(
                    "source_disagreement_points",
                    0
                )
            ),
            "verification_reason": verification.get(
                "reason",
                ""
            ),
            "checked_at": now,
            "verification_time": now
        }

        names = []
        vals = []

        for key, value in values.items():

            if key in columns:

                names.append(
                    key
                )

                vals.append(
                    value
                )

        if not names:
            return

        columns_sql = ", ".join(
            names
        )

        placeholders = ", ".join(
            "?"
            for _ in names
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
            vals
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
            "verified": False,
            "booking_verified": False,
            "source_disagreement_points": 0
        }

    normal_price = normal_data.get(
        "normal_price"
    )

    # --------------------------------------------------------
    # 1 - Tarihsel anomali / 20
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # 2 - Piyasa sapmasi / 25
    # --------------------------------------------------------

    market = calculate_market_divergence(
        flight["price"],
        normal_price
    )

    # --------------------------------------------------------
    # 3 - Ani dusus / 10
    # --------------------------------------------------------

    sudden = calculate_sudden_drop(
        flight["price"],
        previous_price
    )

    # --------------------------------------------------------
    # 4 - Kaynak farki / 10
    # --------------------------------------------------------

    source_points = int(
        verification.get(
            "source_disagreement_points",
            0
        )
    )

    # --------------------------------------------------------
    # 5 - Vergi anomalisi / 10
    # --------------------------------------------------------

    tax_analysis = calculate_tax_anomaly(
        flight
    )

    tax_points = tax_analysis[
        "points"
    ]

    # --------------------------------------------------------
    # 6 - Kur / 5
    # --------------------------------------------------------

    currency_points = (
        calculate_currency_anomaly(
            flight
        )
    )

    # --------------------------------------------------------
    # 7 - Fare / 5
    # --------------------------------------------------------

    fare_points = (
        calculate_fare_anomaly(
            flight
        )
    )

    # --------------------------------------------------------
    # 8 - Kisa sure / 5
    # --------------------------------------------------------

    persistence_points = 0

    if departure_date:

        persistence_points = (
            calculate_short_lived_persistence(
                flight,
                departure_date
            )
        )

    # --------------------------------------------------------
    # 9 - Kaynak guvenilirligi / 5
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

        "market_points": market[
            "market_points"
        ],

        "sudden_drop_points": sudden[
            "points"
        ],

        "source_disagreement_points": source_points,

        "tax_points": tax_points,

        "tax_current_ratio": tax_analysis[
            "current_ratio"
        ],

        "tax_normal_ratio": tax_analysis[
            "normal_ratio"
        ],

        "tax_deviation": tax_analysis[
            "deviation"
        ],

        "tax_sample_count": tax_analysis[
            "sample_count"
        ],

        "tax_confidence": tax_analysis[
            "confidence"
        ],

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
        flight
    ):
        score += 10

    duration = flight.get(
        "duration_minutes",
        0
    )

    if (
        duration
        and duration <= 180
    ):
        score += 10

    return round(
        min(
            score,
            100
        ),
        2
    )


# ============================================================
# SKOR ESIGI
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


def get_alert_level(
    score
):

    if score >= get_threshold(
        "high_confidence",
        90
    ):

        return "KRITIK HATA FIYATI"

    if score >= get_threshold(
        "candidate",
        85
    ):

        return "HATA FIYATI ADAYI"

    if score >= get_threshold(
        "suspicious",
        70
    ):

        return "SUPHELI FIYAT"

    if score >= get_threshold(
        "good_deal",
        50
    ):

        return "IYI FIRSAT"

    if score >= get_threshold(
        "normal",
        30
    ):

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
# NEDEN HATA FIYATI?
# ============================================================

def explain_error_fare(
    score_data,
    flight,
    verification
):

    reasons = []

    if score_data[
        "historical_points"
    ] >= 11:

        reasons.append(
            "rotanin ogrendigi normal fiyata "
            "gore cok buyuk sapma var"
        )

    if score_data[
        "market_points"
    ] >= 13:

        reasons.append(
            "piyasa normalinin belirgin "
            "sekilde altinda"
        )

    if score_data[
        "sudden_drop_points"
    ] >= 6:

        reasons.append(
            "onceki gozleme gore ani fiyat dususu var"
        )

    if score_data[
        "source_disagreement_points"
    ] >= 5:

        reasons.append(
            "booking kaynaklari arasinda "
            "belirgin fiyat farki var"
        )

    # --------------------------------------------------------
    # V3 VERGI ACIKLAMASI
    # --------------------------------------------------------

    if score_data.get(
        "tax_points",
        0
    ) >= 4:

        current_ratio = score_data.get(
            "tax_current_ratio"
        )

        normal_ratio = score_data.get(
            "tax_normal_ratio"
        )

        if (
            current_ratio is not None
            and normal_ratio is not None
        ):

            reasons.append(
                "vergi orani rotanin normal "
                "vergi oranindan belirgin sekilde dusuk"
            )

        else:

            reasons.append(
                "fiyat yapisinda vergi anomalisi tespit edildi"
            )

    if score_data[
        "currency_points"
    ] > 0:

        reasons.append(
            "TR pazarinda beklenmeyen "
            "para birimi goruldu"
        )

    if flight.get(
        "self_transfer",
        False
    ):

        reasons.append(
            "self-transfer ihtimali var; "
            "ucuzlugun nedeni bu olabilir"
        )

    if flight.get(
        "checked_bags"
    ) == 0:

        reasons.append(
            "kayitli bagaj dahil degil veya "
            "0 gorunuyor"
        )

    if flight.get(
        "price_status"
    ) != "verified":

        reasons.append(
            "Ignav fiyat durumu tam "
            "verified degil"
        )

    if verification.get(
        "booking_verified",
        False
    ):

        reasons.append(
            "booking kaynagi kontrolunden de gecti"
        )

    if not reasons:

        reasons.append(
            "fiyat normal seviyenin belirgin altinda"
        )

    return reasons


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


def send_telegram_message(
    message
):

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
# ALERT
# ============================================================

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

    reasons = explain_error_fare(
        score_data,
        flight,
        verification
    )

    normal_price = score_data[
        "normal_price"
    ]

    message = (
        f"{level}\n\n"
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
        )

    message += (
        f"Fiyat durumu: "
        f"{flight.get('price_status', 'unknown')}\n"
        f"Bagaj: "
        f"{flight.get('baggage', '')}\n"
        f"Durak: "
        f"{flight.get('stops', 0)}\n"
        f"Self-transfer: "
        f"{'EVET' if flight.get('self_transfer') else 'HAYIR'}\n"
    )

    # --------------------------------------------------------
    # V3 FIYAT YAPISI
    # --------------------------------------------------------

    if flight.get(
        "base_fare"
    ) is not None:

        message += (
            f"Base fare: "
            f"{flight['base_fare']:.2f} "
            f"{flight['currency']}\n"
        )

    if flight.get(
        "taxes"
    ) is not None:

        message += (
            f"Vergi: "
            f"{flight['taxes']:.2f} "
            f"{flight['currency']}\n"
        )

    if flight.get(
        "fees"
    ) is not None:

        message += (
            f"Ucretler: "
            f"{flight['fees']:.2f} "
            f"{flight['currency']}\n"
        )

    if score_data.get(
        "tax_current_ratio"
    ) is not None:

        message += (
            f"Vergi orani: "
            f"%{score_data['tax_current_ratio'] * 100:.1f}\n"
        )

    if score_data.get(
        "tax_normal_ratio"
    ) is not None:

        message += (
            f"Normal vergi orani: "
            f"%{score_data['tax_normal_ratio'] * 100:.1f}\n"
        )

    message += (
        f"\n"
        f"HATA SKORU: "
        f"{score:.1f}/100\n"
        f"FIRSAT SKORU: "
        f"{opportunity_score:.1f}/100\n\n"
        f"NEDEN DIKKAT CEKTI?\n"
    )

    for reason in reasons:

        message += (
            f"- {reason}\n"
        )

    message += (
        f"\nSKOR DETAYI\n"
        f"Tarihsel anomali: "
        f"{score_data['historical_points']}/20\n"
        f"Piyasa sapmasi: "
        f"{score_data['market_points']}/25\n"
        f"Ani dusus: "
        f"{score_data['sudden_drop_points']}/10\n"
        f"Kaynak farki: "
        f"{score_data['source_disagreement_points']}/10\n"
        f"Vergi anomalisi: "
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
        f"Ucus: "
        f"{flight['flight_number']}\n"
        f"Sure: "
        f"{flight['duration_minutes']} dk\n"
        f"Dogrulama: BASARILI"
    )

    booking_analysis = verification.get(
        "booking_analysis"
    )

    if booking_analysis:

        message += (
            f"\n\nBOOKING KONTROLU\n"
            f"Saglayici sayisi: "
            f"{booking_analysis['provider_count']}\n"
            f"Farkli fiyat sayisi: "
            f"{booking_analysis.get('price_point_count', 0)}\n"
            f"En dusuk booking: "
            f"{booking_analysis['min_price']} "
            f"{flight['currency']}\n"
            f"En yuksek booking: "
            f"{booking_analysis['max_price']} "
            f"{flight['currency']}\n"
            f"Saglayici farki: "
            f"%{booking_analysis['spread_percent']}\n"
        )

        providers = booking_analysis.get(
            "providers",
            []
        )

        if providers:

            message += (
                "Kaynaklar: "
                +
                ", ".join(
                    providers[:5]
                )
            )

    if not send_telegram_message(
        message
    ):
        return False

    conn = get_connection()

    try:

        columns = table_columns(
            conn,
            "alerts"
        )

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

        names = []
        vals = []

        for key, value in values.items():

            if key in columns:

                names.append(
                    key
                )

                vals.append(
                    value
                )

        if names:

            conn.execute(
                f"""
                INSERT INTO alerts (
                    {", ".join(names)}
                )
                VALUES (
                    {", ".join("?" for _ in names)}
                )
                """,
                vals
            )

            conn.commit()

    except Exception as e:

        print(
            "Alert kayit hatasi:",
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

    domestic = airports.get(
        "domestic_destinations",
        []
    )

    europe = airports.get(
        "europe_destinations",
        []
    )

    routes = []

    # --------------------------------------------------------
    # DOMESTIK
    # --------------------------------------------------------

    if airports.get(
        "domestic_enabled",
        True
    ):

        for origin in priority_origins:

            for destination in domestic:

                if origin != destination:

                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

    # --------------------------------------------------------
    # AVRUPA
    # --------------------------------------------------------

    if airports.get(
        "europe_enabled",
        True
    ):

        for origin in priority_origins:

            for destination in europe:

                if origin != destination:

                    routes.append(
                        (
                            origin,
                            destination
                        )
                    )

        for destination in priority_origins:

            for origin in europe:

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

        seen.add(
            route
        )

        unique.append(
            route
        )

    return unique


# ============================================================
# ROTA DONUSUMU
# ============================================================

def get_next_routes(
    all_routes
):

    if not all_routes:
        return [], 0

    route_count = int(
        settings.get(
            "radar",
            {}
        ).get(
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

            start = int(
                row[0] or 0
            )

        except Exception:

            start = 0

    else:

        start = 0

    selected = []

    for offset in range(
        min(
            route_count,
            len(all_routes)
        )
    ):

        index = (
            start +
            offset
        ) % len(all_routes)

        selected.append(
            all_routes[index]
        )

    next_index = (
        start +
        len(selected)
    ) % len(all_routes)

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

    return selected, start


# ============================================================
# TARIHLER
# ============================================================

def build_departure_dates():

    days = settings.get(
        "radar",
        {}
    ).get(
        "dates_days_ahead",
        [30, 60, 90]
    )

    base = utc_now()

    dates = []

    for day in days:

        try:

            day = int(
                day
            )

        except Exception:

            continue

        dates.append(
            (
                base +
                timedelta(
                    days=day
                )
            ).strftime(
                "%Y-%m-%d"
            )
        )

    return dates


# ============================================================
# ANA PROGRAM
# ============================================================

def main():

    print("")
    print("=" * 65)
    print(
        "UÇUŞ HATA FİYATI RADARI V3.0"
    )
    print("=" * 65)

    init_db()

    all_routes = build_routes()

    routes, start_index = (
        get_next_routes(
            all_routes
        )
    )

    dates = build_departure_dates()

    print(
        f"Toplam rota havuzu: "
        f"{len(all_routes)}"
    )

    print(
        f"Bu çalışmada taranacak rota: "
        f"{len(routes)}"
    )

    print(
        "Tarihler: "
        +
        ", ".join(
            dates
        )
    )

    print(
        f"Market: {get_market()}"
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

    print(
        "V3: Vergi/fare analizi AKTIF"
    )

    # --------------------------------------------------------
    # VERIFICATION ESIGI
    # --------------------------------------------------------

    verification_settings = settings.get(
        "radar",
        {}
    ).get(
        "verification",
        {}
    )

    verification_minimum = int(
        verification_settings.get(
            "minimum_score_for_verification",
            30
        )
    )

    # Booking-links daha pahali/ek API
    # kullanabilecegi icin ciddi adaylarda calisir.
    booking_minimum = 70

    total_searches = 0
    total_flights = 0
    total_verified = 0
    total_booking_checks = 0
    total_alerts = 0

    # --------------------------------------------------------
    # ROTA TARAMA
    # --------------------------------------------------------

    for origin, destination in routes:

        for departure_date in dates:

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

                preliminary = (
                    calculate_error_score(
                        flight,
                        normal_data,
                        previous_price,
                        {
                            "verified": False,
                            "booking_verified": False,
                            "source_disagreement_points": 0
                        },
                        departure_date
                    )
                )

                opportunity = (
                    calculate_opportunity_score(
                        flight,
                        normal_data,
                        previous_price
                    )
                )

                tax_text = ""

                if flight.get(
                    "tax_ratio"
                ) is not None:

                    tax_text = (
                        f" | vergi "
                        f"%{flight['tax_ratio'] * 100:.1f}"
                    )

                print(
                    f"  "
                    f"{flight['airline']} "
                    f"{flight['flight_number']} "
                    f"{flight['price']:.2f} "
                    f"{flight['currency']} "
                    f"| durak "
                    f"{flight['stops']} "
                    f"{tax_text}"
                    f" | on skor "
                    f"{preliminary['score']:.1f}"
                )

                save_flight(
                    flight,
                    departure_date
                )

                # ------------------------------------------------
                # DOGRULAMA
                # ------------------------------------------------

                if (
                    preliminary["score"]
                    <
                    verification_minimum
                ):
                    continue

                print(
                    "    -> Fiyat "
                    "dogrulamasi basliyor."
                )

                run_booking_check = (
                    preliminary["score"]
                    >=
                    booking_minimum
                )

                if run_booking_check:

                    print(
                        "    -> Yuksek aday: "
                        "booking kaynaklari da kontrol edilecek."
                    )

                    total_booking_checks += 1

                verification = verify_flight(
                    flight,
                    departure_date,
                    run_booking_check
                )

                save_verification(
                    flight,
                    departure_date,
                    verification
                )

                if verification.get(
                    "verified",
                    False
                ):

                    total_verified += 1

                # ------------------------------------------------
                # FINAL SKOR
                # ------------------------------------------------

                final_score_data = (
                    calculate_error_score(
                        flight,
                        normal_data,
                        previous_price,
                        verification,
                        departure_date
                    )
                )

                final_score = (
                    final_score_data[
                        "score"
                    ]
                )

                final_opportunity = (
                    calculate_opportunity_score(
                        flight,
                        normal_data,
                        previous_price
                    )
                )

                print(
                    f"    -> Final skor: "
                    f"{final_score:.1f}"
                )

                if final_score_data.get(
                    "tax_points",
                    0
                ) > 0:

                    current_tax = (
                        final_score_data.get(
                            "tax_current_ratio"
                        )
                    )

                    normal_tax = (
                        final_score_data.get(
                            "tax_normal_ratio"
                        )
                    )

                    if current_tax is not None:

                        print(
                            "    -> Vergi orani: "
                            f"%{current_tax * 100:.1f}"
                        )

                    if normal_tax is not None:

                        print(
                            "    -> Normal vergi orani: "
                            f"%{normal_tax * 100:.1f}"
                        )

                    print(
                        "    -> Vergi anomalisi: "
                        f"{final_score_data['tax_points']}/10"
                    )

                if verification.get(
                    "booking_verified",
                    False
                ):

                    booking_analysis = (
                        verification.get(
                            "booking_analysis",
                            {}
                        )
                    )

                    print(
                        "    -> Booking kaynaklari: "
                        f"{booking_analysis.get('provider_count', 0)}"
                    )

                    print(
                        "    -> Kaynak farki: "
                        f"%{booking_analysis.get('spread_percent', 0)}"
                    )

                # ------------------------------------------------
                # ALERT
                # ------------------------------------------------

                if create_and_send_alert(
                    flight,
                    departure_date,
                    final_score_data,
                    final_opportunity,
                    verification
                ):

                    total_alerts += 1

    # --------------------------------------------------------
    # OZET
    # --------------------------------------------------------

    print("")
    print("=" * 65)
    print(
        "TARAMA TAMAMLANDI - V3.0"
    )
    print("=" * 65)

    print(
        f"API aramasi: "
        f"{total_searches}"
    )

    print(
        f"Bulunan ucus: "
        f"{total_flights}"
    )

    print(
        f"Dogrulanan: "
        f"{total_verified}"
    )

    print(
        f"Booking kontrolu: "
        f"{total_booking_checks}"
    )

    print(
        f"Gonderilen alert: "
        f"{total_alerts}"
    )

    print("=" * 65)


# ============================================================
# BASLAT
# ============================================================

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
