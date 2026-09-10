import os
import json
import sqlite3
import hashlib
import requests
from datetime import date, timedelta, datetime

API_KEY = os.environ.get("IGNAV_API_KEY")
API_URL = "https://ignav.com/api"
SETTINGS_FILE = "config/settings.json"
DATABASE_FILE = "prices.db"


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def create_database():
    connection = sqlite3.connect(DATABASE_FILE)

    connection.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            price REAL,
            currency TEXT,
            airline TEXT,
            flight_number TEXT,
            duration_minutes INTEGER,
            checked_bags INTEGER,
            self_transfer INTEGER,
            status TEXT,
            source TEXT,
            recorded_at TEXT NOT NULL
        )
    """)

    connection.commit()

    # Eski veritabanlarında flight_key yoksa ekle.
    columns = [
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(prices)"
        ).fetchall()
    ]

    if "flight_key" not in columns:
        connection.execute(
            "ALTER TABLE prices ADD COLUMN flight_key TEXT"
        )
        connection.commit()

    return connection


def build_routes(settings):
    routes = []

    domestic = settings["airports"]["domestic_destinations"]
    europe = settings["airports"]["europe_destinations"]

    if settings["airports"]["domestic_enabled"]:
        for destination in domestic:
            if destination != "SZF":
                routes.append(("SZF", destination))

        for origin in domestic:
            if origin != "SZF":
                routes.append((origin, "SZF"))

    if settings["airports"]["europe_enabled"]:
        for destination in europe:
            routes.append(("SZF", destination))

        for origin in europe:
            routes.append((origin, "SZF"))

    return routes


def search_flight(origin, destination, departure_date, settings):
    passengers = settings["passengers"]
    connections = settings["connections"]

    response = requests.post(
        f"{API_URL}/fares/one-way",
        headers={
            "X-Api-Key": API_KEY,
            "Content-Type": "application/json"
        },
        json={
            "origin": origin,
            "destination": destination,
            "departure_date": departure_date,
            "adults": passengers["adults"],
            "children": passengers["children"],
            "infants_on_lap": passengers["infants"],
            "cabin_class": "economy",
            "max_stops": connections["max_connections"],
            "allow_self_transfer": connections["self_transfer"],
            "market": "TR"
        },
        timeout=60
    )

    if not response.ok:
        print(
            f"HATA {origin}->{destination}: "
            f"{response.status_code}"
        )
        return None

    return response.json()


def extract_flights(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    # Güncel Ignav formatı
    if isinstance(data.get("itineraries"), list):
        return data["itineraries"]

    # Eski/alternatif formatlar
    for key in ["fares", "results", "flights", "data"]:
        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


def get_price(flight):
    price_info = flight.get("price", {})

    if isinstance(price_info, dict):
        return price_info.get("amount")

    if isinstance(price_info, (int, float)):
        return price_info

    return None


def get_currency(flight):
    price_info = flight.get("price", {})

    if isinstance(price_info, dict):
        return price_info.get("currency")

    return None


def get_airline(flight):
    outbound = flight.get("outbound", {})
    segments = outbound.get("segments", [])

    if not segments:
        return outbound.get("carrier")

    return (
        segments[0].get("marketing_carrier_code")
        or outbound.get("carrier")
        or segments[0].get("operating_carrier_name")
    )


def get_flight_number(flight):
    segments = flight.get(
        "outbound", {}
    ).get("segments", [])

    if not segments:
        return None

    return segments[0].get("flight_number")


def get_duration(flight):
    outbound = flight.get("outbound", {})

    return (
        outbound.get("duration_minutes")
        or flight.get("duration_minutes")
    )


def get_checked_bags(flight):
    # Güncel Ignav formatı: bags.checked
    bags = flight.get("bags")

    if isinstance(bags, dict):
        if bags.get("checked") is not None:
            return bags.get("checked")

    # Eski format için
    baggage = flight.get("baggage")

    if isinstance(baggage, dict):
        return baggage.get("checked_bags")

    return None


def get_self_transfer(flight):
    return bool(
        flight.get(
            "requires_self_transfer",
            False
        )
    )


def create_flight_key(
    origin,
    destination,
    departure_date,
    flight
):
    outbound = flight.get("outbound", {})
    segments = outbound.get("segments", [])

    parts = [
        origin,
        destination,
        departure_date,
        str(flight.get("cabin_class", "economy"))
    ]

    for segment in segments:
        parts.extend([
            segment.get(
                "marketing_carrier_code",
                ""
            ),
            segment.get(
                "flight_number",
                ""
            ),
            segment.get(
                "departure_airport",
                ""
            ),
            segment.get(
                "arrival_airport",
                ""
            ),
            segment.get(
                "departure_time_local",
                ""
            ),
            segment.get(
                "arrival_time_local",
                ""
            )
        ])

    raw_key = "|".join(parts)

    return hashlib.sha256(
        raw_key.encode("utf-8")
    ).hexdigest()[:32]


def save_flight(
    connection,
    origin,
    destination,
    departure_date,
    flight
):
    price = get_price(flight)

    price_info = flight.get("price", {})

    status = None

    if isinstance(price_info, dict):
        status = price_info.get("status")

    flight_key = create_flight_key(
        origin,
        destination,
        departure_date,
        flight
    )

    connection.execute("""
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
            checked_bags,
            self_transfer,
            status,
            source,
            recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        flight_key,
        origin,
        destination,
        departure_date,
        price,
        get_currency(flight),
        get_airline(flight),
        get_flight_number(flight),
        get_duration(flight),
        get_checked_bags(flight),
        int(get_self_transfer(flight)),
        status,
        "Ignav",
        datetime.utcnow().isoformat()
    ))

    connection.commit()

    return flight_key


