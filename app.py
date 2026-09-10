import os
import sqlite3
import statistics
from datetime import datetime, timedelta

import requests


DB_FILE = "prices.db"
SETTINGS_FILE = "config/settings.json"


# =========================================================
# AYARLAR
# =========================================================

def load_settings():
    import json

    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


settings = load_settings()


# =========================================================
# VERİTABANI
# =========================================================

def get_connection():
    return sqlite3.connect(DB_FILE)


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
            seen_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT,
            verified INTEGER NOT NULL,
            checked_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            price REAL NOT NULL,
            currency TEXT,
            score REAL,
            alert_level TEXT,
            sent_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# IGNAV
# =========================================================

def ignav_search(origin, destination, departure_date):
    api_key = os.getenv("IGNAV_API_KEY")

    if not api_key:
        print("IGNAV_API_KEY bulunamadi.")
        return None

    url = "https://ignav.com/api/fares/one-way"

    payload = {
        "origin": origin,
        "destination": destination,
        "departure_date": departure_date,
        "adults": settings["passengers"]["adults"],
        "children": settings["passengers"]["children"],
        "infants_in_seat": 0,
        "infants_on_lap": settings["passengers"]["infants"],
        "cabin_class": "economy"
    }

    if settings["cabin"]["business"]:
        payload["cabin_class"] = "business"

    payload["max_stops"] = settings["connections"]["max_connections"]
    payload["self_transfer"] = settings["connections"]["self_transfer"]

    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=60
        )

        print(
            f"Ignav: {origin}->{destination} "
            f"{departure_date} "
            f"HTTP {response.status_code}"
        )

        if response.status_code != 200:
            print(response.text[:500])
            return None

        return response.json()

    except Exception as e:
        print("Ignav hatasi:", e)
        return None


# =========================================================
# UÇUŞ VERİSİ ÇIKARMA
# =========================================================

def extract_flights(data):
    if not data:
        return []

    itineraries = data.get("itineraries", [])

    results = []

    for item in itineraries:
        price_info = item.get("price", {})
        outbound = item.get("outbound", {})

        price = price_info.get("amount")
        currency = price_info.get("currency")

        if price is None:
            continue

        segments = outbound.get("segments", [])

        if not segments:
            continue

        first_segment = segments[0]

        origin = first_segment.get("departure_airport", "")
        destination = segments[-1].get("arrival_airport", "")

        airline = (
            outbound.get("carrier")
            or first_segment.get("marketing_carrier_code")
            or ""
        )

        flight_number = first_segment.get("flight_number", "")

        duration = outbound.get("duration_minutes", 0)

        baggage_info = item.get("bags", {})
        baggage_text = str(baggage_info)

        self_transfer = bool(
            item.get("requires_self_transfer", False)
        )

        flight_key = create_flight_key(
            origin,
            destination,
            flight_number,
            airline,
            duration
        )

        results.append({
            "flight_key": flight_key,
            "origin": origin,
            "destination": destination,
            "price": float(price),
            "currency": currency,
            "airline": airline,
            "flight_number": flight_number,
            "duration_minutes": duration,
            "baggage": baggage_text,
            "self_transfer": self_transfer
        })

    return results


# =========================================================
# UÇUŞ ANAHTARI
# =========================================================

def create_flight_key(
    origin,
    destination,
    flight_number,
    airline,
    duration
):
    return (
        f"{origin}-"
        f"{destination}-"
        f"{airline}-"
        f"{flight_number}-"
        f"{duration}"
    )


# =========================================================
# FİYAT KAYDETME
# =========================================================

def save_flight(flight, departure_date):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
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
            baggage,
            self_transfer,
            seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        flight["flight_key"],
        flight["origin"],
        flight["destination"],
        departure_date,
        flight["price"],
        flight["currency"],
        flight["airline"],
        flight["flight_number"],
        flight["duration_minutes"],
        flight["baggage"],
        int(flight["self_transfer"]),
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


# =========================================================
# ROTA FİYAT GEÇMİŞİ
# =========================================================

