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

    cur.execute("""
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
    """)

    cur.execute("""
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
    """)

    cur.execute("""
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
    """)

    cur.execute("""
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
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS radar_state (
            id INTEGER PRIMARY KEY,
            route_index INTEGER DEFAULT 0,
            updated_at TEXT
        )
    """)

    schemas = {
        "prices": {
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
        },
        "price_observations": {
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
            "verification_time": "TEXT"
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
            "sent_at": "TEXT"
        }
    }

    for table, fields in schemas.items():
        for name, definition in fields.items():
            add_column_if_missing(conn, table, name, definition)

    if cur.execute(
        "SELECT 1 FROM radar_state WHERE id=1"
    ).fetchone() is None:
        cur.execute(
            "INSERT INTO radar_state(id,route_index,updated_at) VALUES(1,0,?)",
            (utc_iso(),)
        )

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

    if not key:
        return None

    return {
        "X-Api-Key": key,
        "Content-Type": "application/json"
    }


def get_market():
    return settings.get("system", {}).get("market", "TR") or "TR"


def ignav_search(origin, destination, departure_date):

    if destination in BLOCKED_DESTINATIONS:
        print(
            f"SKIP: {destination} engelli/hatali hedef."
        )
        return None

    headers = ignav_headers()

    if not headers:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    p = settings.get("passengers", {})
    c = settings.get("connections", {})

    cabin = (
        "business"
        if settings.get("cabin", {}).get("business", False)
        else "economy"
    )

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": p.get("adults", 1),
        "children": p.get("children", 0),
        "infants_in_seat": 0,
        "infants_on_lap": p.get("infants", 0),
        "cabin_class": cabin,
        "max_stops": c.get("max_connections", 1),
        "allow_self_transfer": c.get("self_transfer", False),
        "market": get_market()
    }

    max_price = settings.get("price", {}).get(
        "maximum_try", 0
    )

    if max_price and max_price > 0:
        payload["max_price"] = max_price

    disabled = settings.get("airlines", {}).get(
        "disabled", []
    )

    if disabled:
        payload["airlines_exclude"] = disabled

    for attempt in range(1, 4):

        try:

            r = requests.post(
                f"{IGNAV_BASE}/fares/one-way",
                headers=headers,
                json=payload,
                timeout=60
            )

            print(
                f"Ignav: {origin}->{destination} "
                f"{departure_date} HTTP {r.status_code}"
            )

            if r.status_code == 200:

                try:
                    return r.json()

                except Exception as e:
                    print(
                        "Ignav JSON hatasi:",
                        repr(e)
                    )
                    return None

            if r.status_code not in TRANSIENT_STATUS:

                print(
                    "Ignav kalici hata, tekrar denenmeyecek:",
                    r.text[:500]
                )

                return None

            print(
                "Ignav gecici hata:",
                r.text[:500]
            )

        except (
            requests.Timeout,
            requests.ConnectionError
        ) as e:

            print(
                f"Ignav gecici baglanti hatasi "
                f"({attempt}/3):",
                repr(e)
            )

        except Exception as e:

            print(
                "Ignav beklenmeyen hata:",
                repr(e)
            )

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

        r = requests.post(
            f"{IGNAV_BASE}/fares/booking-links",
            headers=headers,
            json={"ignav_id": ignav_id},
            timeout=60
        )

        print(
            f"Booking links: HTTP {r.status_code}"
        )

        if r.status_code != 200:
            return None

        return r.json()

    except Exception as e:

        print(
            "Booking links hatasi:",
            repr(e)
        )

        return None


