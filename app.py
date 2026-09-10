import os
import json
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("IGNAV_API_KEY")
BASE_URL = "https://ignav.com/api"


def search_flights(origin, destination, departure_date):
    if not API_KEY:
        raise RuntimeError("IGNAV_API_KEY bulunamadı.")

    url = f"{BASE_URL}/fares/one-way"

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": 1,
        "children": 0,
        "infants_on_lap": 0,
        "cabin_class": "economy",
        "max_stops": 2,
        "allow_self_transfer": False,
        "market": "TR"
    }

    response = requests.post(
        url,
        headers={
            "X-Api-Key": API_KEY,
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )

    response.raise_for_status()
    return response.json()


def main():
    tomorrow = date.today() + timedelta(days=30)

    routes = [
        ("SZF", "IST"),
        ("SZF", "SAW"),
        ("IST", "SZF"),
        ("SAW", "SZF"),
    ]

    results = []

    for origin, destination in routes:
        try:
            data = search_flights(
                origin,
                destination,
                tomorrow.isoformat()
            )

            results.append({
                "origin": origin,
                "destination": destination,
                "date": tomorrow.isoformat(),
                "data": data
            })

        except Exception as e:
            results.append({
                "origin": origin,
                "destination": destination,
                "error": str(e)
            })

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