def get_route_history(
    origin,
    destination,
    currency,
    exclude_price=None
):
    conn = get_connection()
    cur = conn.cursor()

    query = """
        SELECT price
        FROM prices
        WHERE origin = ?
          AND destination = ?
          AND currency = ?
    """

    params = [
        origin,
        destination,
        currency
    ]

    if exclude_price is not None:
        query += " AND price != ?"
        params.append(exclude_price)

    query += """
        ORDER BY seen_at DESC
        LIMIT 200
    """

    cur.execute(query, params)

    rows = cur.fetchall()

    conn.close()

    return [float(row[0]) for row in rows]


# =========================================================
# NORMAL FİYAT MOTORU
# =========================================================

def calculate_normal_price(
    origin,
    destination,
    currency,
    current_price=None
):
    history = get_route_history(
        origin,
        destination,
        currency,
        exclude_price=current_price
    )

    if len(history) < 3:
        return {
            "normal_price": None,
            "sample_count": len(history),
            "confidence": "yetersiz_veri"
        }

    # Aşırı uç fiyatların normal fiyatı bozmasını
    # azaltmak için medyan kullanıyoruz.
    median_price = statistics.median(history)

    return {
        "normal_price": round(median_price, 2),
        "sample_count": len(history),
        "confidence": (
            "yuksek" if len(history) >= 20
            else "orta" if len(history) >= 8
            else "dusuk"
        )
    }


# =========================================================
# NORMAL FİYATA GÖRE SAPMA
# =========================================================

def calculate_market_divergence(
    current_price,
    normal_price
):
    if normal_price is None or normal_price <= 0:
        return {
            "drop_percent": 0,
            "market_points": 0
        }

    difference = normal_price - current_price

    drop_percent = (
        difference / normal_price
    ) * 100

    if drop_percent <= 0:
        points = 0

    elif drop_percent >= 70:
        points = 25

    elif drop_percent >= 60:
        points = 22

    elif drop_percent >= 50:
        points = 19

    elif drop_percent >= 40:
        points = 16

    elif drop_percent >= 30:
        points = 13

    elif drop_percent >= 20:
        points = 9

    elif drop_percent >= 10:
        points = 5

    else:
        points = 2

    return {
        "drop_percent": round(drop_percent, 2),
        "market_points": points
    }


# =========================================================
# GENEL HATA SKORU
# =========================================================

def calculate_error_score(
    flight,
    normal_data,
    previous_price=None
):
    score = 0

    normal_price = normal_data["normal_price"]

    # -----------------------------------------------------
    # 1. Tarihsel anomali - 20 puan
    # -----------------------------------------------------

    historical_points = 0

    if normal_price:
        drop = (
            (normal_price - flight["price"])
            / normal_price
        ) * 100

        if drop >= 70:
            historical_points = 20
        elif drop >= 60:
            historical_points = 17
        elif drop >= 50:
            historical_points = 14
        elif drop >= 40:
            historical_points = 11
        elif drop >= 30:
            historical_points = 8
        elif drop >= 20:
            historical_points = 5
        elif drop >= 10:
            historical_points = 2

    score += historical_points

    # -----------------------------------------------------
    # 2. Market divergence - 25 puan
    # -----------------------------------------------------

    market = calculate_market_divergence(
        flight["price"],
        normal_price
    )

    score += market["market_points"]

    # -----------------------------------------------------
    # 3. Ani düşüş - 10 puan
    # -----------------------------------------------------

    sudden_drop_points = 0

    if previous_price and previous_price > 0:
        drop_from_previous = (
            (previous_price - flight["price"])
            / previous_price
        ) * 100

        if drop_from_previous >= 50:
            sudden_drop_points = 10
        elif drop_from_previous >= 30:
            sudden_drop_points = 7
        elif drop_from_previous >= 20:
            sudden_drop_points = 5
        elif drop_from_previous >= 10:
            sudden_drop_points = 2

    score += sudden_drop_points

    return {
        "score": round(min(score, 100), 2),
        "historical_points": historical_points,
        "market_points": market["market_points"],
        "sudden_drop_points": sudden_drop_points,
        "normal_price": normal_price,
        "drop_percent": market["drop_percent"],
        "sample_count": normal_data["sample_count"],
        "confidence": normal_data["confidence"]
    }