def get_history(
    connection,
    flight_key
):
    cursor = connection.execute("""
        SELECT price
        FROM prices
        WHERE flight_key = ?
          AND currency = 'TRY'
          AND price IS NOT NULL
          AND price > 0
        ORDER BY id DESC
        LIMIT 100
    """, (flight_key,))

    return [
        float(row[0])
        for row in cursor.fetchall()
    ]


def calculate_error_score(
    connection,
    flight_key,
    current_price,
    settings
):
    weights = settings["scoring"]["error_score"]

    history = get_history(
        connection,
        flight_key
    )

    if len(history) < 3:
        return {
            "total": 0,
            "band": "veri yetersiz",
            "historical": 0,
            "sudden_drop": 0,
            "average": None,
            "drop_percent": None,
            "history_count": len(history)
        }

    average = sum(history) / len(history)

    if average <= 0:
        return {
            "total": 0,
            "band": "veri yetersiz",
            "historical": 0,
            "sudden_drop": 0,
            "average": None,
            "drop_percent": None,
            "history_count": len(history)
        }

    drop_percent = (
        (average - current_price)
        / average
    ) * 100

    historical_score = min(
        weights["historical_anomaly"],
        max(
            0,
            drop_percent / 100
            * weights["historical_anomaly"]
        )
    )

    sudden_drop_score = 0

    if len(history) >= 1:
        previous = history[0]

        if previous > 0:
            sudden_drop = (
                (previous - current_price)
                / previous
            ) * 100

            sudden_drop_score = min(
                weights["sudden_drop"],
                max(
                    0,
                    sudden_drop / 50
                    * weights["sudden_drop"]
                )
            )

    total = round(
        min(
            100,
            historical_score
            + sudden_drop_score
        ),
        1
    )

    thresholds = settings["scoring"]["thresholds"]

    if total >= thresholds["high_confidence"]:
        band = "yüksek güvenli hata fiyatı"
    elif total >= thresholds["candidate"]:
        band = "aday"
    elif total >= thresholds["suspicious"]:
        band = "şüpheli"
    elif total >= thresholds["good_deal"]:
        band = "iyi fırsat"
    else:
        band = "normal"

    return {
        "total": total,
        "band": band,
        "historical": round(
            historical_score,
            1
        ),
        "sudden_drop": round(
            sudden_drop_score,
            1
        ),
        "average": round(
            average,
            2
        ),
        "drop_percent": round(
            drop_percent,
            1
        ),
        "history_count": len(history)
    }


def main():
    if not API_KEY:
        raise RuntimeError(
            "IGNAV_API_KEY bulunamadi."
        )

    settings = load_settings()
    connection = create_database()

    routes = build_routes(settings)

    departure_date = (
        date.today() + timedelta(days=30)
    ).isoformat()

    print("======================================")
    print("UCUS HATA FIYATI RADARI")
    print("======================================")
    print()
    print("Tarama tarihi:", departure_date)
    print("Toplam rota:", len(routes))
    print()

    # Şimdilik düşük API kullanımı.
    test_routes = routes[:5]

    total_saved = 0

    for number, (origin, destination) in enumerate(
        test_routes,
        start=1
    ):
        print(
            f"[{number}/{len(test_routes)}] "
            f"{origin} -> {destination}"
        )

        data = search_flight(
            origin,
            destination,
            departure_date,
            settings
        )

        if data is None:
            print("  Veri alinamadi")
            print()
            continue

        flights = extract_flights(data)

        print(
            "  Bulunan ucus:",
            len(flights)
        )

        for flight in flights:
            price = get_price(flight)
            currency = get_currency(flight)

            if price is None:
                continue

            if currency != "TRY":
                continue

            flight_key = create_flight_key(
                origin,
                destination,
                departure_date,
                flight
            )

            score = calculate_error_score(
                connection,
                flight_key,
                price,
                settings
            )

            save_flight(
                connection,
                origin,
                destination,
                departure_date,
                flight
            )

            total_saved += 1

            print()
            print(
                f"  {origin} -> {destination}"
            )
            print(
                f"  Uçuş: "
                f"{get_airline(flight)} "
                f"{get_flight_number(flight)}"
            )
            print(
                f"  Fiyat: "
                f"{price:.0f} {currency}"
            )
            print(
                f"  Flight Key: "
                f"{flight_key[:12]}..."
            )
            print(
                f"  Error Score: "
                f"{score['total']}/100"
            )
            print(
                f"  Durum: "
                f"{score['band']}"
            )

            if score["average"] is not None:
                print(
                    f"  Bu uçuşun normal "
                    f"ortalaması: "
                    f"{score['average']:.0f} TRY"
                )
                print(
                    f"  Düşüş: "
                    f"{score['drop_percent']}%"
                )

            print(
                f"  Geçmiş kayıt: "
                f"{score['history_count']}"
            )

        print()

    connection.close()

    print("======================================")
    print("TARAMA TAMAMLANDI")
    print("======================================")
    print(
        "Kaydedilen fiyat:",
        total_saved
    )
    print(
        "Uçuş kimliği sistemi aktif."
    )


if __name__ == "__main__":
    main()
