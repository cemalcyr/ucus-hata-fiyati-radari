import os
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests


# ============================================================
# UÇUŞ HATA FİYATI RADARI
# V4 - STABLE VERIFICATION ENGINE
# ============================================================

DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"
IGNAV_BASE = "https://ignav.com/api"

BLOCKED_DESTINATIONS = {"AYK"}

TRANSIENT_STATUS = {
    408, 425, 429,
    500, 502, 503, 504,
    522, 524
}

RUN_STARTED_AT = None


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


def table_columns(conn, table):
    return {
        row[1]
        for row in conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def add_column_if_missing(conn, table, name, definition):
    if name not in table_columns(conn, table):
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
        )


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            flight_identity TEXT,
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS price_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            flight_identity TEXT,
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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY,
            route_index INTEGER DEFAULT 0,
            updated_at TEXT
        )
        """
    )

    schemas = {
        "prices": {
            "flight_key": "TEXT",
            "flight_identity": "TEXT",
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
            "tax_ratio": "REAL",
        },
        "price_observations": {
            "flight_key": "TEXT",
            "flight_identity": "TEXT",
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
            "tax_ratio": "REAL",
        },
        "verifications": {
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
            "verification_time": "TEXT",
        },
        "alerts": {
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
            "sent_at": "TEXT",
        },
    }

    for table, fields in schemas.items():
        for name, definition in fields.items():
            add_column_if_missing(
                conn,
                table,
                name,
                definition
            )

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
            VALUES(1, 0, ?)
            """,
            (utc_iso(),)
        )

    # V3.x kayıtlarının stabil kimliklerini tamamla.
    rows = cur.execute(
        """
        SELECT
            id,
            flight_key,
            origin,
            destination,
            departure_date,
            airline,
            flight_number
        FROM price_observations
        WHERE flight_identity IS NULL
           OR flight_identity=''
        """
    ).fetchall()

    for row in rows:
        identity = make_identity(
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            ""
        )

        cur.execute(
            """
            UPDATE price_observations
            SET flight_identity=?
            WHERE id=?
            """,
            (identity, row[0])
        )

    rows = cur.execute(
        """
        SELECT
            id,
            flight_key,
            origin,
            destination,
            departure_date,
            airline,
            flight_number
        FROM prices
        WHERE flight_identity IS NULL
           OR flight_identity=''
        """
    ).fetchall()

    for row in rows:
        identity = make_identity(
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            ""
        )

        cur.execute(
            """
            UPDATE prices
            SET flight_identity=?
            WHERE id=?
            """,
            (identity, row[0])
        )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_obs_identity
        ON price_observations(
            flight_identity,
            observed_at
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_obs_route
        ON price_observations(
            origin,
            destination,
            departure_date,
            currency,
            observed_at
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_prices_identity
        ON prices(
            flight_identity,
            recorded_at
        )
        """
    )

    conn.commit()
    conn.close()

    print("Veritabani kontrolu tamamlandi.")


# ============================================================
# GÜVENLİ DÖNÜŞÜMLER
# ============================================================

def safe_float(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        text = value.strip().replace(" ", "")

        if not text:
            return None

        try:
            if "," in text and "." in text:
                if text.rfind(",") > text.rfind("."):
                    text = text.replace(".", "")
                    text = text.replace(",", ".")
                else:
                    text = text.replace(",", "")

            elif "," in text:
                text = text.replace(",", ".")

            return float(text)

        except ValueError:
            return None

    return None


def normalize_text(value):
    return str(value or "").strip().upper()


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
    }


def get_market():
    return (
        settings
        .get("system", {})
        .get("market", "TR")
        or "TR"
    )


def ignav_search(origin, destination, departure_date):
    if destination in BLOCKED_DESTINATIONS:
        print(
            f"SKIP: {destination} engelli hedef."
        )
        return None

    headers = ignav_headers()

    if not headers:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    passengers = settings.get("passengers", {})
    connections = settings.get("connections", {})

    cabin = (
        "business"
        if settings.get("cabin", {}).get(
            "business",
            False
        )
        else "economy"
    )

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": passengers.get("adults", 1),
        "children": passengers.get("children", 0),
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
        "market": get_market(),
    }

    maximum_price = (
        settings
        .get("price", {})
        .get("maximum_try", 0)
    )

    if maximum_price and maximum_price > 0:
        payload["max_price"] = maximum_price

    disabled = (
        settings
        .get("airlines", {})
        .get("disabled", [])
    )

    if disabled:
        payload["airlines_exclude"] = disabled

    for attempt in range(1, 4):

        try:
            response = requests.post(
                f"{IGNAV_BASE}/fares/one-way",
                headers=headers,
                json=payload,
                timeout=60,
            )

            print(
                f"IGNAV: "
                f"{origin}->{destination} "
                f"{departure_date} "
                f"HTTP {response.status_code}"
            )

            if response.status_code == 200:
                try:
                    return response.json()

                except Exception as exc:
                    print(
                        "IGNAV JSON hatasi:",
                        repr(exc)
                    )
                    return None

            if response.status_code not in TRANSIENT_STATUS:
                print(
                    "IGNAV kalici hata:",
                    response.text[:500]
                )
                return None

            print(
                f"IGNAV gecici hata "
                f"({attempt}/3): "
                f"{response.text[:500]}"
            )

        except (
            requests.Timeout,
            requests.ConnectionError
        ) as exc:

            print(
                f"IGNAV baglanti hatasi "
                f"({attempt}/3):",
                repr(exc)
            )

        except Exception as exc:
            print(
                "IGNAV beklenmeyen hata:",
                repr(exc)
            )
            return None

        if attempt < 3:
            time.sleep(attempt * 2)

    return None


# ============================================================
# BOOKING
# ============================================================

def get_booking_links(ignav_id):
    if not ignav_id:
        return None

    headers = ignav_headers()

    if not headers:
        return None

    for attempt in range(1, 3):

        try:
            response = requests.post(
                f"{IGNAV_BASE}/fares/booking-links",
                headers=headers,
                json={
                    "ignav_id": ignav_id
                },
                timeout=60,
            )

            print(
                f"Booking links: "
                f"HTTP {response.status_code}"
            )

            if response.status_code == 200:
                try:
                    return response.json()
                except Exception as exc:
                    print(
                        "Booking JSON hatasi:",
                        repr(exc)
                    )
                    return None

            if response.status_code not in TRANSIENT_STATUS:
                return None

        except (
            requests.Timeout,
            requests.ConnectionError
        ) as exc:

            print(
                f"Booking baglanti hatasi "
                f"({attempt}/2):",
                repr(exc)
            )

        except Exception as exc:
            print(
                "Booking beklenmeyen hata:",
                repr(exc)
            )
            return None

        if attempt < 2:
            time.sleep(attempt)

    return None


# ============================================================
# UÇUŞ KİMLİĞİ
# ============================================================

def segment_signature(segment):
    if not isinstance(segment, dict):
        return ""

    return "|".join(
        normalize_text(
            segment.get(key)
        )
        for key in (
            "departure_airport",
            "arrival_airport",
            "marketing_carrier_code",
            "flight_number",
            "departure_time",
            "arrival_time",
        )
    )


def segment_path_signature(segments):
    result = []

    for segment in segments or []:

        if not isinstance(segment, dict):
            continue

        departure = normalize_text(
            segment.get(
                "departure_airport"
            )
        )

        arrival = normalize_text(
            segment.get(
                "arrival_airport"
            )
        )

        carrier = normalize_text(
            segment.get(
                "marketing_carrier_code"
            )
            or segment.get(
                "carrier_code"
            )
        )

        number = normalize_text(
            segment.get(
                "flight_number"
            )
        )

        if (
            departure
            or arrival
            or carrier
            or number
        ):
            result.append(
                f"{departure}>{arrival}:"
                f"{carrier}{number}"
            )

    return "|".join(result)


def make_identity(
    origin,
    destination,
    departure_date,
    airline,
    flight_number,
    path,
):
    return "~".join(
        [
            normalize_text(origin),
            normalize_text(destination),
            normalize_text(departure_date),
            normalize_text(airline),
            normalize_text(flight_number),
            normalize_text(path),
        ]
    )


def create_flight_key(
    origin,
    destination,
    flight_number,
    airline,
    duration,
    stops,
    segments=None,
):
    signatures = [
        segment_signature(segment)
        for segment in (segments or [])
        if segment_signature(segment)
    ]

    return "-".join(
        [
            normalize_text(origin),
            normalize_text(destination),
            normalize_text(airline),
            normalize_text(flight_number),
            str(duration),
            str(stops),
            "||".join(signatures),
        ]
    )


# ============================================================
# VERİ ÇIKARMA
# ============================================================

def baggage_to_text(value):
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    return json.dumps(
        value,
        ensure_ascii=False
    )


def extract_money(value):
    number = safe_float(value)

    if number is not None:
        return number

    if isinstance(value, dict):

        for key in (
            "amount",
            "value",
            "total",
            "price",
        ):
            number = safe_float(
                value.get(key)
            )

            if number is not None:
                return number

        for key in (
            "items",
            "components",
            "details",
            "breakdown",
        ):
            if key in value:
                number = extract_money(
                    value[key]
                )

                if number is not None:
                    return number

    if isinstance(value, list):

        values = [
            extract_money(item)
            for item in value
        ]

        values = [
            value
            for value in values
            if value is not None
        ]

        if values:
            return sum(values)

    return None


def recursive_component(value, names):
    if isinstance(value, dict):

        label = str(
            value.get("type")
            or value.get("category")
            or value.get("name")
            or value.get("label")
            or value.get("description")
            or ""
        ).lower()

        if any(
            name in label
            for name in names
        ):
            number = extract_money(value)

            if number is not None:
                return number

        for key, child in value.items():

            key_lower = str(key).lower()

            if key_lower in names:
                number = extract_money(child)

                if number is not None:
                    return number

            number = recursive_component(
                child,
                names
            )

            if number is not None:
                return number

    elif isinstance(value, list):

        for child in value:

            number = recursive_component(
                child,
                names
            )

            if number is not None:
                return number

    return None


def price_breakdown(
    itinerary,
    price_info,
    total,
):
    base = recursive_component(
        price_info,
        {
            "base_fare",
            "basefare",
            "base fare",
            "fare",
        },
    )

    taxes = recursive_component(
        price_info,
        {
            "tax",
            "taxes",
            "taxes_and_fees",
            "taxesandfees",
        },
    )

    fees = recursive_component(
        price_info,
        {
            "fee",
            "fees",
            "mandatory_fee",
            "mandatory fees",
        },
    )

    values = (
        base,
        taxes,
        fees,
    )

    if any(
        value is not None and value < 0
        for value in values
    ):
        return None, None, None, None

    known = [
        value
        for value in values
        if value is not None
    ]

    if (
        total is not None
        and known
        and sum(known) > total * 1.03
    ):
        return None, None, None, None

    if (
        base is not None
        and taxes is not None
        and fees is None
        and total is not None
    ):
        fees = total - base - taxes

    elif (
        base is not None
        and fees is not None
        and taxes is None
        and total is not None
    ):
        taxes = total - base - fees

    elif (
        base is None
        and taxes is not None
        and fees is not None
        and total is not None
    ):
        base = total - taxes - fees

    if any(
        value is not None and value < 0
        for value in (
            base,
            taxes,
            fees,
        )
    ):
        return None, None, None, None

    ratio = None

    if (
        total
        and taxes is not None
        and fees is not None
    ):
        ratio = (
            taxes + fees
        ) / total

    elif (
        total
        and taxes is not None
    ):
        ratio = taxes / total

    return (
        base,
        taxes,
        fees,
        ratio,
    )


def extract_flights(
    data,
    origin,
    destination,
    departure_date,
):
    if not isinstance(data, dict):
        return []

    raw = (
        data.get("itineraries")
        or data.get("results")
        or data.get("fares")
        or []
    )

    if isinstance(raw, dict):

        raw = (
            raw.get("itineraries")
            or raw.get("results")
            or list(raw.values())
        )

    flights = []

    if not isinstance(raw, list):
        return flights

    for itinerary in raw:

        if not isinstance(itinerary, dict):
            continue

        price_info = (
            itinerary.get("price")
            or itinerary.get("total_price")
            or {}
        )

        price = extract_money(
            price_info
        )

        if (
            price is None
            or price <= 0
        ):
            continue

        if isinstance(price_info, dict):
            currency = normalize_text(
                price_info.get(
                    "currency"
                )
                or itinerary.get(
                    "currency"
                )
                or settings
                .get("system", {})
                .get(
                    "currency",
                    "TRY"
                )
            )
        else:
            currency = normalize_text(
                itinerary.get(
                    "currency"
                )
                or settings
                .get("system", {})
                .get(
                    "currency",
                    "TRY"
                )
            )

        outbound = (
            itinerary.get("outbound")
            or itinerary.get(
                "outbound_itinerary"
            )
            or {}
        )

        segments = (
            outbound.get("segments")
            if isinstance(
                outbound,
                dict
            )
            else None
        )

        if not isinstance(
            segments,
            list
        ):
            segments = []

        first = (
            segments[0]
            if segments
            else {}
        )

        last = (
            segments[-1]
            if segments
            else first
        )

        airline = normalize_text(
            first.get(
                "marketing_carrier_code"
            )
            or first.get(
                "carrier_code"
            )
            or itinerary.get(
                "airline"
            )
        )

        flight_number = normalize_text(
            first.get(
                "flight_number"
            )
            or itinerary.get(
                "flight_number"
            )
        )

        duration_raw = itinerary.get(
            "duration_minutes"
        )

        if (
            duration_raw is None
            and isinstance(
                outbound,
                dict
            )
        ):
            duration_raw = outbound.get(
                "duration_minutes"
            )

        duration = int(
            safe_float(
                duration_raw
            ) or 0
        )

        stops = max(
            0,
            len(segments) - 1
        )

        self_transfer = bool(
            itinerary.get(
                "requires_self_transfer"
            )
            or itinerary.get(
                "self_transfer"
            )
            or False
        )

        baggage = (
            itinerary.get("bags")
            or itinerary.get("baggage")
            or first.get("bags")
            or first.get("baggage")
        )

        path = segment_path_signature(
            segments
        )

        identity = make_identity(
            origin,
            destination,
            departure_date,
            airline,
            flight_number,
            path,
        )

        flight_key = create_flight_key(
            origin,
            destination,
            flight_number,
            airline,
            duration,
            stops,
            segments,
        )

        base, taxes, fees, ratio = (
            price_breakdown(
                itinerary,
                price_info,
                price,
            )
        )

        flights.append(
            {
                "flight_key": flight_key,
                "flight_identity": identity,
                "origin": origin,
                "destination": destination,
                "departure_date": departure_date,
                "price": price,
                "currency": currency,
                "airline": airline,
                "flight_number": flight_number,
                "duration_minutes": duration,
                "baggage": baggage_to_text(
                    baggage
                ),
                "self_transfer": self_transfer,
                "price_status": "",
                "ignav_id": (
                    itinerary.get(
                        "ignav_id"
                    )
                    or itinerary.get("id")
                ),
                "stops": stops,
                "seen_at": utc_iso(),
                "recorded_at": utc_iso(),
                "base_fare": base,
                "taxes": taxes,
                "fees": fees,
                "tax_ratio": ratio,
                "segments": segments,
                "last_arrival": last.get(
                    "arrival_time"
                ),
            }
        )

    return flights


# ============================================================
# DEDUP
# ============================================================

def canonical_display_key(flight):
    return (
        normalize_text(
            flight.get("origin")
        ),
        normalize_text(
            flight.get("destination")
        ),
        normalize_text(
            flight.get("departure_date")
        ),
        normalize_text(
            flight.get("airline")
        ),
        normalize_text(
            flight.get("flight_number")
        ),
        round(
            float(
                flight.get("price") or 0
            ),
            2,
        ),
        normalize_text(
            flight.get("currency")
        ),
        bool(
            flight.get(
                "self_transfer"
            )
        ),
        int(
            flight.get("stops") or 0
        ),
        "||".join(
            segment_signature(
                segment
            )
            for segment in flight.get(
                "segments",
                []
            )
            if segment_signature(
                segment
            )
        ),
    )


def dedup_flights(flights):
    seen = set()
    output = []

    for flight in flights:

        if flight.get("price") is None:
            continue

        key = canonical_display_key(
            flight
        )

        if key in seen:
            continue

        seen.add(key)
        output.append(flight)

    removed = len(flights) - len(output)

    return output, removed


# ============================================================
# FİYAT KAYDI
# ============================================================

def save_flight(flight):
    conn = get_connection()

    now = utc_iso()

    conn.execute(
        """
        INSERT INTO prices(
            flight_key,
            flight_identity,
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
            seen_at,
            recorded_at,
            base_fare,
            taxes,
            fees,
            tax_ratio
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            flight["flight_key"],
            flight["flight_identity"],
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
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
            now,
            now,
            flight["base_fare"],
            flight["taxes"],
            flight["fees"],
            flight["tax_ratio"],
        ),
    )

    recent_cutoff = (
        utc_now()
        - timedelta(hours=6)
    ).isoformat()

    recent = conn.execute(
        """
        SELECT 1
        FROM price_observations
        WHERE flight_identity=?
          AND departure_date=?
          AND currency=?
          AND ABS(price-?)<0.01
          AND observed_at>=?
        LIMIT 1
        """,
        (
            flight["flight_identity"],
            flight["departure_date"],
            flight["currency"],
            flight["price"],
            recent_cutoff,
        ),
    ).fetchone()

    if not recent:

        conn.execute(
            """
            INSERT INTO price_observations(
                flight_key,
                flight_identity,
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
                observed_at,
                base_fare,
                taxes,
                fees,
                tax_ratio
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?,?
            )
            """,
            (
                flight["flight_key"],
                flight["flight_identity"],
                flight["origin"],
                flight["destination"],
                flight["departure_date"],
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
                now,
                flight["base_fare"],
                flight["taxes"],
                flight["fees"],
                flight["tax_ratio"],
            ),
        )

    conn.commit()
    conn.close()


# ============================================================
# GEÇMİŞ
# ============================================================

def history_cutoff():
    return (
        RUN_STARTED_AT
        or utc_iso()
    )


def get_previous_price(flight):
    conn = get_connection()

    row = conn.execute(
        """
        SELECT price
        FROM price_observations
        WHERE flight_identity=?
          AND departure_date=?
          AND currency=?
          AND observed_at<?
        ORDER BY observed_at DESC, id DESC
        LIMIT 1
        """,
        (
            flight["flight_identity"],
            flight["departure_date"],
            flight["currency"],
            history_cutoff(),
        ),
    ).fetchone()

    conn.close()

    if row:
        return float(row[0])

    return None


def latest_per_identity(rows):
    latest = {}

    for identity, price, observed_at in rows:

        if not identity:
            continue

        old = latest.get(identity)

        if (
            old is None
            or observed_at > old[1]
        ):
            latest[identity] = (
                float(price),
                observed_at,
            )

    return [
        value[0]
        for value in latest.values()
        if value[0] > 0
    ]


def stable_flight_history(flight):
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            flight_identity,
            price,
            observed_at
        FROM price_observations
        WHERE flight_identity=?
          AND currency=?
          AND observed_at<?
          AND price>0
        ORDER BY observed_at DESC
        LIMIT 100
        """,
        (
            flight["flight_identity"],
            flight["currency"],
            history_cutoff(),
        ),
    ).fetchall()

    conn.close()

    return [
        float(row[1])
        for row in rows
        if row[1] > 0
    ]


def airline_flight_history(flight):
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            flight_identity,
            price,
            observed_at
        FROM price_observations
        WHERE origin=?
          AND destination=?
          AND departure_date=?
          AND currency=?
          AND airline=?
          AND flight_number=?
          AND observed_at<?
          AND price>0
        ORDER BY observed_at DESC
        LIMIT 300
        """,
        (
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["currency"],
            flight["airline"],
            flight["flight_number"],
            history_cutoff(),
        ),
    ).fetchall()

    conn.close()

    return latest_per_identity(
        rows
    )


