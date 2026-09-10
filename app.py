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


def save_flights(connection, origin, destination, departure_date, data):
    flights = extract_flights(data)

    saved = 0

    for flight in flights:
        if not isinstance(flight, dict):
            continue

        price_info = flight.get("price", {})

        if isinstance(price_info, dict):
            price = price_info.get("amount")
            currency = price_info.get("currency")
            status = price_info.get("status")
        else:
            price = price_info
            currency = None
            status = None

        segments = flight.get("outbound", {}).get("segments", [])

        airline = None
        flight_number = None
        duration = None

        if segments:
            first_segment = segments[0]

            airline = (
                first_segment.get("carrier")
                or first_segment.get("airline")
            )

            flight_number = (
                first_segment.get("flight_number")
                or first_segment.get("flightNumber")
            )

        duration = (
            flight.get("outbound", {}).get("duration_minutes")
            or flight.get("duration_minutes")
        )

        checked_bags = None

        baggage = flight.get("baggage")

        if isinstance(baggage, dict):
            checked_bags = baggage.get("checked_bags")

        self_transfer = flight.get(
            "requires_self_transfer",
            False
        )

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
            price,
            currency,
            airline,
            flight_number,
            duration,
            checked_bags,
            int(bool(self_transfer)),
            status,
            "Ignav",
            datetime.utcnow().isoformat()
        ))

        saved += 1

    connection.commit()

    return saved


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

    test_routes = routes[:5]

    total_saved = 0

    for number, (origin, destination) in enumerate(
        test_routes, start=1
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

        if data is not None:
            saved = save_flights(
                connection,
                origin,
                destination,
                departure_date,
                data
            )

            total_saved += saved

            print(
                f"  OK - {saved} fiyat kaydedildi"
            )
        else:
            print("  Veri alinamadi")

        print()

    connection.close()

    print("======================================")
    print("TARAMA TAMAMLANDI")
    print("======================================")
    print("Kaydedilen fiyat:", total_saved)


if __name__ == "__main__":
    main()
