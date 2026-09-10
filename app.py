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
# VERITABANI
# ============================================================

def get_connection():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute(
        "PRAGMA journal_mode=WAL"
    )

    return conn


def table_info(
    conn,
    table_name
):

    return conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()


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
# DATABASE KURULUM / MIGRATION
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
    # ESKI KAYITLAR
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
# SAYISAL YARDIMCILAR
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
        return float(value)

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
# IGNAV
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

        payload[
            "max_price"
        ] = max_price

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

    attempts = 3

    for attempt in range(
        1,
        attempts + 1
    ):

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

            if response.status_code == 200:

                try:

                    return response.json()

                except Exception as e:

                    print(
                        "Ignav JSON hatasi:",
                        repr(e)
                    )

                    return None

            print(
                "Ignav API cevabi:",
                response.text[:700]
            )

            if attempt < attempts:

                time.sleep(
                    attempt * 2
                )

        except Exception as e:

            print(
                f"Ignav arama hatasi "
                f"(deneme {attempt}/{attempts}):",
                repr(e)
            )

            if attempt < attempts:

                time.sleep(
                    attempt * 2
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
                response.text[:700]
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
# UCUS IMZASI
# ============================================================

def normalize_text(
    value
):

    return str(
        value or ""
    ).strip().upper()


def segment_signature(
    segment
):

    if not isinstance(
        segment,
        dict
    ):
        return ""

    return "|".join(
        [
            normalize_text(
                segment.get(
                    "departure_airport"
                )
            ),
            normalize_text(
                segment.get(
                    "arrival_airport"
                )
            ),
            normalize_text(
                segment.get(
                    "marketing_carrier_code"
                )
                or
                segment.get(
                    "carrier_code"
                )
            ),
            normalize_text(
                segment.get(
                    "flight_number"
                )
            ),
            normalize_text(
                segment.get(
                    "departure_time"
                )
                or
                segment.get(
                    "departure_datetime"
                )
            ),
            normalize_text(
                segment.get(
                    "arrival_time"
                )
                or
                segment.get(
                    "arrival_datetime"
                )
            )
        ]
    )


def create_flight_key(
    origin,
    destination,
    flight_number,
    airline,
    duration,
    stops,
    segments=None
):

    if segments:

        signatures = []

        for segment in segments:

            signature = segment_signature(
                segment
            )

            if signature:
                signatures.append(
                    signature
                )

        if signatures:

            full_signature = "||".join(
                signatures
            )

            return (
                f"{normalize_text(origin)}-"
                f"{normalize_text(destination)}-"
                f"{normalize_text(airline)}-"
                f"{normalize_text(flight_number)}-"
                f"{duration}-"
                f"{stops}-"
                f"{full_signature}"
            )

    return (
        f"{normalize_text(origin)}-"
        f"{normalize_text(destination)}-"
        f"{normalize_text(airline)}-"
        f"{normalize_text(flight_number)}-"
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
# PARA ALANI
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

            if abs(
                difference
            ) > (
                max(
                    total * 0.10,
                    10
                )
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
        and taxes <= total
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
# UCUS AYIKLAMA
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

        numeric_price = safe_float(
            price
        )

        if (
            numeric_price is None
            or numeric_price <= 0
        ):
            continue

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

        valid_segments = [
            segment
            for segment in segments
            if isinstance(
                segment,
                dict
            )
        ]

        if not valid_segments:
            continue

        first = valid_segments[0]
        last = valid_segments[-1]

        origin = first.get(
            "departure_airport",
            ""
        )

        destination = last.get(
            "arrival_airport",
            ""
        )

        if not origin or not destination:
            continue

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
            len(valid_segments) - 1,
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
            stops,
            valid_segments
        )

        # ----------------------------------------------------
        # GÜÇLÜ DEDUP
        # ----------------------------------------------------

        duplicate_key = (
            flight_key,
            round(
                numeric_price,
                2
            ),
            normalize_text(
                currency
            ),
            int(
                self_transfer
            )
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
                "segments": valid_segments,

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

        # 20 alan / 20 placeholder
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
    # GÖZLEM
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
# ÖNCEKİ FİYAT
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

        try:

            return float(
                row[0]
            )

        except Exception:
            return None

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
# NORMAL FIYAT
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
            AND ABS(price - ?) > 0.01
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

    values = []

    for row in rows:

        try:

            values.append(
                float(
                    row[0]
                )
            )

        except Exception:
            pass

    return values


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
# VERGI GECMISI
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

    if deviation >= 0.20:
        points = 8

    elif deviation >= 0.12:
        points = 6

    elif deviation >= 0.07:
        points = 4

    elif deviation >= 0.04:
        points = 2

    elif deviation <= -0.25:
        points = 3

    elif deviation <= -0.15:
        points = 2

    elif deviation <= -0.08:
        points = 1

    if len(history) >= 20:
        confidence = "yuksek"

    elif len(history) >= 8:
        confidence = "orta"

    else:
        confidence = "dusuk"

    return {
        "points": min(
            points,
            8
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
        points = 15

    elif drop >= 50:
        points = 13

    elif drop >= 40:
        points = 11

    elif drop >= 30:
        points = 9

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
# BOOKING ANALIZI
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
            "providers": [],
            "urls": []
        }

    options = booking_data.get(
        "booking_options",
        []
    )

    prices = []
    providers = []
    urls = []

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

            amount = safe_float(
                price_info.get(
                    "amount"
                )
            )

            link_currency = price_info.get(
                "currency"
            )

            if amount is None:
                continue

            if link_currency != currency:
                continue

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

            url = (
                link.get("url")
                or link.get("booking_url")
                or link.get("href")
                or ""
            )

            if not provider and url:

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
                str(provider)
            )

            if url:

                urls.append(
                    {
                        "provider": str(
                            provider
                        ),
                        "url": str(
                            url
                        ),
                        "price": amount
                    }
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
            "providers": [],
            "urls": []
        }

    minimum = min(
        unique_prices
    )

    maximum = max(
        unique_prices
    )

    if minimum > 0:

        spread = (
            (
                maximum -
                minimum
            )
            /
            minimum
        ) * 100

        search_gap = (
            (
                search_price -
                minimum
            )
            /
            minimum
        ) * 100

    else:

        spread = 0
        search_gap = 0

    points = 0

    if len(unique_prices) >= 3:

        if spread >= 30:
            points = 12

        elif spread >= 20:
            points = 10

        elif spread >= 10:
            points = 7

        elif spread >= 5:
            points = 3

    elif len(unique_prices) >= 2:

        if spread >= 30:
            points = 10

        elif spread >= 20:
            points = 8

        elif spread >= 10:
            points = 5

        elif spread >= 5:
            points = 2

    if abs(
        search_gap
    ) >= 30:

        points = min(
            15,
            points + 4
        )

    elif abs(
        search_gap
    ) >= 20:

        points = min(
            15,
            points + 3
        )

    elif abs(
        search_gap
    ) >= 10:

        points = min(
            15,
            points + 1
        )

    # URL'leri provider bazinda tekilleştir
    clean_urls = []
    url_seen = set()

    for item in urls:

        url = item.get(
            "url"
        )

        if not url:
            continue

        if url in url_seen:
            continue

        url_seen.add(
            url
        )

        clean_urls.append(
            item
        )

    return {
        "points": min(
            points,
            15
        ),
        "provider_count": len(
            unique_providers
        ),
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
        "providers": unique_providers,
        "urls": clean_urls
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

        return 3

    return 0


# ============================================================
# FARE KALITESI
# ============================================================

def calculate_fare_anomaly(
    flight
):

    points = 0

    if flight.get(
        "self_transfer",
        False
    ):

        # Self transfer hata fiyatinin
        # guvenilirligini azaltir.
        points -= 3

    if has_checked_baggage(
        flight
    ):

        points += 2

    if flight.get(
        "stops",
        0
    ) == 0:

        points += 1

    return max(
        0,
        min(
            points,
            3
        )
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

    if count == 3:
        return 1

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

    return min(
        points,
        5
    )


# ============================================================
# VERIFICATION
# ============================================================

def flight_match_score(
    candidate,
    target
):

    score = 0

    if (
        candidate.get("flight_key")
        ==
        target.get("flight_key")
    ):

        return 100

    if (
        normalize_text(
            candidate.get(
                "airline"
            )
        )
        ==
        normalize_text(
            target.get(
                "airline"
            )
        )
    ):

        score += 20

    if (
        normalize_text(
            candidate.get(
                "flight_number"
            )
        )
        ==
        normalize_text(
            target.get(
                "flight_number"
            )
        )
    ):

        score += 40

    if (
        normalize_text(
            candidate.get(
                "origin"
            )
        )
        ==
        normalize_text(
            target.get(
                "origin"
            )
        )
    ):

        score += 10

    if (
        normalize_text(
            candidate.get(
                "destination"
            )
        )
        ==
        normalize_text(
            target.get(
                "destination"
            )
        )
    ):

        score += 10

    if (
        candidate.get(
            "stops",
            0
        )
        ==
        target.get(
            "stops",
            0
        )
    ):

        score += 5

    duration_a = candidate.get(
        "duration_minutes",
        0
    )

    duration_b = target.get(
        "duration_minutes",
        0
    )

    if (
        duration_a
        and duration_b
    ):

        if abs(
            duration_a -
            duration_b
        ) <= 15:

            score += 10

    if (
        candidate.get(
            "self_transfer",
            False
        )
        ==
        target.get(
            "self_transfer",
            False
        )
    ):

        score += 5

    return score


def verify_flight(
    flight,
    departure_date,
    run_booking_check=False
):

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
            "booking_data": None,
            "reason": (
                "Ikinci Ignav aramasi basarisiz."
            )
        }

    flights = extract_flights(
        data
    )

    matched = None

    # --------------------------------------------------------
    # 1. Önce birebir flight key
    # --------------------------------------------------------

    for item in flights:

        if (
            item["flight_key"]
            !=
            flight["flight_key"]
        ):
            continue

        if (
            item["currency"]
            !=
            flight["currency"]
        ):
            continue

        if (
            abs(
                item["price"]
                -
                flight["price"]
            )
            > 0.01
        ):
            continue

        if (
            item["self_transfer"]
            !=
            flight["self_transfer"]
        ):
            continue

        matched = item
        break

    # --------------------------------------------------------
    # 2. Eski DB verisi / key degisikligi icin fallback
    # --------------------------------------------------------

    if matched is None:

        candidates = []

        for item in flights:

            if (
                item["currency"]
                !=
                flight["currency"]
            ):
                continue

            if (
                abs(
                    item["price"]
                    -
                    flight["price"]
                )
                > 0.01
            ):
                continue

            if (
                item["self_transfer"]
                !=
                flight["self_transfer"]
            ):
                continue

            match_score = flight_match_score(
                item,
                flight
            )

            if match_score >= 70:

                candidates.append(
                    (
                        match_score,
                        item
                    )
                )

        if candidates:

            candidates.sort(
                key=lambda x: x[0],
                reverse=True
            )

            matched = candidates[0][1]

    if matched is None:

        return {
            "verified": False,
            "booking_verified": False,
            "source_disagreement_points": 0,
            "booking_data": None,
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
    # BOOKING
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

            else:

                result[
                    "booking_analysis"
                ] = booking_analysis

                result["reason"] += (
                    " Booking endpointi yanit verdi "
                    "ancak kullanilabilir fiyat "
                    "kaynagi bulunamadi."
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

        if names:

            conn.execute(
                f"""
                INSERT INTO verifications (
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
    # 1 - TARİHSEL ANOMALI / 20
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
    # 2 - PIYASA / 25
    # --------------------------------------------------------

    market = calculate_market_divergence(
        flight["price"],
        normal_price
    )

    # --------------------------------------------------------
    # 3 - ANI DÜŞÜŞ / 15
    # --------------------------------------------------------

    sudden = calculate_sudden_drop(
        flight["price"],
        previous_price
    )

    # --------------------------------------------------------
    # 4 - BOOKING / 15
    # --------------------------------------------------------

    source_points = int(
        verification.get(
            "source_disagreement_points",
            0
        )
    )

    source_points = min(
        source_points,
        15
    )

    # --------------------------------------------------------
    # 5 - VERGI / 8
    # --------------------------------------------------------

    tax_analysis = calculate_tax_anomaly(
        flight
    )

    tax_points = tax_analysis[
        "points"
    ]

    # --------------------------------------------------------
    # 6 - KUR / 3
    # --------------------------------------------------------

    currency_points = (
        calculate_currency_anomaly(
            flight
        )
    )

    # --------------------------------------------------------
    # 7 - FARE / 3
    # --------------------------------------------------------

    fare_points = (
        calculate_fare_anomaly(
            flight
        )
    )

    # --------------------------------------------------------
    # 8 - PERSISTENCE / 2
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
    # 9 - KAYNAK GÜVEN / 5
    # --------------------------------------------------------

    reliability_points = (
        calculate_source_reliability(
            verification
        )
    )

    # --------------------------------------------------------
    # 10 - PRICE STATUS BONUS
    #
    # Verified fiyat bilgisi, API'nin kendi teyididir.
    # Ayrı bir 5 puan yerine reliability içine dahil edilmez;
    # burada sadece kalite filtresinde kullanilir.
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
# HATA FIYATI KALITE KAPISI
# ============================================================

def is_real_error_fare_candidate(
    flight,
    score_data,
    verification,
    opportunity_score
):

    if not verification.get(
        "verified",
        False
    ):
        return False

    score = score_data.get(
        "score",
        0
    )

    historical_drop = score_data.get(
        "drop_percent",
        0
    )

    previous_drop = score_data.get(
        "previous_drop_percent",
        0
    )

    booking_verified = verification.get(
        "booking_verified",
        False
    )

    source_points = score_data.get(
        "source_disagreement_points",
        0
    )

    tax_points = score_data.get(
        "tax_points",
        0
    )

    self_transfer = flight.get(
        "self_transfer",
        False
    )

    # --------------------------------------------------------
    # Self-transfer uçuşlarda çok daha sıkı filtre.
    # --------------------------------------------------------

    if self_transfer:

        return (
            score >= 90
            and booking_verified
            and historical_drop >= 50
        )

    # --------------------------------------------------------
    # Gerçek hata fiyatı için temel kanıt:
    # --------------------------------------------------------

    strong_price_drop = (
        historical_drop >= 40
        or previous_drop >= 35
    )

    strong_market_drop = (
        historical_drop >= 50
    )

    booking_evidence = (
        booking_verified
        and source_points >= 3
    )

    tax_evidence = (
        tax_points >= 4
    )

    # --------------------------------------------------------
    # Çok güçlü piyasa sapması
    # --------------------------------------------------------

    if (
        score >= 90
        and strong_market_drop
    ):

        return True

    # --------------------------------------------------------
    # Booking ile doğrulanmış ciddi sapma
    # --------------------------------------------------------

    if (
        score >= 85
        and strong_price_drop
        and booking_evidence
    ):

        return True

    # --------------------------------------------------------
    # Booking yok ama hem tarihsel hem ani düşüş çok güçlü
    # --------------------------------------------------------

    if (
        score >= 85
        and historical_drop >= 50
        and previous_drop >= 30
    ):

        return True

    # --------------------------------------------------------
    # Vergi anomalisi + çok güçlü fiyat sapması
    # --------------------------------------------------------

    if (
        score >= 85
        and historical_drop >= 50
        and tax_evidence
    ):

        return True

    return False


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
# ESİK
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

    return "NORMAL"


# ============================================================
# ALERT İZNİ
# ============================================================

def alert_allowed_by_level(
    score,
    flight,
    score_data,
    verification,
    opportunity_score
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

    # --------------------------------------------------------
    # Gerçek hata fiyatı / aday için kalite kapısı
    # --------------------------------------------------------

    if score >= get_threshold(
        "candidate",
        85
    ):

        return is_real_error_fare_candidate(
            flight,
            score_data,
            verification,
            opportunity_score
        )

    # --------------------------------------------------------
    # 70-84 aralığında sadece ayarlarda açık olsa bile
    # normalde Telegram'a gönderme.
    #
    # Böylece radar "ucuz uçuş botuna" dönüşmez.
    # --------------------------------------------------------

    return False


# ============================================================
# ALERT TEKRARI
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
            "rotanin normal fiyatina gore "
            "cok buyuk sapma var"
        )

    if score_data[
        "market_points"
    ] >= 13:

        reasons.append(
            "normal fiyat seviyesinin belirgin "
            "sekilde altinda"
        )

    if score_data[
        "sudden_drop_points"
    ] >= 9:

        reasons.append(
            "onceki gozleme gore cok buyuk "
            "fiyat dususu var"
        )

    elif score_data[
        "sudden_drop_points"
    ] >= 6:

        reasons.append(
            "onceki gozleme gore belirgin "
            "fiyat dususu var"
        )

    if score_data[
        "source_disagreement_points"
    ] >= 5:

        reasons.append(
            "booking kaynaklarinda belirgin "
            "fiyat farki goruldu"
        )

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
                "vergi orani normal seviyenin "
                "belirgin sekilde altinda"
            )

    if flight.get(
        "self_transfer",
        False
    ):

        reasons.append(
            "self-transfer var; bu nedenle "
            "fiyat ekstra dikkatle degerlendiriliyor"
        )

    if has_checked_baggage(
        flight
    ):

        reasons.append(
            "kayitli bagaj dahil"
        )

    if verification.get(
        "booking_verified",
        False
    ):

        reasons.append(
            "booking kaynagi da dogrulandi"
        )

    if not reasons:

        reasons.append(
            "fiyat normal seviyenin belirgin "
            "sekilde altinda ve ikinci aramada dogrulandi"
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
                "text": message,
                "disable_web_page_preview": False
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
        score,
        flight,
        score_data,
        verification,
        opportunity_score
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
        "🚨 HATA FIYATI BULUNDU\n\n"
        f"✈️ {flight['origin']} → "
        f"{flight['destination']}\n"
        f"📅 {departure_date}\n"
        f"✈️ {flight['airline']} "
        f"{flight['flight_number']}\n\n"
        f"💰 Fiyat: "
        f"{flight['price']:.2f} "
        f"{flight['currency']}\n"
    )

    if normal_price is not None:

        message += (
            f"📊 Normal fiyat: "
            f"{normal_price:.2f} "
            f"{flight['currency']}\n"
            f"📉 Indirim: "
            f"%{score_data['drop_percent']:.1f}\n"
        )

    message += (
        f"\n🎯 Hata fiyati skoru: "
        f"{score:.1f}/100\n"
        f"🔥 {level}\n"
        f"🎁 Firsat skoru: "
        f"{opportunity_score:.1f}/100\n\n"
    )

    # --------------------------------------------------------
    # Fiyat yapısı
    # --------------------------------------------------------

    if flight.get(
        "base_fare"
    ) is not None:

        message += (
            f"🧾 Base fare: "
            f"{flight['base_fare']:.2f} "
            f"{flight['currency']}\n"
        )

    if flight.get(
        "taxes"
    ) is not None:

        message += (
            f"💸 Vergiler: "
            f"{flight['taxes']:.2f} "
            f"{flight['currency']}\n"
        )

    if flight.get(
        "fees"
    ) is not None:

        message += (
            f"💰 Ucretler: "
            f"{flight['fees']:.2f} "
            f"{flight['currency']}\n"
        )

    if score_data.get(
        "tax_current_ratio"
    ) is not None:

        message += (
            f"📊 Vergi orani: "
            f"%{score_data['tax_current_ratio'] * 100:.1f}\n"
        )

    if score_data.get(
        "tax_normal_ratio"
    ) is not None:

        message += (
            f"📊 Normal vergi orani: "
            f"%{score_data['tax_normal_ratio'] * 100:.1f}\n"
        )

    message += "\n"

    # --------------------------------------------------------
    # Uçuş kalitesi
    # --------------------------------------------------------

    checked = flight.get(
        "checked_bags"
    )

    if checked is not None:

        baggage_line = (
            f"{checked} adet"
            if checked > 0
            else "Kayitli bagaj yok"
        )

    else:

        baggage_line = (
            flight.get(
                "baggage",
                ""
            )
            or "Belirtilmemis"
        )

    if flight.get(
        "stops",
        0
    ) == 0:

        stops_text = "Direkt"

    else:

        stops_text = (
            f"{flight['stops']} aktarma"
        )

    message += (
        f"🧳 Bagaj: {baggage_line}\n"
        f"🔄 Aktarma: {stops_text}\n"
        f"↪️ Self-transfer: "
        f"{'EVET' if flight.get('self_transfer') else 'HAYIR'}\n"
        f"⏱ Sure: "
        f"{flight['duration_minutes']} dk\n\n"
    )

    # --------------------------------------------------------
    # Neden?
    # --------------------------------------------------------

    message += (
        "🔎 NEDEN DIKKAT CEKTI?\n"
    )

    for reason in reasons:

        message += (
            f"• {reason}\n"
        )

    # --------------------------------------------------------
    # Skor
    # --------------------------------------------------------

    message += (
        "\n📊 SKOR DETAYI\n"
        f"Tarihsel anomali: "
        f"{score_data['historical_points']}/20\n"
        f"Piyasa sapmasi: "
        f"{score_data['market_points']}/25\n"
        f"Ani dusus: "
        f"{score_data['sudden_drop_points']}/15\n"
        f"Booking/kaynak: "
        f"{score_data['source_disagreement_points']}/15\n"
        f"Vergi anomalisi: "
        f"{score_data['tax_points']}/8\n"
        f"Kur anomalisi: "
        f"{score_data['currency_points']}/3\n"
        f"Fare kalitesi: "
        f"{score_data['fare_points']}/3\n"
        f"Kisa sureli davranis: "
        f"{score_data['persistence_points']}/2\n"
        f"Kaynak guvenilirligi: "
        f"{score_data['reliability_points']}/5\n\n"
        f"✅ Fiyat dogrulandi\n"
    )

    # --------------------------------------------------------
    # Booking
    # --------------------------------------------------------

    booking_analysis = verification.get(
        "booking_analysis"
    )

    if verification.get(
        "booking_verified",
        False
    ):

        message += (
            "✅ Booking kaynagi dogrulandi\n"
        )

        if booking_analysis:

            message += (
                f"\nBOOKING KONTROLU\n"
                f"Saglayici: "
                f"{booking_analysis.get('provider_count', 0)}\n"
                f"Farkli fiyat: "
                f"{booking_analysis.get('price_point_count', 0)}\n"
            )

            minimum = booking_analysis.get(
                "min_price"
            )

            maximum = booking_analysis.get(
                "max_price"
            )

            if minimum is not None:

                message += (
                    f"En dusuk: "
                    f"{minimum:.2f} "
                    f"{flight['currency']}\n"
                )

            if maximum is not None:

                message += (
                    f"En yuksek: "
                    f"{maximum:.2f} "
                    f"{flight['currency']}\n"
                )

            message += (
                f"Kaynak farki: "
                f"%{booking_analysis.get('spread_percent', 0):.1f}\n"
            )

            providers = booking_analysis.get(
                "providers",
                []
            )

            if providers:

                message += (
                    "Saglayicilar: "
                    +
                    ", ".join(
                        providers[:5]
                    )
                    +
                    "\n"
                )

    # --------------------------------------------------------
    # Booking URL
    # --------------------------------------------------------

    if booking_analysis:

        urls = booking_analysis.get(
            "urls",
            []
        )

        if urls:

            message += (
                "\n🔗 REZERVASYON BAGLANTILARI\n"
            )

            used = 0

            for item in urls:

                url = item.get(
                    "url"
                )

                provider = item.get(
                    "provider",
                    "Booking"
                )

                if not url:
                    continue

                message += (
                    f"{provider}: {url}\n"
                )

                used += 1

                if used >= 3:
                    break

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if not send_telegram_message(
        message
    ):
        return False

    # --------------------------------------------------------
    # ALERT KAYDI
    # --------------------------------------------------------

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

        date_value = (
            base +
            timedelta(
                days=day
            )
        ).strftime(
            "%Y-%m-%d"
        )

        if date_value not in dates:

            dates.append(
                date_value
            )

    return dates


# ============================================================
# ANA PROGRAM
# ============================================================

def main():

    print("")
    print("=" * 70)
    print(
        "UÇUŞ HATA FİYATI RADARI V3.1"
    )
    print("=" * 70)

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
        "V3.1: "
        "Dedup + verification + booking + "
        "error-fare quality gate AKTIF"
    )

    # --------------------------------------------------------
    # VERIFICATION
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

    # Güçlü adayları booking'e sokmak için
    # eski 70 yerine 60 kullaniyoruz.
    booking_minimum = 60

    total_searches = 0
    total_flights = 0
    total_verified = 0
    total_booking_checks = 0
    total_alerts = 0
    total_candidates = 0

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
                f"Temizlenen ucus: "
                f"{len(flights)}"
            )

            total_flights += len(
                flights
            )

            # ------------------------------------------------
            # Aynı rota/date içinde ekstra güvenlik dedup
            # ------------------------------------------------

            processed = set()

            for flight in flights:

                process_key = (
                    flight["flight_key"],
                    round(
                        flight["price"],
                        2
                    ),
                    flight["currency"],
                    int(
                        flight.get(
                            "self_transfer",
                            False
                        )
                    )
                )

                if process_key in processed:

                    continue

                processed.add(
                    process_key
                )

                # ------------------------------------------------
                # Önceki fiyat
                # ------------------------------------------------

                previous_price = (
                    get_previous_flight_price(
                        flight,
                        departure_date
                    )
                )

                # ------------------------------------------------
                # Normal fiyat
                # ------------------------------------------------

                normal_data = (
                    calculate_normal_price(
                        flight["origin"],
                        flight["destination"],
                        flight["currency"],
                        flight["price"]
                    )
                )

                # ------------------------------------------------
                # Preliminary score
                # ------------------------------------------------

                preliminary_verification = {
                    "verified": False,
                    "booking_verified": False,
                    "source_disagreement_points": 0
                }

                preliminary = (
                    calculate_error_score(
                        flight,
                        normal_data,
                        previous_price,
                        preliminary_verification,
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

                normal_text = ""

                if normal_data.get(
                    "normal_price"
                ) is not None:

                    normal_text = (
                        f" | normal "
                        f"{normal_data['normal_price']:.0f}"
                    )

                print(
                    f"  "
                    f"{flight['airline']} "
                    f"{flight['flight_number']} "
                    f"{flight['price']:.0f} "
                    f"{flight['currency']}"
                    f"{normal_text}"
                    f" | durak "
                    f"{flight['stops']}"
                    f"{tax_text}"
                    f" | on "
                    f"{preliminary['score']:.1f}"
                )

                # ------------------------------------------------
                # Kaydet
                # ------------------------------------------------

                save_flight(
                    flight,
                    departure_date
                )

                # ------------------------------------------------
                # Verification filtresi
                # ------------------------------------------------

                if (
                    preliminary["score"]
                    <
                    verification_minimum
                ):

                    continue

                total_candidates += 1

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
                        "    -> Guclu aday: "
                        "booking kontrolu basliyor."
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

                else:

                    print(
                        "    -> Verification basarisiz."
                    )

                    continue

                # ------------------------------------------------
                # Final skor
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

                # ------------------------------------------------
                # Güçlü hata fiyatı filtresi
                # ------------------------------------------------

                is_candidate = (
                    is_real_error_fare_candidate(
                        flight,
                        final_score_data,
                        verification,
                        final_opportunity
                    )
                )

                print(
                    "    -> Hata fiyati kalite: "
                    +
                    (
                        "GECIYOR"
                        if is_candidate
                        else "GECMIYOR"
                    )
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
                        f"{final_score_data['tax_points']}/8"
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
                        "    -> Booking farki: "
                        f"%{booking_analysis.get('spread_percent', 0)}"
                    )

                # ------------------------------------------------
                # ALERT
                # ------------------------------------------------

                if (
                    is_candidate
                    and
                    create_and_send_alert(
                        flight,
                        departure_date,
                        final_score_data,
                        final_opportunity,
                        verification
                    )
                ):

                    total_alerts += 1

    # --------------------------------------------------------
    # ÖZET
    # --------------------------------------------------------

    print("")
    print("=" * 70)
    print(
        "TARAMA TAMAMLANDI - V3.1"
    )
    print("=" * 70)

    print(
        f"API aramasi: "
        f"{total_searches}"
    )

    print(
        f"Temizlenen ucus: "
        f"{total_flights}"
    )

    print(
        f"Skor adayi: "
        f"{total_candidates}"
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

    print("=" * 70)


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
