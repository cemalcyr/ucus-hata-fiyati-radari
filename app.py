import os
import json
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("IGNAV_API_KEY")
API_URL = "https://ignav.com/api"


def search_flights(origin, destination, departure_date):
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
            "adults": 1,
            "cabin_class": "economy",
            "max_stops": 2,
            "allow_self_transfer": False,
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
        raise RuntimeError("IGNAV_API_KEY bulunamadi.")

    departure_date = (
        date.today() + timedelta(days=30)
    ).isoformat()

    print("Ucus Hata Fiyati Radari")
    print("======================")
    print("Test rotasi: SZF -> IST")
    print("Tarih:", departure_date)
    print()

    data = search_flights(
        "SZF",
        "IST",
        departure_date
    )

    print(json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    ))


if __name__ == "__main__":
    main()