def route_market_history(flight):
    conn = get_connection()

    rows = conn.execute(
        """
        SELECT
            flight_identity,
            price,
            observed_at
        FROM price_observations
        WHERE origin=?
          AND destination=?
          AND departure_date=?
          AND currency=?
          AND observed_at<?
          AND price>0
        ORDER BY observed_at DESC
        LIMIT 1000
        """,
        (
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["currency"],
            history_cutoff(),
        ),
    ).fetchall()

    conn.close()

    return latest_per_identity(
        rows
    )


def normal_price(flight):
    """
    Normal fiyat belirleme sırası:

    1. Aynı stabil uçuş
    2. Aynı havayolu + uçuş numarası
    3. Aynı rota + tarih piyasası

    Güncel çalışmanın fiyatı geçmişe dahil edilmez.
    """

    exact = stable_flight_history(
        flight
    )

    if len(exact) >= 2:
        return (
            statistics.median(exact),
            len(exact),
            "exact-flight",
        )

    same_number = airline_flight_history(
        flight
    )

    if len(same_number) >= 2:
        return (
            statistics.median(
                same_number
            ),
            len(same_number),
            "airline-flight",
        )

    market = route_market_history(
        flight
    )

    if len(market) >= 3:
        return (
            statistics.median(market),
            len(market),
            "route-market",
        )

    return (
        None,
        max(
            len(exact),
            len(same_number),
            len(market),
        ),
        "insufficient",
    )


