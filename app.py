import os
import json
import sqlite3
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
    segments = flight.get("outbound", {}).get("segments", [])

    if not segments:
        return None

    first = segments[0]

    return (
        first.get("carrier")
        or first.get("airline")
    )


def get_flight_number(flight):
    segments = flight.get("outbound", {}).get("segments", [])

    if not segments:
        return None

    first = segments[0]

    return (
        first.get("flight_number")
        or first.get("flightNumber")
    )


def get_duration(flight):
    outbound = flight.get("outbound", {})

    return (
        outbound.get("duration_minutes")
        or flight.get("duration_minutes")
    )


def get_checked_bags(flight):
    baggage = flight.get("baggage")

    if isinstance(baggage, dict):
        return baggage.get("checked_bags")

    return None


def get_self_transfer(flight):
    return bool(
        flight.get("requires_self_transfer", False)
    )


def save_flight(
    connection,
    origin,
    destination,
    departure_date,
    flight
):
    connection.execute("""
        INSERT INTO prices (
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
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        origin,
        destination,
        departure_date,
        get_price(flight),
        get_currency(flight),
        get_airline(flight),
        get_flight_number(flight),
        get_duration(flight),
        get_checked_bags(flight),
        int(get_self_transfer(flight)),
        flight.get("price", {}).get("status")
        if isinstance(flight.get("price"), dict)
        else None,
        "Ignav",
        datetime.utcnow().isoformat()
    ))

    connection.commit()


def calculate_error_score(
    connection,
    origin,
    destination,
    departure_date,
    current_price
):
    if current_price is None or current_price <= 0:
        return 0, 0, None

    cursor = connection.execute("""
        SELECT price
        FROM prices
        WHERE origin = ?
          AND destination = ?
          AND departure_date = ?
          AND price IS NOT NULL
          AND price > 0
        ORDER BY id DESC
        LIMIT 100
    """, (
        origin,
        destination,
        departure_date
    ))

    rows = cursor.fetchall()

    prices = [
        float(row[0])
        for row in rows
    ]

    # Mevcut taramayı geçmiş karşılaştırmasına
    # dahil etmeden önce yeterli geçmiş var mı?
    if len(prices) < 2:
        return 0, 0, None

    average_price = sum(prices) / len(prices)

    minimum_price = min(prices)

    if average_price <= 0:
        return 0, 0, None

    drop_percent = (
        (average_price - current_price)
        / average_price
    ) * 100

    # Sadece fiyat düşüşüne dayalı ilk skor.
    # Diğer puanlar sonraki aşamalarda eklenecek.
    historical_score = min(
        20,
        max(
            0,
            drop_percent * 0.5
        )
    )

    error_score = round(
        historical_score,
        1
    )

    return (
        error_score,
        round(drop_percent, 1),
        round(average_price, 2)
    )


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

    # Şimdilik API tüketimini düşük tutuyoruz.
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

            if price is None:
                continue

            currency = get_currency(flight)

            # Şimdilik yalnızca TRY fiyatlarını
            # doğrudan karşılaştırıyoruz.
            if currency != "TRY":
                continue

            error_score, drop_percent, average_price = (
                calculate_error_score(
                    connection,
                    origin,
                    destination,
                    departure_date,
                    price
                )
            )

            save_flight(
                connection,
                origin,
                destination,
                departure_date,
                flight
            )

            total_saved += 1

            print(
                f"  {origin}->{destination} "
                f"{price:.0f} {currency} | "
                f"Error Score: {error_score}/100"
            )

            if average_price is not None:
                print(
                    f"  Normal ortalama: "
                    f"{average_price:.0f} TRY | "
                    f"Dusus: {drop_percent}%"
                )

        print()

    connection.close()

    print("======================================")
    print("TARAMA TAMAMLANDI")
    print("======================================")
    print("Kaydedilen fiyat:", total_saved)
    print()
    print("Error Score motoru aktif.")


if __name__ == "__main__":
    main()
