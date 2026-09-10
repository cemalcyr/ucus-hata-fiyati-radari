import os
import json
import requests
from datetime import date, timedelta

API_KEY = os.environ.get("IGNAV_API_KEY")
API_URL = "https://ignav.com/api"


def search_flights(origin, destination, departure_date):
    if not API_KEY:
        raise RuntimeError("IGNAV_API_KEY bulunamadi.")

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
            "children": 0,
            "infants_on_lap": 0,
            "infants_in_seat": 0,
            "cabin_class": "economy",
            "max_stops": 2,
            "allow_self_transfer": False,
            "market": "TR",
        },
        timeout=60,
    )

    response.raise_for_status()
    return response.json()


def main():
    departure_date = (
        date.today() + timedelta(days=30)
    ).isoformat()

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
                departure_date,
            )

            itineraries = data.get("itineraries", [])

            simplified = []

            for item in itineraries[:5]:
                price = item.get("price", {})
                outbound = item.get("outbound", {})

                simplified.append({
                    "price": price,
                    "carrier": outbound.get("carrier"),
                    "duration_minutes": outbound.get(
                        "duration_minutes"
                    ),
                    "self_transfer": item.get(
                        "requires_self_transfer"
                    ),
                    "ignav_id": item.get("ignav_id"),
                })

            results.append({
                "origin": origin,
                "destination": destination,
                "departure_date": departure_date,
                "results": simplified,
            })

        except Exception as error:
            results.append({
                "origin": origin,
                "destination": destination,
                "error": str(error),
            })

    print(
        json.dumps(
            results,
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