def segment_signature(s):

    if not isinstance(s, dict):
        return ""

    return "|".join(
        normalize_text(s.get(k))
        for k in (
            "departure_airport",
            "arrival_airport",
            "marketing_carrier_code",
            "flight_number",
            "departure_time",
            "arrival_time"
        )
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

    sigs = [
        segment_signature(s)
        for s in (segments or [])
        if segment_signature(s)
    ]

    return "-".join([
        normalize_text(origin),
        normalize_text(destination),
        normalize_text(airline),
        normalize_text(flight_number),
        str(duration),
        str(stops),
        "||".join(sigs)
    ])


def baggage_to_text(b):

    if b is None:
        return ""

    if isinstance(b, str):
        return b

    return json.dumps(
        b,
        ensure_ascii=False
    )


def extract_money(v):

    n = safe_float(v)

    if n is not None:
        return n

    if isinstance(v, dict):

        for k in (
            "amount",
            "value",
            "total",
            "price"
        ):

            n = safe_float(v.get(k))

            if n is not None:
                return n

        for k in (
            "items",
            "components",
            "details",
            "breakdown"
        ):

            if k in v:

                n = extract_money(v[k])

                if n is not None:
                    return n

    if isinstance(v, list):

        vals = [
            extract_money(x)
            for x in v
        ]

        vals = [
            x for x in vals
            if x is not None
        ]

        return sum(vals) if vals else None

    return None


def recursive_component(v, names):

    if isinstance(v, dict):

        label = str(
            v.get("type")
            or v.get("category")
            or v.get("name")
            or v.get("label")
            or v.get("description")
            or ""
        ).lower()

        if any(
            n in label
            for n in names
        ):

            n = extract_money(v)

            if n is not None:
                return n

        for x in v.values():

            n = recursive_component(
                x,
                names
            )

            if n is not None:
                return n

    elif isinstance(v, list):

        for x in v:

            n = recursive_component(
                x,
                names
            )

            if n is not None:
                return n

    return None


def price_breakdown(itinerary, total):

    base = recursive_component(
        itinerary,
        (
            "base fare",
            "base_fare",
            "fare"
        )
    )

    taxes = recursive_component(
        itinerary,
        (
            "tax",
            "taxes"
        )
    )

    fees = recursive_component(
        itinerary,
        (
            "fee",
            "fees",
            "surcharge"
        )
    )

    if (
        base is not None
        and taxes is not None
        and fees is not None
    ):

        if abs(
            (base + taxes + fees) - total
        ) > max(2, total * 0.03):

            base = None
            taxes = None
            fees = None

    tax_ratio = None

    if (
        taxes is not None
        and base is not None
        and base > 0
    ):

        tax_ratio = (
            taxes / (base + taxes)
        ) * 100

    return (
        base,
        taxes,
        fees,
        tax_ratio
    )


def extract_flights(
    data,
    origin,
    destination,
    departure_date
):

    results = []

    if not isinstance(data, dict):
        return results

    itineraries = data.get("itineraries")

    if not isinstance(itineraries, list):
        itineraries = data.get("results", [])

    if not isinstance(itineraries, list):
        return results

    for it in itineraries:

        if not isinstance(it, dict):
            continue

        price_obj = it.get("price", {})

        if isinstance(price_obj, dict):
            price = (
                safe_float(
                    price_obj.get("amount")
                )
                or safe_float(
                    price_obj.get("value")
                )
                or safe_float(
                    price_obj.get("total")
                )
            )

            currency = (
                price_obj.get("currency")
                or it.get("currency")
                or "TRY"
            )

        else:
            price = safe_float(price_obj)
            currency = (
                it.get("currency")
                or "TRY"
            )

        if price is None:
            continue

        outbound = it.get(
            "outbound",
            {}
        )

        if not isinstance(outbound, dict):
            outbound = {}

        segments = outbound.get(
            "segments",
            []
        )

        if not isinstance(segments, list):
            segments = []

        if not segments:
            segments = it.get(
                "segments",
                []
            )

        if not isinstance(segments, list):
            segments = []

        first = (
            segments[0]
            if segments
            else {}
        )

        carrier = (
            first.get(
                "marketing_carrier_code"
            )
            or first.get(
                "carrier_code"
            )
            or it.get("airline")
            or ""
        )

        flight_number = (
            first.get("flight_number")
            or it.get("flight_number")
            or ""
        )

        duration = it.get(
            "duration_minutes"
        )

        if duration is None:
            duration = outbound.get(
                "duration_minutes"
            )

        duration = (
            int(duration)
            if safe_float(duration) is not None
            else None
        )

        stops = max(
            0,
            len(segments) - 1
        )

        self_transfer = bool(
            it.get(
                "requires_self_transfer",
                outbound.get(
                    "requires_self_transfer",
                    False
                )
            )
        )

        baggage = baggage_to_text(
            it.get("bags")
            or it.get("baggage")
            or outbound.get("bags")
            or outbound.get("baggage")
        )

        ignav_id = (
            it.get("ignav_id")
            or it.get("id")
            or ""
        )

        (
            base,
            taxes,
            fees,
            tax_ratio
        ) = price_breakdown(
            it,
            price
        )

        flight_key = create_flight_key(
            origin,
            destination,
            flight_number,
            carrier,
            duration,
            stops,
            segments
        )

        results.append({
            "flight_key": flight_key,
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "price": float(price),
            "currency": normalize_text(currency),
            "airline": normalize_text(carrier),
            "flight_number": normalize_text(
                flight_number
            ),
            "duration_minutes": duration,
            "baggage": baggage,
            "self_transfer": self_transfer,
            "price_status": it.get(
                "price_status"
            ),
            "ignav_id": ignav_id,
            "stops": stops,
            "seen_at": utc_iso(),
            "recorded_at": utc_iso(),
            "base_fare": base,
            "taxes": taxes,
            "fees": fees,
            "tax_ratio": tax_ratio,
            "segments": segments
        })

    return results


def display_dedup_key(f):

    segment_key = "||".join(
        segment_signature(s)
        for s in f.get("segments", [])
        if segment_signature(s)
    )

    return (
        normalize_text(f.get("origin")),
        normalize_text(f.get("destination")),
        normalize_text(f.get("airline")),
        normalize_text(f.get("flight_number")),
        round(float(f.get("price", 0)), 2),
        normalize_text(f.get("currency")),
        bool(f.get("self_transfer")),
        int(f.get("stops", 0)),
        segment_key
    )


def dedup_flights(flights):

    unique = []
    seen = set()
    removed = 0

    for f in flights:

        key = display_dedup_key(f)

        if key in seen:
            removed += 1
            continue

        seen.add(key)
        unique.append(f)

    return unique, removed


def save_flight(f):

    conn = get_connection()

    now = utc_iso()

    conn.execute(
        """
        INSERT INTO prices(
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
            seen_at,
            recorded_at,
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
            f["flight_key"],
            f["origin"],
            f["destination"],
            f["departure_date"],
            f["price"],
            f["currency"],
            f["airline"],
            f["flight_number"],
            f["duration_minutes"],
            f["baggage"],
            int(f["self_transfer"]),
            f["price_status"],
            f["ignav_id"],
            f["stops"],
            now,
            now,
            f["base_fare"],
            f["taxes"],
            f["fees"],
            f["tax_ratio"]
        )
    )

    duplicate_window = (
        utc_now() - timedelta(
            minutes=30
        )
    ).isoformat()

    duplicate = conn.execute(
        """
        SELECT 1
        FROM price_observations
        WHERE flight_key=?
          AND departure_date=?
          AND currency=?
          AND ABS(price-?) < 0.01
          AND observed_at>=?
        LIMIT 1
        """,
        (
            f["flight_key"],
            f["departure_date"],
            f["currency"],
            f["price"],
            duplicate_window
        )
    ).fetchone()

    if not duplicate:

        conn.execute(
            """
            INSERT INTO price_observations(
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
                observed_at,
                base_fare,
                taxes,
                fees,
                tax_ratio
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,?,?,?
            )
            """,
            (
                f["flight_key"],
                f["origin"],
                f["destination"],
                f["departure_date"],
                f["price"],
                f["currency"],
                f["airline"],
                f["flight_number"],
                f["duration_minutes"],
                f["baggage"],
                int(f["self_transfer"]),
                f["price_status"],
                f["ignav_id"],
                f["stops"],
                now,
                f["base_fare"],
                f["taxes"],
                f["fees"],
                f["tax_ratio"]
            )
        )

    conn.commit()
    conn.close()


def get_previous_price(f):

    conn = get_connection()

    row = conn.execute(
        """
        SELECT price
        FROM price_observations
        WHERE flight_key=?
          AND departure_date=?
          AND currency=?
        ORDER BY id DESC
        LIMIT 2
        """,
        (
            f["flight_key"],
            f["departure_date"],
            f["currency"]
        )
    ).fetchall()

    conn.close()

    if len(row) >= 2:
        return float(row[1][0])

    return None


def normal_price(f):

    conn = get_connection()

    rows = conn.execute(
        """
        SELECT price
        FROM price_observations
        WHERE origin=?
          AND destination=?
          AND departure_date=?
          AND currency=?
        ORDER BY observed_at DESC
        LIMIT 300
        """,
        (
            f["origin"],
            f["destination"],
            f["departure_date"],
            f["currency"]
        )
    ).fetchall()

    conn.close()

    values = [
        float(x[0])
        for x in rows
        if x[0] is not None
    ]

    if len(values) < 3:
        return None

    return statistics.median(values)


def score_preliminary(
    f,
    previous,
    normal
):

    score = 0

    market_drop = 0
    previous_drop = 0
    sudden_drop = 0

    if normal and normal > 0:

        market_drop = max(
            0,
            (normal - f["price"])
            / normal * 100
        )

        if market_drop >= 70:
            score += 20
        elif market_drop >= 60:
            score += 18
        elif market_drop >= 50:
            score += 16
        elif market_drop >= 40:
            score += 13
        elif market_drop >= 30:
            score += 10
        elif market_drop >= 20:
            score += 6
        elif market_drop >= 10:
            score += 3

    if previous and previous > 0:

        previous_drop = max(
            0,
            (previous - f["price"])
            / previous * 100
        )

        sudden_drop = previous_drop

        if previous_drop >= 60:
            score += 15
        elif previous_drop >= 50:
            score += 13
        elif previous_drop >= 40:
            score += 11
        elif previous_drop >= 30:
            score += 9
        elif previous_drop >= 20:
            score += 6
        elif previous_drop >= 10:
            score += 3
        elif previous_drop > 0:
            score += 1

    if f.get("tax_ratio") is not None:
        if (
            f["tax_ratio"] > 50
            or f["tax_ratio"] < 5
        ):
            score += 5

    if f.get("currency") != "TRY":
        score += 1

    if f.get("stops", 0) == 0:
        score += 2

    if f.get("self_transfer"):
        score -= 8

    if f.get("price_status"):
        status = normalize_text(
            f["price_status"]
        )

        if "ERROR" in status:
            score += 5

    return (
        max(0, min(100, score)),
        market_drop,
        sudden_drop,
        previous_drop
    )


def recursive_booking_prices(v):

    values = []

    if isinstance(v, dict):

        cur = normalize_text(
            v.get("currency")
            or v.get("currency_code")
            or ""
        )

        for k, x in v.items():

            kl = str(k).lower()

            if any(
                t in kl
                for t in (
                    "price",
                    "amount",
                    "total",
                    "fare"
                )
            ):

                n = safe_float(x)

                if (
                    n is not None
                    and n > 0
                ):
                    values.append(
                        (n, cur)
                    )

            values.extend(
                recursive_booking_prices(x)
            )

    elif isinstance(v, list):

        for x in v:
            values.extend(
                recursive_booking_prices(x)
            )

    return values


def recursive_urls(v):

    urls = []

    if isinstance(v, dict):

        for x in v.values():

            urls.extend(
                recursive_urls(x)
            )

    elif isinstance(v, list):

        for x in v:
            urls.extend(
                recursive_urls(x)
            )

    elif isinstance(v, str):

        s = v.strip()

        if s.startswith(
            ("http://", "https://")
        ):

            try:

                parsed = urlparse(s)

                if parsed.scheme in (
                    "http",
                    "https"
                ) and parsed.netloc:

                    urls.append(s)

            except Exception:
                pass

    result = []

    for u in urls:

        if u not in result:
            result.append(u)

    return result


def booking_analysis(f):

    data = get_booking_links(
        f.get("ignav_id")
    )

    if not data:

        return {
            "checked": False,
            "verified": False,
            "points": 0,
            "urls": []
        }

    target = float(
        f["price"]
    )

    cur = normalize_text(
        f["currency"]
    )

    candidates = []

    for n, c in recursive_booking_prices(data):

        if not c or c == cur:
            candidates.append(
                (n, c)
            )

    near = []

    for n, c in candidates:

        if c and c != cur:
            continue

        diff = (
            abs(n - target)
            / target
            if target
            else 999
        )

        if (
            diff <= 0.03
            or abs(n - target) <= 100
        ):
            near.append(
                (diff, n)
            )

    near.sort()

    urls = recursive_urls(data)

    verified = bool(near)

    points = (
        15
        if verified
        else (
            4
            if candidates
            else 0
        )
    )

    if (
        candidates
        and not verified
    ):
        points = 2

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
            n
            for n, _ in candidates[:10]
        ]
    }


def verify_flight(f):

    data = ignav_search(
        f["origin"],
        f["destination"],
        f["departure_date"]
    )

    if not data:

        return {
            "verified": False,
            "reason": "ikinci arama sonucu yok"
        }

    candidates = extract_flights(
        data,
        f["origin"],
        f["destination"],
        f["departure_date"]
    )

    exact = []

    for x in candidates:

        if normalize_text(
            x["currency"]
        ) != normalize_text(
            f["currency"]
        ):
            continue

        if (
            abs(
                float(x["price"])
                - float(f["price"])
            )
            <= max(
                50,
                float(f["price"]) * 0.02
            )
        ):

            if (
                x["flight_key"]
                == f["flight_key"]
            ):
                exact.append(x)

    if exact:

        return {
            "verified": True,
            "reason":
                "ayni ucus ve yakin ayni fiyat "
                "ikinci aramada goruldu"
        }

    best = None
    bestd = 999

    for x in candidates:

        if normalize_text(
            x["currency"]
        ) != normalize_text(
            f["currency"]
        ):
            continue

        if (
            x["airline"]
            == f["airline"]
            and
            x["flight_number"]
            == f["flight_number"]
        ):

            d = (
                abs(
                    x["price"]
                    - f["price"]
                )
                / max(
                    f["price"],
                    1
                )
            )

            if d < bestd:
                bestd = d
                best = x

    if (
        best
        and bestd <= 0.03
    ):

        return {
            "verified": True,
            "reason":
                "ayni havayolu/ucus numarasi "
                "ve yakin fiyat"
        }

    return {
        "verified": False,
        "reason":
            "ikinci aramada yeterli eslesme yok"
    }


def final_score(
    pre,
    verify,
    booking,
    f,
    market_drop=0,
    previous_drop=0
):

    # V3.2 calibration:
    # very large, independently verified anomalies
    # must be able to cross the 85/90 radar bands.
    # Ordinary cheap fares remain below the strict
    # quality gate.

    score = pre

    if verify.get("verified"):
        score += 8

    if booking.get("verified"):
        score += 25

    elif booking.get("checked"):
        score += booking.get(
            "points",
            0
        )

    if market_drop >= 60:
        score += 15

    elif market_drop >= 50:
        score += 10

    elif market_drop >= 40:
        score += 6

    if previous_drop >= 50:
        score += 5

    if f.get("self_transfer"):
        score -= 8

    if f.get("stops", 0) > 1:
        score -= 3

    return max(
        0,
        min(100, score)
    )


def quality_gate(
    f,
    score,
    verified,
    booking,
    market_drop,
    previous_drop
):

    if not verified:
        return False

    if f.get("self_transfer"):

        return (
            score >= 90
            and market_drop >= 60
            and booking.get("verified")
        )

    strong_booking = booking.get(
        "verified"
    )

    return (
        (
            score >= 90
            and market_drop >= 50
        )
        or
        (
            score >= 85
            and market_drop >= 50
            and previous_drop >= 30
        )
        or
        (
            score >= 85
            and market_drop >= 40
            and strong_booking
        )
        or
        (
            score >= 85
            and market_drop >= 50
            and f.get("tax_ratio") is not None
        )
    )


def alert_level(score):

    if score >= 90:
        return "HIGH_CONFIDENCE_ERROR_FARE"

    if score >= 85:
        return "ERROR_FARE_CANDIDATE"

    if score >= 70:
        return "SUSPICIOUS"

    return "BELOW_ALERT"


def telegram_send(text):

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat:

        print(
            "Telegram bilgileri yok; "
            "bildirim atlanacak."
        )

        return False

    try:

        r = requests.post(
            f"https://api.telegram.org/"
            f"bot{token}/sendMessage",
            json={
                "chat_id": chat,
                "text": text,
                "disable_web_page_preview": False
            },
            timeout=30
        )

        print(
            "Telegram:",
            r.status_code
        )

        return r.status_code == 200

    except Exception as e:

        print(
            "Telegram hatasi:",
            repr(e)
        )

        return False


def alert_allowed(
    f,
    score,
    quality
):

    a = settings.get(
        "alerts",
        {}
    )

    if (
        not a.get(
            "enabled",
            True
        )
        or
        not a.get(
            "telegram_enabled",
            True
        )
        or
        not quality
    ):
        return False

    return (
        score
        >= int(
            a.get(
                "candidate_score",
                85
            )
        )
    )


def already_alerted(f):

    hours = float(
        settings.get(
            "radar",
            {}
        ).get(
            "duplicate_alert_window_hours",
            24
        )
    )

    conn = get_connection()

    row = conn.execute(
        """
        SELECT 1
        FROM alerts
        WHERE flight_key=?
          AND departure_date=?
          AND sent_at>=?
        LIMIT 1
        """,
        (
            f["flight_key"],
            f["departure_date"],
            (
                utc_now()
                - timedelta(
                    hours=hours
                )
            ).isoformat()
        )
    ).fetchone()

    conn.close()

    return bool(row)


def save_alert(
    f,
    score,
    level
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
            f["flight_key"],
            f["origin"],
            f["destination"],
            f["departure_date"],
            f["price"],
            f["currency"],
            f["airline"],
            f["flight_number"],
            score,
            level,
            utc_iso()
        )
    )

    conn.commit()
    conn.close()


def send_alert(
    f,
    score,
    normal,
    prev,
    booking
):

    level = alert_level(
        score
    )

    lines = [
        "🚨 UÇUŞ HATA FİYATI RADARI",
        (
            f"{f['origin']} → "
            f"{f['destination']}"
        ),
        (
            f"Tarih: "
            f"{f['departure_date']}"
        ),
        (
            f"Uçuş: "
            f"{f['airline']} "
            f"{f['flight_number']}"
        ),
        (
            f"Fiyat: "
            f"{f['price']:.0f} "
            f"{f['currency']}"
        ),
        (
            f"Skor: "
            f"{score:.0f} / 100"
        ),
        (
            f"Seviye: "
            f"{level}"
        )
    ]

    if normal:

        lines.append(
            f"Normal medyan: "
            f"{normal:.0f} "
            f"{f['currency']}  |  "
            f"düşüş: "
            f"{(normal-f['price'])/normal*100:.0f}%"
        )

    if prev:

        lines.append(
            f"Önceki fiyat: "
            f"{prev:.0f} "
            f"{f['currency']}"
        )

    if booking.get(
        "near_price"
    ) is not None:

        lines.append(
            f"Booking teyidi: "
            f"{booking['near_price']:.0f} "
            f"{f['currency']}"
        )

    if booking.get("urls"):

        lines.append(
            "Rezervasyon:"
        )

        lines.extend(
            f"{i+1}. {u}"
            for i, u in enumerate(
                booking["urls"]
            )
        )

    return telegram_send(
        "\n".join(lines)[:4090]
    )


def build_routes():

    a = settings.get(
        "airports",
        {}
    )

    origins = a.get(
        "priority_origins",
        []
    )

    routes = []

    domestic = (
        a.get(
            "domestic_destinations",
            []
        )
        if a.get(
            "domestic_enabled",
            True
        )
        else []
    )

    europe = (
        a.get(
            "europe_destinations",
            []
        )
        if a.get(
            "europe_enabled",
            True
        )
        else []
    )

    destinations = []

    for d in domestic + europe:

        if (
            d not in BLOCKED_DESTINATIONS
            and d not in destinations
        ):

            destinations.append(d)

    for o in origins:

        for d in destinations:

            if o != d:
                routes.append(
                    (o, d)
                )

    return routes


def rotated_routes(
    routes,
    count
):

    if not routes:
        return []

    conn = get_connection()

    idx = conn.execute(
        """
        SELECT route_index
        FROM radar_state
        WHERE id=1
        """
    ).fetchone()[0]

    chosen = [
        routes[
            (idx + i)
            % len(routes)
        ]
        for i in range(
            min(
                count,
                len(routes)
            )
        )
    ]

    new = (
        idx + len(chosen)
    ) % len(routes)

    conn.execute(
        """
        UPDATE radar_state
        SET route_index=?,
            updated_at=?
        WHERE id=1
        """,
        (
            new,
            utc_iso()
        )
    )

    conn.commit()
    conn.close()

    return chosen


def target_dates():

    offsets = settings.get(
        "radar",
        {}
    ).get(
        "dates_days_ahead",
        [30, 60, 90]
    )

    base = datetime.now(
        timezone.utc
    ).date()

    return [
        (
            base
            + timedelta(
                days=int(x)
            )
        ).isoformat()
        for x in offsets
    ]


def process_flight(f):

    save_flight(f)

    prev = get_previous_price(f)

    normal = normal_price(f)

    (
        pre,
        market_drop,
        sudden,
        previous_drop
    ) = score_preliminary(
        f,
        prev,
        normal
    )

    verify_min = int(
        settings.get(
            "radar",
            {}
        ).get(
            "verification",
            {}
        ).get(
            "minimum_score_for_verification",
            30
        )
    )

    verification = (
        verify_flight(f)
        if (
            pre >= verify_min
            and
            settings.get(
                "radar",
                {}
            ).get(
                "verification",
                {}
            ).get(
                "enabled",
                True
            )
        )
        else {
            "verified": False,
            "reason": "esik altinda"
        }
    )

    # Booking is deliberately independent
    # from the old >=60 deadlock.
    booking_trigger = (
        pre >= 35
        and (
            market_drop >= 35
            or previous_drop >= 25
            or pre >= 50
        )
    )

    booking = (
        booking_analysis(f)
        if booking_trigger
        else {
            "checked": False,
            "verified": False,
            "points": 0,
            "urls": []
        }
    )

    score = final_score(
        pre,
        verification,
        booking,
        f,
        market_drop,
        previous_drop
    )

    quality = quality_gate(
        f,
        score,
        verification.get(
            "verified",
            False
        ),
        booking,
        market_drop,
        previous_drop
    )

    if (
        alert_allowed(
            f,
            score,
            quality
        )
        and
        not already_alerted(f)
    ):

        if send_alert(
            f,
            score,
            normal,
            prev,
            booking
        ):

            save_alert(
                f,
                score,
                alert_level(score)
            )

    return {
        "pre": pre,
        "score": score,
        "verified": verification.get(
            "verified",
            False
        ),
        "booking_checked":
            booking.get(
                "checked",
                False
            ),
        "booking_verified":
            booking.get(
                "verified",
                False
            ),
        "quality": quality,
        "market_drop": market_drop,
        "previous_drop": previous_drop
    }


def main():

    init_db()

    routes = build_routes()

    chosen = rotated_routes(
        routes,
        int(
            settings.get(
                "radar",
                {}
            ).get(
                "routes_per_run",
                10
            )
        )
    )

    dates = target_dates()

    print(
        f"Toplam rota havuzu: "
        f"{len(routes)} | "
        f"Bu calismada: "
        f"{len(chosen)} | "
        f"Tarih: {dates}"
    )

    total = 0
    dedup = 0
    verified = 0
    bookings = 0
    booking_verified = 0
    alerts = 0

    for origin, destination in chosen:

        for date in dates:

            data = ignav_search(
                origin,
                destination,
                date
            )

            if not data:
                continue

            flights, dc = dedup_flights(
                extract_flights(
                    data,
                    origin,
                    destination,
                    date
                )
            )

            dedup += dc
            total += len(flights)

            for f in flights:

                r = process_flight(f)

                verified += int(
                    r["verified"]
                )

                bookings += int(
                    r["booking_checked"]
                )

                booking_verified += int(
                    r["booking_verified"]
                )

                alerts += int(
                    r["quality"]
                    and
                    r["score"] >= 85
                )

                print(
                    f"  "
                    f"{f['origin']}"
                    f"->{f['destination']} "
                    f"{f['departure_date']} "
                    f"{f['airline']} "
                    f"{f['flight_number']} "
                    f"{f['price']:.0f} "
                    f"{f['currency']} "
                    f"| pre={r['pre']} "
                    f"final={r['score']} "
                    f"verify={r['verified']} "
                    f"booking="
                    f"{r['booking_checked']}/"
                    f"{r['booking_verified']} "
                    f"quality="
                    f"{'PASS' if r['quality'] else 'FAIL'}"
                )

    print(
        "\n=== V3.2 DIAGNOSTIK ==="
    )

    print(
        f"Islenen ucus: {total}"
    )

    print(
        f"Dedup edilen: {dedup}"
    )

    print(
        f"Verification: {verified}"
    )

    print(
        f"Booking kontrolu: {bookings}"
    )

    print(
        f"Booking yakin fiyat teyidi: "
        f"{booking_verified}"
    )

    print(
        f"Kaliteyi gecen aday: {alerts}"
    )

    print(
        "Calisma tamamlandi."
    )


if __name__ == "__main__":
    main()
