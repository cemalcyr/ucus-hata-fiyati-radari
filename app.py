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

    return (
        segments[0].get("carrier")
        or segments[0].get("airline")
    )


def get_flight_number(flight):
    segments = flight.get("outbound", {}).get("segments", [])

    if not segments:
        return None

    return (
        segments[0].get("flight_number")
        or segments[0].get("flightNumber")
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
    price = get_price(flight)

    price_info = flight.get("price", {})

    status = None

    if isinstance(price_info, dict):
        status = price_info.get("status")

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


def get_history(
    connection,
    origin,
    destination,
    departure_date
):
    cursor = connection.execute("""
        SELECT price
        FROM prices
        WHERE origin = ?
          AND destination = ?
          AND departure_date = ?
          AND currency = 'TRY'
          AND price IS NOT NULL
          AND price > 0
        ORDER BY id DESC
        LIMIT 100
    """, (
        origin,
        destination,
        departure_date
    ))

    return [
        float(row[0])
        for row in cursor.fetchall()
    ]


def calculate_error_score(
    connection,
    origin,
    destination,
    departure_date,
    current_price,
    settings
):
    weights = settings["scoring"]["error_score"]

    history = get_history(
        connection,
        origin,
        destination,
        departure_date
    )

    # Henüz yeterli geçmiş yoksa güvenilir
    # tarihsel karşılaştırma yapmıyoruz.
    if len(history) < 3:
        return {
            "total": 0,
            "band": "veri yetersiz",
            "historical": 0,
            "market": 0,
            "sudden_drop": 0,
            "source_disagreement": 0,
            "tax": 0,
            "currency": 0,
            "fare": 0,
            "short_lived": 0,
            "reliability": 0,
            "average": None,
            "drop_percent": None,
            "history_count": len(history)
        }

    average = sum(history) / len(history)
    minimum = min(history)

    if average <= 0:
        return {
            "total": 0,
            "band": "veri yetersiz",
            "historical": 0,
            "market": 0,
            "sudden_drop": 0,
            "source_disagreement": 0,
            "tax": 0,
            "currency": 0,
            "fare": 0,
            "short_lived": 0,
            "reliability": 0,
            "average": average,
            "drop_percent": 0,
            "history_count": len(history)
        }

    drop_percent = (
        (average - current_price)
        / average
    ) * 100

    # 1. Tarihsel anomali - 20 puan
    historical_score = min(
        weights["historical_anomaly"],
        max(
            0,
            drop_percent / 100
            * weights["historical_anomaly"]
        )
    )

    # 2. Piyasa karşılaştırması
    # İkinci kaynak henüz eklenmedi.
    market_score = 0

    # 3. Ani düşüş
    sudden_drop_score = 0

    if len(history) >= 2:
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

    # 4. Kaynaklar arası fark
    source_disagreement_score = 0

    # 5. Vergi / ücret anomalisi
    tax_score = 0

    # 6. Kur anomalisi
    currency_score = 0

    # 7. Fare anomalisi
    fare_score = 0

    # 8. Kısa süreli fiyat
    short_lived_score = 0

    # 9. Kaynak güvenilirliği
    # Şimdilik Ignav verisinin API cevabını
    # başarıyla almamız yalnız başına
    # "yüksek güven" anlamına gelmez.
    reliability_score = 0

    total = (
        historical_score
        + market_score
        + sudden_drop_score
        + source_disagreement_score
        + tax_score
        + currency_score
        + fare_score
        + short_lived_score
        + reliability_score
    )

    total = round(
        min(100, max(0, total)),
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
        "historical": round(historical_score, 1),
        "market": round(market_score, 1),
        "sudden_drop": round(sudden_drop_score, 1),
        "source_disagreement": round(
            source_disagreement_score, 1
        ),
        "tax": round(tax_score, 1),
        "currency": round(currency_score, 1),
        "fare": round(fare_score, 1),
        "short_lived": round(
            short_lived_score, 1
        ),
        "reliability": round(
            reliability_score, 1
        ),
        "average": round(average, 2),
        "minimum": round(minimum, 2),
        "drop_percent": round(
            drop_percent, 1
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

            score = calculate_error_score(
                connection,
                origin,
                destination,
                departure_date,
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
                f"  Fiyat: {price:.0f} {currency}"
            )
            print(
                f"  Error Score: "
                f"{score['total']}/100"
            )
            print(
                f"  Durum: {score['band']}"
            )

            if score["average"] is not None:
                print(
                    f"  Normal ortalama: "
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

            print(
                "  Puan dağılımı: "
                f"Tarihsel={score['historical']} | "
                f"Ani düşüş={score['sudden_drop']} | "
                f"Piyasa={score['market']}"
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
        "100 puanlık Error Score aktif."
    )


if __name__ == "__main__":
    main()