# ============================================================
# ÖN SKOR
# ============================================================

def score_preliminary(
    flight,
    previous_price,
    normal_info,
):
    normal, samples, normal_source = normal_info

    score = 0

    market_drop = 0.0
    sudden_drop = 0.0

    # --------------------------------------------------------
    # Önceki çalışmaya göre düşüş
    # --------------------------------------------------------

    if (
        previous_price
        and previous_price > 0
    ):
        sudden_drop = (
            (
                previous_price
                - flight["price"]
            )
            / previous_price
            * 100
        )

        sudden_drop = max(
            0,
            sudden_drop
        )

        if sudden_drop >= 60:
            score += 18
        elif sudden_drop >= 50:
            score += 15
        elif sudden_drop >= 40:
            score += 12
        elif sudden_drop >= 30:
            score += 9
        elif sudden_drop >= 20:
            score += 6
        elif sudden_drop >= 10:
            score += 3
        elif sudden_drop > 0:
            score += 1

    # --------------------------------------------------------
    # Normal fiyata göre piyasa düşüşü
    # --------------------------------------------------------

    if normal and normal > 0:

        market_drop = (
            (
                normal
                - flight["price"]
            )
            / normal
            * 100
        )

        market_drop = max(
            0,
            market_drop
        )

        if market_drop >= 70:
            score += 28
        elif market_drop >= 60:
            score += 25
        elif market_drop >= 50:
            score += 21
        elif market_drop >= 40:
            score += 17
        elif market_drop >= 30:
            score += 13
        elif market_drop >= 20:
            score += 9
        elif market_drop >= 10:
            score += 5
        elif market_drop > 0:
            score += 2

    # --------------------------------------------------------
    # Ek güven sinyalleri
    # --------------------------------------------------------

    if (
        flight.get("tax_ratio")
        is not None
        and flight["tax_ratio"] > 0.35
    ):
        score += 4

    if (
        flight.get("currency")
        == settings
        .get("system", {})
        .get("currency", "TRY")
    ):
        score += 2

    if (
        not flight.get(
            "self_transfer"
        )
        and int(
            flight.get("stops") or 0
        ) <= 1
    ):
        score += 2

    if (
        flight.get("price", 0) > 0
        and flight.get("price", 0) < 1500
    ):
        score += 2

    return (
        min(78, score),
        market_drop,
        sudden_drop,
        samples,
        normal_source,
    )


