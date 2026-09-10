import os
import json
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("IGNAV_API_KEY")
API_URL = "https://ignav.com/api"
SETTINGS_FILE = "config/settings.json"


def load_settings():
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def build_routes(settings):
    routes = []

    domestic = settings["airports"]["domestic_destinations"]
    europe = settings["airports"]["europe_destinations"]

    # SZF -> Türkiye
    if settings["airports"]["domestic_enabled"]:
        for destination in domestic:
            if destination != "SZF":
                routes.append(("SZF", destination))

        # Türkiye -> SZF
        for origin in domestic:
            if origin != "SZF":
                routes.append((origin, "SZF"))

    # SZF -> Avrupa
    if settings["airports"]["europe_enabled"]:
        for destination in europe:
            routes.append(("SZF", destination))

        # Avrupa -> SZF
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


def main():
    if not API_KEY:
        raise RuntimeError("IGNAV_API_KEY bulunamadi.")

    settings = load_settings()
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
    print("API butcesi:",
          settings["system"]["api_budget_tl"], "TL")
    print()

    # İlk aşamada güvenli test:
    # Sadece ilk 5 rota taranır.
    test_routes = routes[:5]

    print("Bu calismada taranacak rota:", len(test_routes))
    print()

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
            print("  OK - veri alindi")
        else:
            print("  Veri alinamadi")

        print()

    print("======================================")
    print("TARAMA TESTI TAMAMLANDI")
    print("======================================")


if __name__ == "__main__":
    main()
