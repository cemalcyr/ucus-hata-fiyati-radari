import os
import json
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("IGNAV_API_KEY")
API_URL = "https://ignav.com/api"
SETTINGS_FILE = "config/settings.json"


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        raise FileNotFoundError(
            f"{SETTINGS_FILE} bulunamadi."
        )

    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def search_flights(origin, destination, departure_date, settings):
    passengers = settings["passengers"]
    connections = settings["connections"]

    response = requests.post(
        f"{API_URL}/fares/one-way",
        headers={
            "X-Api-Key": API_KEY,
            "Content-Type": "application/json",
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

            "market": "TR",
        },
        timeout=60,
    )

    print("HTTP STATUS:", response.status_code)

    if not response.ok:
        print("API ERROR:")
        print(response.text)
        response.raise_for_status()

    return response.json()


def main():
    if not API_KEY:
        raise RuntimeError(
            "IGNAV_API_KEY bulunamadi."
        )

    settings = load_settings()

    departure_date = (
        date.today() + timedelta(days=30)
    ).isoformat()

    print("================================")
    print("UCUS HATA FIYATI RADARI")
    print("================================")
    print()

    print("Ayarlar basariyla okundu.")
    print()

    print("Oncelikli kalkislar:",
          settings["airports"]["priority_origins"])

    print("Oncelikli varisler:",
          settings["airports"]["priority_destinations"])

    print("Yolcular:",
          settings["passengers"])

    print("Maksimum aktarma:",
          settings["connections"]["max_connections"])

    print("Kendi kendine aktarma:",
          settings["connections"]["self_transfer"])

    print("API butcesi:",
          settings["system"]["api_budget_tl"],
          "TL")

    print("Ucretli API izni:",
          settings["system"]["paid_api_allowed"])

    print()
    print("TEST")
    print("--------------------------------")

    data = search_flights(
        "SZF",
        "IST",
        departure_date,
        settings
    )

    print()
    print("SONUC BASARILI")
    print("--------------------------------")

    if isinstance(data, dict):
        print("API cevabi alindi.")
        print("Anahtarlar:", list(data.keys()))

    print()
    print("Tarama tamamlandi.")


if __name__ == "__main__":
    main()