# ============================================================
# URL / BOOKING VERİSİ
# ============================================================

def recursive_urls(value):
    output = []

    if isinstance(value, dict):

        for key, child in value.items():

            if (
                isinstance(child, str)
                and child.startswith(
                    (
                        "http://",
                        "https://",
                    )
                )
            ):
                host = (
                    urlparse(child)
                    .netloc
                    .lower()
                )

                if host and host not in {
                    "ignav.com",
                    "www.ignav.com",
                }:
                    output.append(child)

            else:
                output.extend(
                    recursive_urls(child)
                )

    elif isinstance(value, list):

        for child in value:
            output.extend(
                recursive_urls(child)
            )

    return list(
        dict.fromkeys(output)
    )


def recursive_booking_prices(
    value,
    inherited_currency="",
):
    """
    Booking yanıtındaki muhtemel toplam fiyatları
    recursive şekilde toplar.
    """

    values = []

    if isinstance(value, dict):

        currency = normalize_text(
            value.get("currency")
            or value.get(
                "currency_code"
            )
            or inherited_currency
        )

        priority_keys = (
            "total_price",
            "total",
            "final_price",
            "booking_price",
            "price",
            "fare_total",
            "amount",
        )

        for key in priority_keys:

            if key in value:

                number = safe_float(
                    value.get(key)
                )

                if (
                    number is not None
                    and number > 0
                ):
                    values.append(
                        (
                            number,
                            currency,
                            key.lower(),
                        )
                    )

        for key, child in value.items():

            key_lower = str(
                key
            ).lower()

            if isinstance(
                child,
                (dict, list)
            ):
                values.extend(
                    recursive_booking_prices(
                        child,
                        currency,
                    )
                )

            elif key_lower in priority_keys:

                number = safe_float(
                    child
                )

                if (
                    number is not None
                    and number > 0
                ):
                    values.append(
                        (
                            number,
                            currency,
                            key_lower,
                        )
                    )

    elif isinstance(value, list):

        for child in value:
            values.extend(
                recursive_booking_prices(
                    child,
                    inherited_currency,
                )
            )

    return list(
        dict.fromkeys(values)
    )


# ============================================================
# BOOKING DOĞRULAMA
# ============================================================

def booking_analysis(flight):
    data = get_booking_links(
        flight.get("ignav_id")
    )

    if not data:
        return {
            "checked": False,
            "verified": False,
            "points": 0,
            "urls": [],
            "reason": "booking verisi yok",
        }

    target = float(
        flight["price"]
    )

    currency = normalize_text(
        flight["currency"]
    )

    candidates = []

    for number, cur, key in (
        recursive_booking_prices(data)
    ):

        if (
            not cur
            or cur == currency
        ):
            candidates.append(
                (
                    number,
                    cur,
                    key,
                )
            )

    near = []

    for number, cur, key in candidates:

        if (
            cur
            and cur != currency
        ):
            continue

        difference = (
            abs(
                number - target
            )
            / target
            if target
            else 999
        )

        absolute_difference = abs(
            number - target
        )

        if (
            difference <= 0.03
            or absolute_difference <= 100
        ):
            near.append(
                (
                    difference,
                    number,
                    key,
                )
            )

    near.sort()

    urls = recursive_urls(
        data
    )

    verified = bool(
        near
        and urls
    )

    if verified:
        points = 25
    elif candidates:
        points = 3
    else:
        points = 0

    return {
        "checked": True,
        "verified": verified,
        "points": points,
        "urls": urls[:3],
        "near_price": (
            near[0][1]
            if near
            else None
        ),
        "provider_prices": [
            number
            for number, _, _ in candidates[:10]
        ],
        "reason": (
            "ayni para birimi + yakin toplam + "
            "rezervasyon linki"
            if verified
            else "yakin toplam/link teyidi yok"
        ),
    }