# =========================================================
# ÖNCEKİ FİYAT
# =========================================================

def get_previous_route_price(
    origin,
    destination,
    currency,
    current_price
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT price
        FROM prices
        WHERE origin = ?
          AND destination = ?
          AND currency = ?
          AND price != ?
        ORDER BY seen_at DESC
        LIMIT 1
    """, (
        origin,
        destination,
        currency,
        current_price
    ))

    row = cur.fetchone()

    conn.close()

    if row:
        return float(row[0])

    return None


# =========================================================
# DOĞRULAMA
# =========================================================

def verify_flight(flight, departure_date):
    data = ignav_search(
        flight["origin"],
        flight["destination"],
        departure_date
    )

    if not data:
        return {
            "verified": False,
            "reason": "İkinci arama başarısız."
        }

    flights = extract_flights(data)

    for item in flights:
        if item["flight_key"] != flight["flight_key"]:
            continue

        price_match = (
            abs(
                item["price"] -
                flight["price"]
            ) < 0.01
        )

        currency_match = (
            item["currency"] ==
            flight["currency"]
        )

        self_transfer_match = (
            item["self_transfer"] ==
            flight["self_transfer"]
        )

        verified_status = True

        # Fiyat ve temel uçuş bilgileri aynı olmalı.
        if (
            price_match
            and currency_match
            and self_transfer_match
        ):
            return {
                "verified": verified_status,
                "price": item["price"],
                "currency": item["currency"],
                "reason": "Aynı uçuş ve aynı fiyat ikinci aramada görüldü."
            }

    return {
        "verified": False,
        "reason": "Aynı uçuş/fiyat ikinci aramada bulunamadı."
    }


# =========================================================
# DOĞRULAMA KAYDI
# =========================================================

def save_verification(
    flight,
    verification
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO verifications (
            flight_key,
            price,
            currency,
            verified,
            checked_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        flight["flight_key"],
        flight["price"],
        flight["currency"],
        int(verification["verified"]),
        datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram_message(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram bilgileri bulunamadi.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=30
        )

        if response.status_code == 200:
            print("Telegram bildirimi gönderildi.")
            return True

        print(
            "Telegram hatasi:",
            response.status_code,
            response.text[:300]
        )

    except Exception as e:
        print("Telegram bağlantı hatası:", e)

    return False


# =========================================================
# UYARI SEVİYESİ
# =========================================================

def get_alert_level(score):
    if score >= 90:
        return "🔴 KRİTİK HATA FİYATI"

    if score >= 85:
        return "🔴 HATA FİYATI ADAYI"

    if score >= 70:
        return "🟠 ŞÜPHELİ FİYAT"

    if score >= 50:
        return "🟡 İYİ FIRSAT"

    return "⚪ NORMAL"


# =========================================================
# AYNI UYARI TEKRAR GÖNDERİLMESİN
# =========================================================

def alert_already_sent(
    flight_key,
    price
):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM alerts
        WHERE flight_key = ?
          AND price = ?
        LIMIT 1
    """, (
        flight_key,
        price
    ))

    row = cur.fetchone()

    conn.close()

    return row is not None


# =========================================================
# UYARI OLUŞTUR + TELEGRAM
# =========================================================

def create_and_send_alert(
    flight,
    departure_date,
    score_data,
    verification
):
    score = score_data["score"]

    if not verification["verified"]:
        return False

    if score < 50:
        return False

    if alert_already_sent(
        flight["flight_key"],
        flight["price"]
    ):
        print("Bu fiyat için daha önce uyarı gönderilmiş.")
        return False

    level = get_alert_level(score)

    normal_price = score_data["normal_price"]
    drop_percent = score_data["drop_percent"]
    sample_count = score_data["sample_count"]

    message = (
        f"{level}\n\n"
        f"✈️ {flight['origin']} → {flight['destination']}\n"
        f"📅 {departure_date}\n\n"
        f"💰 Fiyat: {flight['price']:.0f} "
        f"{flight['currency']}\n"
    )

    if normal_price:
        message += (
            f"📊 Normal fiyat: {normal_price:.0f} "
            f"{flight['currency']}\n"
            f"📉 Normalden sapma: %{drop_percent:.1f}\n"
            f"🧠 Geçmiş veri: {sample_count} kayıt\n"
        )

    message += (
        f"\n🎯 Hata Skoru: {score:.1f}/100\n"
        f"✈️ Havayolu: {flight['airline']}\n"
        f"🔢 Uçuş: {flight['flight_number']}\n"
        f"⏱️ Süre: {flight['duration_minutes']} dk\n"
        f"🧳 Bagaj: {flight['baggage']}\n"
        f"🔎 Doğrulama: BAŞARILI\n"
    )

    sent = send_telegram_message(message)

    if sent:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO alerts (
                flight_key,
                price,
                currency,
                score,
                alert_level,
                sent_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            flight["flight_key"],
            flight["price"],
            flight["currency"],
            score,
            level,
            datetime.utcnow().isoformat()
        ))

        conn.commit()
        conn.close()

    return sent