# ============================================================
# İKİNCİ IGNAV DOĞRULAMASI
# ============================================================

def verify_flight(flight):
    """
    Aynı aramayı ikinci kez yapar.

    Doğrulama sırası:
    1. Aynı stabil uçuş kimliği + fiyat
    2. Aynı havayolu + uçuş numarası + fiyat
    3. Aynı rota + çok yakın zaman/fiyat kombinasyonu

    Amaç yalnızca "aynı fiyata başka bir uçuş" bulmak değil,
    gerçekten aynı ucuz uçuşun hâlâ görülebilir olduğunu
    doğrulamaktır.
    """

    data = ignav_search(
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
    )

    if not data:
        return {
            "verified": False,
            "reason": "ikinci IGNAV araması sonucu yok",
        }

    candidates = extract_flights(
        data,
        flight["origin"],
        flight["destination"],
        flight["departure_date"],
    )

    candidates, _ = dedup_flights(
        candidates
    )

    target_currency = normalize_text(
        flight["currency"]
    )

    same_currency = [
        candidate
        for candidate in candidates
        if normalize_text(
            candidate["currency"]
        ) == target_currency
    ]

    target_price = float(
        flight["price"]
    )

    # Normal tolerans:
    # %2 veya minimum 50 TL
    tolerance = max(
        50,
        target_price * 0.02
    )

    # --------------------------------------------------------
    # 1. Stabil uçuş kimliği
    # --------------------------------------------------------

    exact_matches = [
        candidate
        for candidate in same_currency
        if (
            candidate.get(
                "flight_identity"
            )
            == flight.get(
                "flight_identity"
            )
            and abs(
                float(
                    candidate["price"]
                )
                - target_price
            ) <= tolerance
        )
    ]

    if exact_matches:
        best = min(
            exact_matches,
            key=lambda item: abs(
                float(item["price"])
                - target_price
            ),
        )

        return {
            "verified": True,
            "reason": (
                "ayni stabil ucus kimligi "
                "+ yakin fiyat"
            ),
            "matched_price": best["price"],
        }

    # --------------------------------------------------------
    # 2. Aynı havayolu + uçuş numarası
    # --------------------------------------------------------

    same_flight_number = []

    for candidate in same_currency:

        if (
            normalize_text(
                candidate["airline"]
            )
            != normalize_text(
                flight["airline"]
            )
        ):
            continue

        if (
            normalize_text(
                candidate["flight_number"]
            )
            != normalize_text(
                flight["flight_number"]
            )
        ):
            continue

        difference = (
            abs(
                float(candidate["price"])
                - target_price
            )
            / max(
                target_price,
                1
            )
        )

        same_flight_number.append(
            (
                difference,
                candidate,
            )
        )

    same_flight_number.sort(
        key=lambda item: item[0]
    )

    if (
        same_flight_number
        and same_flight_number[0][0]
        <= 0.03
    ):
        candidate = (
            same_flight_number[0][1]
        )

        return {
            "verified": True,
            "reason": (
                "ayni havayolu/ucus numarasi "
                "+ yakin fiyat"
            ),
            "matched_price": candidate[
                "price"
            ],
        }

    # --------------------------------------------------------
    # 3. Son güvenlik kontrolü
    # --------------------------------------------------------

    route_candidates = []

    for candidate in same_currency:

        if (
            normalize_text(
                candidate["origin"]
            )
            != normalize_text(
                flight["origin"]
            )
        ):
            continue

        if (
            normalize_text(
                candidate["destination"]
            )
            != normalize_text(
                flight["destination"]
            )
        ):
            continue

        difference = (
            abs(
                float(candidate["price"])
                - target_price
            )
            / max(
                target_price,
                1
            )
        )

        # Bu aşama daha sıkı.
        # Havayolu veya uçuş numarası bilinmiyorsa
        # yalnızca çok yakın fiyatı tek başına
        # "doğrulandı" saymıyoruz.
        if difference <= 0.01:
            route_candidates.append(
                (
                    difference,
                    candidate,
                )
            )

    if route_candidates:
        route_candidates.sort(
            key=lambda item: item[0]
        )

        candidate = (
            route_candidates[0][1]
        )

        if (
            candidate.get(
                "stops"
            )
            == flight.get(
                "stops"
            )
            and bool(
                candidate.get(
                    "self_transfer"
                )
            )
            == bool(
                flight.get(
                    "self_transfer"
                )
            )
        ):
            return {
                "verified": True,
                "reason": (
                    "ayni rota + cok yakin fiyat "
                    "+ ayni aktarma yapisi"
                ),
                "matched_price": candidate[
                    "price"
                ],
            }

    return {
        "verified": False,
        "reason": (
            "ikinci aramada yeterli "
            "ucus eslesmesi yok"
        ),
    }


# ============================================================
# SKOR
# ============================================================

def final_score(
    preliminary_score,
    verification,
    booking,
    flight,
    market_drop=0,
    previous_drop=0,
):
    score = preliminary_score

    verified = bool(
        verification.get(
            "verified"
        )
    )

    booking_verified = bool(
        booking.get(
            "verified"
        )
    )

    # --------------------------------------------------------
    # İkinci kaynak doğrulaması
    # --------------------------------------------------------

    if verified:
        score += 15

    # --------------------------------------------------------
    # Booking doğrulaması
    # --------------------------------------------------------

    if booking_verified:
        score += 25

    elif booking.get(
        "checked"
    ):
        score += int(
            booking.get(
                "points",
                0
            )
        )

    # --------------------------------------------------------
    # Çift doğrulama bonusu
    # --------------------------------------------------------

    if (
        verified
        and booking_verified
    ):
        score += 10

    # --------------------------------------------------------
    # Çok büyük piyasa farkı
    # --------------------------------------------------------

    if market_drop >= 60:
        score += 15

    elif market_drop >= 50:
        score += 10

    elif market_drop >= 40:
        score += 7

    elif market_drop >= 30:
        score += 4

    # --------------------------------------------------------
    # Önceki fiyata sert düşüş
    # --------------------------------------------------------

    if previous_drop >= 50:
        score += 5

    elif previous_drop >= 30:
        score += 3

    # --------------------------------------------------------
    # Risk cezaları
    # --------------------------------------------------------

    if flight.get(
        "self_transfer"
    ):
        score -= 8

    if int(
        flight.get("stops") or 0
    ) > 1:
        score -= 3

    return max(
        0,
        min(
            100,
            score
        )
    )


# ============================================================
# KALİTE KAPISI
# ============================================================

def quality_gate(
    flight,
    score,
    verified,
    booking,
    market_drop,
    previous_drop,
    history_samples,
):
    """
    Telegram'a gidecek aday için son kalite filtresi.

    Temel kural:
    - ikinci arama doğrulaması şart
    - geçmiş verisi şart
    - %30 civarı düşüş ancak booking de doğruluyorsa geçer
    - %50+ çok güçlü düşüşte booking olmasa bile ikinci
      IGNAV doğrulaması yeterli olabilir
    - self-transfer için daha sıkı davranılır
    """

    if not verified:
        return False

    if history_samples < 2:
        return False

    if flight.get(
        "self_transfer"
    ):
        return bool(
            booking.get("verified")
            and market_drop >= 60
            and score >= 75
        )

    # --------------------------------------------------------
    # En güçlü senaryo:
    # ikinci IGNAV + booking doğrulaması
    # --------------------------------------------------------

    if (
        booking.get("verified")
        and market_drop >= 28
        and score >= 60
    ):
        return True

    # --------------------------------------------------------
    # Çok büyük piyasa anomalisi:
    # ikinci IGNAV doğrulaması yeterli olabilir.
    # --------------------------------------------------------

    if (
        market_drop >= 50
        and score >= 60
    ):
        return True

    if (
        market_drop >= 60
        and score >= 55
    ):
        return True

    # --------------------------------------------------------
    # Hem önceki fiyat hem piyasa fiyatı sert düşmüşse
    # --------------------------------------------------------

    if (
        market_drop >= 40
        and previous_drop >= 30
        and score >= 65
    ):
        return True

    return False


# ============================================================
# TELEGRAM
# ============================================================

def alert_level(score):
    if score >= 90:
        return "HIGH_CONFIDENCE_ERROR_FARE"

    if score >= 85:
        return "ERROR_FARE_CANDIDATE"

    if score >= 70:
        return "VERIFIED_CANDIDATE"

    if score >= 60:
        return "STRONG_CANDIDATE"

    return "BELOW_ALERT"


def telegram_configured():
    return bool(
        os.getenv(
            "TELEGRAM_BOT_TOKEN"
        )
        and os.getenv(
            "TELEGRAM_CHAT_ID"
        )
    )


def telegram_send(text):
    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        print(
            "Telegram: "
            "TELEGRAM_BOT_TOKEN / "
            "TELEGRAM_CHAT_ID eksik."
        )
        return False

    for attempt in range(1, 3):

        try:

            response = requests.post(
                f"https://api.telegram.org/"
                f"bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text[:4090],
                    "disable_web_page_preview": False,
                },
                timeout=30,
            )

            if response.status_code == 200:
                print(
                    "Telegram: "
                    "mesaj gonderildi."
                )
                return True

            if (
                response.status_code
                not in TRANSIENT_STATUS
            ):
                print(
                    "Telegram HTTP hata:",
                    response.status_code,
                    response.text[:500],
                )
                return False

            print(
                f"Telegram gecici hata "
                f"({attempt}/2):",
                response.text[:500],
            )

        except (
            requests.Timeout,
            requests.ConnectionError
        ) as exc:

            print(
                f"Telegram baglanti hatasi "
                f"({attempt}/2):",
                repr(exc),
            )

        except Exception as exc:

            print(
                "Telegram beklenmeyen hata:",
                repr(exc),
            )
            return False

        if attempt < 2:
            time.sleep(attempt)

    return False


def telegram_test():
    if not telegram_configured():
        print(
            "Telegram test: "
            "secret eksik, test yapilmadi."
        )
        return False

    return telegram_send(
        "✅ Uçuş Hata Fiyatı Radarı V4 "
        "bağlantı testi başarılı.\n"
        "Telegram bildirim kanalı aktif."
    )


# ============================================================
# ALERT KONTROLÜ
# ============================================================

def alert_allowed(
    flight,
    score,
    quality,
    verification,
    booking,
    market_drop,
):
    alerts = settings.get(
        "alerts",
        {}
    )

    if not alerts.get(
        "enabled",
        True
    ):
        return False

    if not alerts.get(
        "telegram_enabled",
        True
    ):
        return False

    if not quality:
        return False

    configured_score = int(
        alerts.get(
            "candidate_score",
            85
        )
    )

    # Normal skor sistemi.
    if score >= configured_score:
        return True

    # Çok önemli düzeltme:
    # İkinci IGNAV + booking birlikte doğrulanmışsa
    # 85 puanlık eski kilit gerçek fırsatları engellemez.
    confirmed_candidate = (
        verification.get(
            "verified"
        )
        and booking.get(
            "verified"
        )
        and market_drop >= 28
        and score >= 60
    )

    return bool(
        confirmed_candidate
    )


def already_alerted(flight):
    hours = float(
        settings
        .get("radar", {})
        .get(
            "duplicate_alert_window_hours",
            24
        )
    )

    conn = get_connection()

    cutoff = (
        utc_now()
        - timedelta(hours=hours)
    ).isoformat()

    row = conn.execute(
        """
        SELECT 1
        FROM alerts
        WHERE origin=?
          AND destination=?
          AND departure_date=?
          AND flight_key=?
          AND sent_at>=?
        LIMIT 1
        """,
        (
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["flight_key"],
            cutoff,
        ),
    ).fetchone()

    conn.close()

    return bool(row)


def save_alert(
    flight,
    score,
    level,
):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO alerts(
            flight_key,
            origin,
            destination,
            departure_date,
            price,
            currency,
            airline,
            flight_number,
            score,
            alert_level,
            sent_at
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            flight["flight_key"],
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
            flight["price"],
            flight["currency"],
            flight["airline"],
            flight["flight_number"],
            score,
            level,
            utc_iso(),
        ),
    )

    conn.commit()
    conn.close()