# =========================================================
# ROTALAR
# =========================================================

def build_routes():
    routes = []

    origins = settings["airports"]["priority_origins"]
    domestic_destinations = settings["airports"]["domestic_destinations"]
    europe_destinations = settings["airports"]["europe_destinations"]

    if settings["airports"]["domestic_enabled"]:
        for origin in origins:
            for destination in domestic_destinations:
                if origin != destination:
                    routes.append(
                        (origin, destination)
                    )

    if settings["airports"]["europe_enabled"]:
        for origin in origins:
            for destination in europe_destinations:
                if origin != destination:
                    routes.append(
                        (origin, destination)
                    )

    return routes


# =========================================================
# ANA TARAMA
# =========================================================

def main():
    init_db()

    routes = build_routes()

    # İlk aşamada API kullanımını düşük tutuyoruz.
    routes = routes[:5]

    total_saved = 0
    total_verified = 0
    total_alerts = 0

    departure_date = (
        datetime.utcnow() +
        timedelta(days=30)
    ).strftime("%Y-%m-%d")

    print("=" * 60)
    print("UCUS HATA FIYATI RADARI")
    print("=" * 60)

    print(
        f"Taranan rota sayisi: {len(routes)}"
    )

    print(
        f"Arama tarihi: {departure_date}"
    )

    for origin, destination in routes:

        print(
            f"\n🔎 {origin} -> {destination}"
        )

        data = ignav_search(
            origin,
            destination,
            departure_date
        )

        flights = extract_flights(data)

        print(
            f"Bulunan uçuş: {len(flights)}"
        )

        for flight in flights:

            # Önce geçmişe bak.
            normal_data = calculate_normal_price(
                flight["origin"],
                flight["destination"],
                flight["currency"],
                current_price=flight["price"]
            )

            previous_price = get_previous_route_price(
                flight["origin"],
                flight["destination"],
                flight["currency"],
                flight["price"]
            )

            score_data = calculate_error_score(
                flight,
                normal_data,
                previous_price
            )

            print(
                f"  {flight['origin']} -> "
                f"{flight['destination']} | "
                f"{flight['price']:.0f} "
                f"{flight['currency']} | "
                f"Normal: "
                f"{normal_data['normal_price']} | "
                f"Skor: "
                f"{score_data['score']}"
            )

            # Fiyatı geçmişe kaydet.
            save_flight(
                flight,
                departure_date
            )

            total_saved += 1

            # Şimdilik sadece anlamlı adayları
            # ikinci kez doğruluyoruz.
            if score_data["score"] >= 30:

                verification = verify_flight(
                    flight,
                    departure_date
                )

                save_verification(
                    flight,
                    verification
                )

                if verification["verified"]:
                    total_verified += 1

                    if create_and_send_alert(
                        flight,
                        departure_date,
                        score_data,
                        verification
                    ):
                        total_alerts += 1

    print("\n" + "=" * 60)
    print("TARAMA TAMAMLANDI")
    print("=" * 60)

    print(
        f"Kaydedilen fiyat: {total_saved}"
    )

    print(
        f"Doğrulanan uçuş: {total_verified}"
    )

    print(
        f"Gönderilen Telegram uyarısı: {total_alerts}"
    )


if __name__ == "__main__":
    main()