def send_alert(
    flight,
    score,
    normal,
    normal_source,
    previous_price,
    booking,
    samples,
    market_drop,
    previous_drop,
    verification,
):
    level = alert_level(
        score
    )

    lines = [
        "🚨 UÇUŞ HATA FİYATI RADARI",
        "",
        f"{flight['origin']} → "
        f"{flight['destination']}",
        f"Tarih: "
        f"{flight['departure_date']}",
        f"Uçuş: "
        f"{flight['airline']} "
        f"{flight['flight_number']}",
        "",
        f"🔥 Fiyat: "
        f"{flight['price']:.0f} "
        f"{flight['currency']}",
        f"📊 Skor: "
        f"{score:.0f} / 100",
        f"🎯 Seviye: {level}",
        "",
        f"📈 Piyasa düşüşü: "
        f"%{market_drop:.1f}",
        f"📉 Önceki fiyata düşüş: "
        f"%{previous_drop:.1f}",
        "",
        f"🔎 IGNAV doğrulama: "
        f"EVET",
        f"   {verification.get('reason', '')}",
        "",
        f"🗃 Geçmiş örnek: "
        f"{samples}",
        f"📌 Normal kaynak: "
        f"{normal_source}",
    ]

    if normal:
        lines.append(
            f"Normal medyan: "
            f"{normal:.0f} "
            f"{flight['currency']}"
        )

    if previous_price:
        lines.append(
            f"Önceki fiyat: "
            f"{previous_price:.0f} "
            f"{flight['currency']}"
        )

    if booking.get(
        "verified"
    ):
        lines.append(
            f"✅ Booking teyidi: "
            f"{booking.get('near_price', 0):.0f} "
            f"{flight['currency']}"
        )

    elif booking.get(
        "checked"
    ):
        lines.append(
            "⚠️ Booking kontrol edildi; "
            "fiyat/link tam teyit edilemedi."
        )

    if booking.get(
        "urls"
    ):
        lines.append("")
        lines.append(
            "🔗 Rezervasyon bağlantıları:"
        )

        for index, url in enumerate(
            booking["urls"],
            start=1
        ):
            lines.append(
                f"{index}. {url}"
            )

    return telegram_send(
        "\n".join(lines)
    )


# ============================================================
# ROTALAR
# ============================================================

def build_routes():
    airports = settings.get(
        "airports",
        {}
    )

    origins = airports.get(
        "priority_origins",
        []
    )

    domestic = (
        airports.get(
            "domestic_destinations",
            []
        )
        if airports.get(
            "domestic_enabled",
            True
        )
        else []
    )

    europe = (
        airports.get(
            "europe_destinations",
            []
        )
        if airports.get(
            "europe_enabled",
            True
        )
        else []
    )

    destinations = []

    for destination in (
        domestic + europe
    ):

        if (
            destination
            not in BLOCKED_DESTINATIONS
            and destination
            not in destinations
        ):
            destinations.append(
                destination
            )

    return [
        (origin, destination)
        for origin in origins
        for destination in destinations
        if origin != destination
    ]


def rotated_routes(
    routes,
    count,
):
    if not routes:
        return []

    conn = get_connection()

    row = conn.execute(
        """
        SELECT route_index
        FROM radar_state
        WHERE id=1
        """
    ).fetchone()

    index = (
        int(row[0])
        if row
        else 0
    )

    selected = [
        routes[
            (index + offset)
            % len(routes)
        ]
        for offset in range(
            min(
                count,
                len(routes)
            )
        )
    ]

    new_index = (
        index + len(selected)
    ) % len(routes)

    conn.execute(
        """
        UPDATE radar_state
        SET route_index=?,
            updated_at=?
        WHERE id=1
        """,
        (
            new_index,
            utc_iso(),
        ),
    )

    conn.commit()
    conn.close()

    return selected


def target_dates():
    offsets = (
        settings
        .get("radar", {})
        .get(
            "dates_days_ahead",
            [30, 60, 90]
        )
    )

    base_date = (
        datetime.now(
            timezone.utc
        ).date()
    )

    return [
        (
            base_date
            + timedelta(
                days=int(offset)
            )
        ).isoformat()
        for offset in offsets
    ]


# ============================================================
# TEK UÇUŞ İŞLEME
# ============================================================

def process_flight(flight):
    """
    Kritik sıra:

    1. Eski fiyatı oku
    2. Normal fiyatı hesapla
    3. Ön skor
    4. İkinci IGNAV doğrulaması
    5. Gerekirse booking doğrulaması
    6. Final skor
    7. Quality gate
    8. Telegram
    9. En son mevcut gözlemi DB'ye yaz

    Böylece mevcut çalışmanın fiyatı kendi normal fiyatını
    yapay şekilde aşağı/yukarı etkilemez.
    """

    previous_price = get_previous_price(
        flight
    )

    normal_info = normal_price(
        flight
    )

    normal, samples, normal_source = (
        normal_info
    )

    (
        preliminary,
        market_drop,
        sudden_drop,
        history_samples,
        history_source,
    ) = score_preliminary(
        flight,
        previous_price,
        normal_info,
    )

    previous_drop = 0.0

    if (
        previous_price
        and previous_price > 0
    ):
        previous_drop = max(
            0,
            (
                (
                    previous_price
                    - flight["price"]
                )
                / previous_price
                * 100
            ),
        )

    # ========================================================
    # VERIFICATION TETİKLEYİCİ
    # ========================================================

    verification_settings = (
        settings
        .get("radar", {})
        .get(
            "verification",
            {}
        )
    )

    verification_enabled = (
        verification_settings.get(
            "enabled",
            True
        )
    )

    configured_minimum = int(
        verification_settings.get(
            "minimum_score_for_verification",
            30
        )
    )

    # Eski sistemdeki kritik hata:
    # PRE < 30 ise doğrulama hiç yapılmıyordu.
    #
    # Yeni sistemde:
    # - PRE eşiği
    # VEYA
    # - piyasa düşüşü >= %25
    # VEYA
    # - önceki fiyata düşüş >= %20
    #
    # koşullarından biri yeterli.

    verification_trigger = (
        preliminary
        >= configured_minimum
        or market_drop >= 25
        or previous_drop >= 20
    )

    if (
        verification_enabled
        and verification_trigger
    ):
        verification = verify_flight(
            flight
        )
    else:
        verification = {
            "verified": False,
            "reason": (
                "verification tetiklenmedi"
            ),
        }

    # ========================================================
    # BOOKING TETİKLEYİCİ
    # ========================================================

    booking_trigger = (
        market_drop >= 25
        or previous_drop >= 20
        or preliminary >= configured_minimum
    )

    if booking_trigger:
        booking = booking_analysis(
            flight
        )
    else:
        booking = {
            "checked": False,
            "verified": False,
            "points": 0,
            "urls": [],
            "reason": "booking tetiklenmedi",
        }

    # ========================================================
    # FINAL SKOR
    # ========================================================

    score = final_score(
        preliminary,
        verification,
        booking,
        flight,
        market_drop,
        previous_drop,
    )

    # ========================================================
    # QUALITY
    # ========================================================

    quality = quality_gate(
        flight,
        score,
        verification.get(
            "verified",
            False
        ),
        booking,
        market_drop,
        previous_drop,
        history_samples,
    )

    # ========================================================
    # ALERT
    # ========================================================

    sent = False

    allowed = alert_allowed(
        flight,
        score,
        quality,
        verification,
        booking,
        market_drop,
    )

    if (
        allowed
        and not already_alerted(
            flight
        )
    ):
        sent = send_alert(
            flight,
            score,
            normal,
            normal_source,
            previous_price,
            booking,
            history_samples,
            market_drop,
            previous_drop,
            verification,
        )

        if sent:
            save_alert(
                flight,
                score,
                alert_level(score),
            )

    # ========================================================
    # DB'YE MEVCUT GÖZLEM
    # ========================================================

    save_flight(
        flight
    )

    save_verification(
        flight,
        verification,
        booking,
    )

    # ========================================================
    # LOG
    # ========================================================

    if normal:

        normal_text = (
            f"NORMAL={normal:.0f}"
            f"({normal_source},"
            f"{history_samples})"
        )

    else:

        normal_text = (
            "NORMAL=YOK"
        )

    print(
        f"  {flight['origin']}"
        f"->{flight['destination']} "
        f"{flight['departure_date']} "
        f"{flight['airline']} "
        f"{flight['flight_number']} "
        f"{flight['price']:.0f} "
        f"{flight['currency']} | "
        f"{normal_text} | "
        f"CURRENT={flight['price']:.0f} "
        f"MARKET_DROP=%"
        f"{market_drop:.1f} "
        f"PREV="
        f"{'%.0f' % previous_price if previous_price else '-'} "
        f"PREV_DROP=%"
        f"{previous_drop:.1f} "
        f"PRE={preliminary} "
        f"VERIFY="
        f"{verification.get('verified', False)} "
        f"BOOKING="
        f"{booking.get('checked', False)}/"
        f"{booking.get('verified', False)} "
        f"FINAL={score} "
        f"QUALITY="
        f"{'PASS' if quality else 'FAIL'} "
        f"SENT={sent}"
    )

    return {
        "pre": preliminary,
        "score": score,
        "verified": verification.get(
            "verified",
            False
        ),
        "booking_checked": booking.get(
            "checked",
            False
        ),
        "booking_verified": booking.get(
            "verified",
            False
        ),
        "quality": quality,
        "sent": sent,
        "market_drop": market_drop,
        "previous_drop": previous_drop,
        "history_samples": history_samples,
        "verification_triggered":
            verification_trigger,
        "booking_triggered":
            booking_trigger,
    }


# ============================================================
# VERIFICATION KAYDI
# ============================================================

def save_verification(
    flight,
    verification,
    booking,
):
    conn = get_connection()

    conn.execute(
        """
        INSERT INTO verifications(
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
            verified,
            booking_verified,
            source_disagreement_points,
            verification_reason,
            checked_at,
            verification_time
        )
        VALUES(
            ?,?,?,?,?,?,?,?,?,?,
            ?,?,?,?,?,?,?,?,?,?
        )
        """,
        (
            flight["flight_key"],
            flight["origin"],
            flight["destination"],
            flight["departure_date"],
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
            int(
                verification.get(
                    "verified",
                    False
                )
            ),
            int(
                booking.get(
                    "verified",
                    False
                )
            ),
            0,
            verification.get(
                "reason",
                ""
            ),
            utc_iso(),
            utc_iso(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# ANA ÇALIŞMA
# ============================================================

def main():
    global RUN_STARTED_AT

    RUN_STARTED_AT = utc_iso()

    print("")
    print(
        "=========================================="
    )
    print(
        " UÇUŞ HATA FİYATI RADARI V4"
    )
    print(
        "=========================================="
    )

    init_db()

    # --------------------------------------------------------
    # Telegram test
    # --------------------------------------------------------

    if os.getenv(
        "TELEGRAM_TEST",
        "0"
    ) == "1":
        telegram_test()

    # --------------------------------------------------------
    # Rotalar
    # --------------------------------------------------------

    routes = build_routes()

    routes_per_run = int(
        settings
        .get("radar", {})
        .get(
            "routes_per_run",
            10
        )
    )

    selected_routes = rotated_routes(
        routes,
        routes_per_run,
    )

    dates = target_dates()

    print(
        f"Toplam rota havuzu: "
        f"{len(routes)}"
    )

    print(
        f"Bu calismada: "
        f"{len(selected_routes)}"
    )

    print(
        f"Tarih: {dates}"
    )

    print(
        "Telegram yapilandirilmis: "
        f"{'EVET' if telegram_configured() else 'HAYIR'}"
    )

    print("")

    # --------------------------------------------------------
    # Sayaçlar
    # --------------------------------------------------------

    total_flights = 0
    dedup_removed = 0

    verification_triggered = 0
    verified_count = 0

    booking_checks = 0
    booking_verified_count = 0

    quality_pass = 0
    telegram_sent = 0

    # --------------------------------------------------------
    # Tarama
    # --------------------------------------------------------

    for origin, destination in selected_routes:

        for date in dates:

            data = ignav_search(
                origin,
                destination,
                date,
            )

            if not data:
                continue

            flights = extract_flights(
                data,
                origin,
                destination,
                date,
            )

            flights, removed = (
                dedup_flights(
                    flights
                )
            )

            dedup_removed += removed
            total_flights += len(flights)

            for flight in flights:

                try:

                    result = process_flight(
                        flight
                    )

                    verification_triggered += int(
                        result[
                            "verification_triggered"
                        ]
                    )

                    verified_count += int(
                        result[
                            "verified"
                        ]
                    )

                    booking_checks += int(
                        result[
                            "booking_checked"
                        ]
                    )

                    booking_verified_count += int(
                        result[
                            "booking_verified"
                        ]
                    )

                    quality_pass += int(
                        result[
                            "quality"
                        ]
                    )

                    telegram_sent += int(
                        result[
                            "sent"
                        ]
                    )

                except Exception as exc:

                    # Tek bir uçuşun bozuk verisi bütün
                    # workflow'u durdurmasın.
                    print(
                        "Ucus isleme hatasi:",
                        repr(exc),
                    )

    # --------------------------------------------------------
    # SONUÇ
    # --------------------------------------------------------

    print("")
    print(
        "=========================================="
    )
    print(
        " V4 DIAGNOSTIK"
    )
    print(
        "=========================================="
    )

    print(
        f"Islenen ucus: "
        f"{total_flights}"
    )

    print(
        f"Dedup edilen: "
        f"{dedup_removed}"
    )

    print(
        f"Verification tetiklenen: "
        f"{verification_triggered}"
    )

    print(
        f"Verification basarili: "
        f"{verified_count}"
    )

    print(
        f"Booking kontrolu: "
        f"{booking_checks}"
    )

    print(
        f"Booking yakin fiyat teyidi: "
        f"{booking_verified_count}"
    )

    print(
        f"Kaliteyi gecen: "
        f"{quality_pass}"
    )

    print(
        f"Telegram gonderilen: "
        f"{telegram_sent}"
    )

    print(
        "=========================================="
    )

    print(
        "Calisma tamamlandi."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
