import os
import json
import sqlite3
import hashlib
import requests
from datetime import date, timedelta, datetime, timezone

API_KEY = os.environ.get("IGNAV_API_KEY")

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)

API_URL = "https://ignav.com/api"

SETTINGS_FILE = "config/settings.json"
DATABASE_FILE = "prices.db"


# ============================================================
# AYARLAR
# ============================================================

def load_settings():

    with open(
        SETTINGS_FILE,
        "r",
        encoding="utf-8"
    ) as file:

        return json.load(file)


# ============================================================
# VERİTABANI
# ============================================================

def create_database():

    connection = sqlite3.connect(
        DATABASE_FILE
    )

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

    connection.execute("""
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_key TEXT NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            departure_date TEXT NOT NULL,
            verified INTEGER NOT NULL,
            original_price REAL,
            verification_price REAL,
            currency TEXT,
            status TEXT,
            reason TEXT,
            checked_at TEXT NOT NULL
        )
    """)

    connection.execute("""
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

    connection.commit()

    columns = [
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(prices)"
        ).fetchall()
    ]

    if "flight_key" not in columns:

        connection.execute(
            "ALTER TABLE prices "
            "ADD COLUMN flight_key TEXT"
        )

        connection.commit()

    return connection


# ============================================================
# ROTALAR
# ============================================================

def build_routes(settings):

    routes = []

    domestic = settings[
        "airports"
    ][
        "domestic_destinations"
    ]

    europe = settings[
        "airports"
    ][
        "europe_destinations"
    ]

    if settings[
        "airports"
    ][
        "domestic_enabled"
    ]:

        for destination in domestic:

            if destination != "SZF":

                routes.append(
                    ("SZF", destination)
                )

        for origin in domestic:

            if origin != "SZF":

                routes.append(
                    (origin, "SZF")
                )

    if settings[
        "airports"
    ][
        "europe_enabled"
    ]:

        for destination in europe:

            routes.append(
                ("SZF", destination)
            )

        for origin in europe:

            routes.append(
                (origin, "SZF")
            )

    return routes


# ============================================================
# IGNAV SORGUSU
# ============================================================

def search_flight(
    origin,
    destination,
    departure_date,
    settings
):

    passengers = settings[
        "passengers"
    ]

    connections = settings[
        "connections"
    ]

    if not API_KEY:

        raise RuntimeError(
            "IGNAV_API_KEY bulunamadi."
        )

    response = requests.post(

        f"{API_URL}/fares/one-way",

        headers={
            "X-Api-Key": API_KEY,
            "Content-Type": "application/json"
        },

        json={

            "origin": origin,

            "destination": destination,

            "departure_date":
                departure_date,

            "adults":
                passengers["adults"],

            "children":
                passengers["children"],

            "infants_on_lap":
                passengers["infants"],

            "cabin_class": (
                "business"
                if settings["cabin"]["business"]
                and not settings["cabin"]["economy"]
                else "economy"
            ),

            "max_stops":
                connections[
                    "max_connections"
                ],

            "allow_self_transfer":
                connections[
                    "self_transfer"
                ],

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


# ============================================================
# UÇUŞLARI AYIKLAMA
# ============================================================

def extract_flights(data):

    if isinstance(data, list):

        return data

    if not isinstance(data, dict):

        return []

    if isinstance(
        data.get("itineraries"),
        list
    ):

        return data["itineraries"]

    for key in [
        "fares",
        "results",
        "flights",
        "data"
    ]:

        value = data.get(key)

        if isinstance(value, list):

            return value

    return []


# ============================================================
# FİYAT
# ============================================================

def get_price(flight):

    price_info = flight.get(
        "price",
        {}
    )

    if isinstance(
        price_info,
        dict
    ):

        return price_info.get(
            "amount"
        )

    if isinstance(
        price_info,
        (int, float)
    ):

        return price_info

    return None


def get_currency(flight):

    price_info = flight.get(
        "price",
        {}
    )

    if isinstance(
        price_info,
        dict
    ):

        return price_info.get(
            "currency"
        )

    return None


# ============================================================
# HAVAYOLU
# ============================================================

def get_airline(flight):

    outbound = flight.get(
        "outbound",
        {}
    )

    segments = outbound.get(
        "segments",
        []
    )

    if not segments:

        return outbound.get(
            "carrier"
        )

    return (

        segments[0].get(
            "marketing_carrier_code"
        )

        or outbound.get(
            "carrier"
        )

        or segments[0].get(
            "operating_carrier_name"
        )
    )


# ============================================================
# UÇUŞ NUMARASI
# ============================================================

def get_flight_number(flight):

    segments = flight.get(
        "outbound",
        {}
    ).get(
        "segments",
        []
    )

    if not segments:

        return None

    return segments[0].get(
        "flight_number"
    )


# ============================================================
# SÜRE
# ============================================================

def get_duration(flight):

    outbound = flight.get(
        "outbound",
        {}
    )

    return (

        outbound.get(
            "duration_minutes"
        )

        or flight.get(
            "duration_minutes"
        )
    )


# ============================================================
# BAGAJ
# ============================================================

def get_checked_bags(flight):

    bags = flight.get(
        "bags"
    )

    if isinstance(
        bags,
        dict
    ):

        if bags.get(
            "checked"
        ) is not None:

            return bags.get(
                "checked"
            )

    baggage = flight.get(
        "baggage"
    )

    if isinstance(
        baggage,
        dict
    ):

        return baggage.get(
            "checked_bags"
        )

    return None


# ============================================================
# SELF TRANSFER
# ============================================================

def get_self_transfer(flight):

    return bool(
        flight.get(
            "requires_self_transfer",
            False
        )
    )


# ============================================================
# UÇUŞ KİMLİĞİ
# ============================================================

def create_flight_key(
    origin,
    destination,
    departure_date,
    flight
):

    outbound = flight.get(
        "outbound",
        {}
    )

    segments = outbound.get(
        "segments",
        []
    )

    parts = [

        origin,

        destination,

        departure_date,

        str(
            flight.get(
                "cabin_class",
                "economy"
            )
        )
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

    raw_key = "|".join(
        parts
    )

    return hashlib.sha256(
        raw_key.encode(
            "utf-8"
        )
    ).hexdigest()[:32]


# ============================================================
# FİYAT KAYDET
# ============================================================

def save_flight(
    connection,
    origin,
    destination,
    departure_date,
    flight
):

    price = get_price(
        flight
    )

    price_info = flight.get(
        "price",
        {}
    )

    status = None

    if isinstance(
        price_info,
        dict
    ):

        status = price_info.get(
            "status"
        )

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

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?
        )

    """, (

        flight_key,

        origin,

        destination,

        departure_date,

        price,

        get_currency(
            flight
        ),

        get_airline(
            flight
        ),

        get_flight_number(
            flight
        ),

        get_duration(
            flight
        ),

        get_checked_bags(
            flight
        ),

        int(
            get_self_transfer(
                flight
            )
        ),

        status,

        "Ignav",

        datetime.now(
            timezone.utc
        ).isoformat()
    ))

    connection.commit()

    return flight_key


# ============================================================
# FİYAT GEÇMİŞİ
# ============================================================

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

    """, (
        flight_key,
    ))

    return [

        float(row[0])

        for row in cursor.fetchall()
    ]


# ============================================================
# ERROR SCORE
# ============================================================

def calculate_error_score(
    connection,
    flight_key,
    current_price,
    settings
):

    weights = settings[
        "scoring"
    ][
        "error_score"
    ]

    history = get_history(
        connection,
        flight_key
    )

    if len(history) < 3:

        return {

            "total": 0,

            "band":
                "veri yetersiz",

            "historical": 0,

            "sudden_drop": 0,

            "average": None,

            "drop_percent": None,

            "history_count":
                len(history)
        }

    average = (
        sum(history)
        / len(history)
    )

    if average <= 0:

        return {

            "total": 0,

            "band":
                "veri yetersiz",

            "historical": 0,

            "sudden_drop": 0,

            "average": None,

            "drop_percent": None,

            "history_count":
                len(history)
        }

    drop_percent = (

        (average - current_price)
        / average

    ) * 100

    historical_score = min(

        weights[
            "historical_anomaly"
        ],

        max(

            0,

            drop_percent
            / 100
            * weights[
                "historical_anomaly"
            ]
        )
    )

    sudden_drop_score = 0

    previous = history[0]

    if previous > 0:

        sudden_drop = (

            (previous - current_price)
            / previous

        ) * 100

        sudden_drop_score = min(

            weights[
                "sudden_drop"
            ],

            max(

                0,

                sudden_drop
                / 50
                * weights[
                    "sudden_drop"
                ]
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

    thresholds = settings[
        "scoring"
    ][
        "thresholds"
    ]

    if total >= thresholds[
        "high_confidence"
    ]:

        band = (
            "yüksek güvenli hata fiyatı"
        )

    elif total >= thresholds[
        "candidate"
    ]:

        band = "aday"

    elif total >= thresholds[
        "suspicious"
    ]:

        band = "şüpheli"

    elif total >= thresholds[
        "good_deal"
    ]:

        band = "iyi fırsat"

    else:

        band = "normal"

    return {

        "total":
            total,

        "band":
            band,

        "historical":
            round(
                historical_score,
                1
            ),

        "sudden_drop":
            round(
                sudden_drop_score,
                1
            ),

        "average":
            round(
                average,
                2
            ),

        "drop_percent":
            round(
                drop_percent,
                1
            ),

        "history_count":
            len(history)
    }


# ============================================================
# VERIFICATION ENGINE
# ============================================================

def verify_flight(
    origin,
    destination,
    departure_date,
    flight,
    settings
):

    print(
        "  🔎 İkinci sorgu başlatılıyor..."
    )

    data = search_flight(
        origin,
        destination,
        departure_date,
        settings
    )

    if data is None:

        return {

            "verified": False,

            "reason":
                "İkinci sorguda veri alınamadı."
        }

    flights = extract_flights(
        data
    )

    if not flights:

        return {

            "verified": False,

            "reason":
                "İkinci sorguda uçuş bulunamadı."
        }

    original_key = create_flight_key(
        origin,
        destination,
        departure_date,
        flight
    )

    original_price = get_price(
        flight
    )

    original_currency = get_currency(
        flight
    )

    for candidate in flights:

        candidate_key = create_flight_key(
            origin,
            destination,
            departure_date,
            candidate
        )

        if candidate_key != original_key:
            continue

        candidate_price = get_price(
            candidate
        )

        candidate_currency = get_currency(
            candidate
        )

        candidate_status = None

        price_info = candidate.get(
            "price",
            {}
        )

        if isinstance(
            price_info,
            dict
        ):

            candidate_status = (
                price_info.get(
                    "status"
                )
            )

        if candidate_price != original_price:

            return {

                "verified": False,

                "reason":
                    "Aynı uçuş bulundu ancak "
                    "fiyat değişti.",

                "verification_price":
                    candidate_price,

                "currency":
                    candidate_currency,

                "status":
                    candidate_status
            }

        if candidate_currency != original_currency:

            return {

                "verified": False,

                "reason":
                    "Para birimi değişti.",

                "verification_price":
                    candidate_price,

                "currency":
                    candidate_currency,

                "status":
                    candidate_status
            }

        if (
            get_self_transfer(
                candidate
            )
            !=
            get_self_transfer(
                flight
            )
        ):

            return {

                "verified": False,

                "reason":
                    "Aktarma tipi değişti.",

                "verification_price":
                    candidate_price,

                "currency":
                    candidate_currency,

                "status":
                    candidate_status
            }

        if candidate_status == "verified":

            return {

                "verified": True,

                "reason":
                    "Aynı uçuş ve aynı fiyat "
                    "ikinci sorguda doğrulandı.",

                "verification_price":
                    candidate_price,

                "currency":
                    candidate_currency,

                "status":
                    candidate_status
            }

        return {

            "verified": False,

            "reason":
                "Aynı uçuş ve fiyat bulundu "
                "ancak fiyat verified değil.",

            "verification_price":
                candidate_price,

            "currency":
                candidate_currency,

            "status":
                candidate_status
        }

    return {

        "verified": False,

        "reason":
            "İkinci sorguda aynı uçuş bulunamadı."
    }


# ============================================================
# VERIFICATION KAYDI
# ============================================================

def save_verification(
    connection,
    origin,
    destination,
    departure_date,
    flight,
    verification
):

    flight_key = create_flight_key(
        origin,
        destination,
        departure_date,
        flight
    )

    connection.execute("""

        INSERT INTO verifications (

            flight_key,
            origin,
            destination,
            departure_date,
            verified,
            original_price,
            verification_price,
            currency,
            status,
            reason,
            checked_at

        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )

    """, (

        flight_key,

        origin,

        destination,

        departure_date,

        int(
            verification[
                "verified"
            ]
        ),

        get_price(
            flight
        ),

        verification.get(
            "verification_price"
        ),

        verification.get(
            "currency"
        ),

        verification.get(
            "status"
        ),

        verification.get(
            "reason"
        ),

        datetime.now(
            timezone.utc
        ).isoformat()
    ))

    connection.commit()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "  ⚠️ Telegram token bulunamadı."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "  ⚠️ Telegram Chat ID bulunamadı."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    try:

        response = requests.post(

            url,

            json={

                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message,

                "disable_web_page_preview":
                    True
            },

            timeout=30
        )

        if response.ok:

            print(
                "  📱 Telegram alarmı gönderildi."
            )

            return True

        print(
            "  ⚠️ Telegram hatası:",
            response.status_code
        )

        return False

    except Exception as error:

        print(
            "  ⚠️ Telegram bağlantı hatası:",
            error
        )

        return False


# ============================================================
# DAHA ÖNCE ALARM GÖNDERİLDİ Mİ?
# ============================================================

def alert_already_sent(
    connection,
    flight_key,
    price
):

    cursor = connection.execute("""

        SELECT id

        FROM alerts

        WHERE flight_key = ?

          AND price = ?

        LIMIT 1

    """, (

        flight_key,

        price
    ))

    return cursor.fetchone() is not None


# ============================================================
# ALARM SEVİYESİ
# ============================================================

def get_alert_level(
    score
):

    if score >= 90:

        return "🔴 KRİTİK HATA FİYATI"

    if score >= 85:

        return "🔴 HATA FİYATI ADAYI"

    if score >= 70:

        return "🟠 ŞÜPHELİ FİYAT"

    if score >= 50:

        return "🟡 İYİ FIRSAT"

    return None


# ============================================================
# TELEGRAM ALARM MOTORU
# ============================================================

def create_and_send_alert(
    connection,
    origin,
    destination,
    departure_date,
    flight,
    score,
    verification
):

    if not verification[
        "verified"
    ]:

        return False

    if score["total"] < 50:

        return False

    price = get_price(
        flight
    )

    currency = get_currency(
        flight
    )

    flight_key = create_flight_key(
        origin,
        destination,
        departure_date,
        flight
    )

    if alert_already_sent(
        connection,
        flight_key,
        price
    ):

        print(
            "  ℹ️ Bu uçuş/fiyat için alarm "
            "daha önce gönderilmiş."
        )

        return False

    alert_level = get_alert_level(
        score["total"]
    )

    if not alert_level:

        return False

    airline = get_airline(
        flight
    )

    flight_number = get_flight_number(
        flight
    )

    duration = get_duration(
        flight
    )

    bags = get_checked_bags(
        flight
    )

    if bags is None:

        baggage_text = (
            "Bilgi yok"
        )

    else:

        baggage_text = (
            f"{bags} adet"
        )

    if duration:

        hours = duration // 60
        minutes = duration % 60

        duration_text = (
            f"{hours}s {minutes}dk"
        )

    else:

        duration_text = (
            "Bilgi yok"
        )

    average = score[
        "average"
    ]

    drop_percent = score[
        "drop_percent"
    ]

    if average is not None:

        normal_text = (
            f"{average:.0f} TL"
        )

    else:

        normal_text = (
            "Yeterli geçmiş verisi yok"
        )

    if drop_percent is not None:

        drop_text = (
            f"%{drop_percent:.1f}"
        )

    else:

        drop_text = (
            "Hesaplanamadı"
        )

    message = f"""
{alert_level}

✈️ Uçuş Hata Fiyatı Radarı

📍 Rota:
{origin} → {destination}

📅 Tarih:
{departure_date}

💰 Fiyat:
{price:.0f} {currency}

📊 Error Score:
{score["total"]}/100

📈 Normal fiyat:
{normal_text}

📉 Normalden düşüş:
{drop_text}

✈️ Havayolu:
{airline or "Bilinmiyor"}

🔢 Uçuş:
{flight_number or "Bilinmiyor"}

⏱️ Süre:
{duration_text}

🧳 Bagaj:
{baggage_text}

🔎 Doğrulama:
✅ Aynı uçuş + aynı fiyat doğrulandı

🟢 Fiyat durumu:
{verification.get("status") or "verified"}

📝 Neden alarm?
İkinci sorguda aynı uçuş ve aynı fiyat
doğrulandı.
"""

    sent = send_telegram_message(
        message.strip()
    )

    if sent:

        connection.execute("""

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

            flight_key,

            price,

            currency,

            score["total"],

            alert_level,

            datetime.now(
                timezone.utc
            ).isoformat()
        ))

        connection.commit()

        return True

    return False


# ============================================================
# ANA PROGRAM
# ============================================================

def main():

    if not API_KEY:

        raise RuntimeError(
            "IGNAV_API_KEY bulunamadi."
        )

    settings = load_settings()

    connection = create_database()

    routes = build_routes(
        settings
    )

    departure_date = (

        date.today()
        + timedelta(days=30)

    ).isoformat()

    print(
        "======================================"
    )

    print(
        "UCUS HATA FIYATI RADARI"
    )

    print(
        "======================================"
    )

    print()

    print(
        "Tarama tarihi:",
        departure_date
    )

    print(
        "Toplam rota:",
        len(routes)
    )

    print()

    # Şimdilik test amacıyla ilk 5 rota.
    # Sistem oturduktan sonra genişleteceğiz.

    test_routes = routes[:5]

    total_saved = 0

    total_verified = 0

    total_alerts = 0

    for number, (
        origin,
        destination
    ) in enumerate(

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

            print(
                "  Veri alınamadı"
            )

            print()

            continue

        flights = extract_flights(
            data
        )

        print(
            "  Bulunan uçuş:",
            len(flights)
        )

        for flight in flights:

            price = get_price(
                flight
            )

            currency = get_currency(
                flight
            )

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

            verification = {

                "verified": False,

                "reason":
                    "Error Score 30'un altında "
                    "olduğu için doğrulama yapılmadı."

            }

            # ------------------------------------------------
            # VERIFICATION
            # ------------------------------------------------

            if score["total"] >= 30:

                verification = verify_flight(

                    origin,

                    destination,

                    departure_date,

                    flight,

                    settings

                )

                if verification[
                    "verified"
                ]:

                    total_verified += 1

                    print(
                        "  ✅ DOĞRULANDI"
                    )

                else:

                    print(
                        "  ❌ DOĞRULANMADI"
                    )

                print(
                    "  Doğrulama:",
                    verification[
                        "reason"
                    ]
                )

            # ------------------------------------------------
            # FİYATI KAYDET
            # ------------------------------------------------

            save_flight(

                connection,

                origin,

                destination,

                departure_date,

                flight

            )

            # ------------------------------------------------
            # DOĞRULAMAYI KAYDET
            # ------------------------------------------------

            save_verification(

                connection,

                origin,

                destination,

                departure_date,

                flight,

                verification

            )

            total_saved += 1

            # ------------------------------------------------
            # TELEGRAM ALARMI
            # ------------------------------------------------

            if verification[
                "verified"
            ]:

                alert_sent = (
                    create_and_send_alert(

                        connection,

                        origin,

                        destination,

                        departure_date,

                        flight,

                        score,

                        verification
                    )
                )

                if alert_sent:

                    total_alerts += 1

            # ------------------------------------------------
            # EKRAN
            # ------------------------------------------------

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

                "  Verification: "

                +

                (

                    "DOĞRULANDI"

                    if verification[
                        "verified"
                    ]

                    else
                    "DOĞRULANMADI"

                )

            )

            print()

        print()

    connection.close()

    print(
        "======================================"
    )

    print(
        "TARAMA TAMAMLANDI"
    )

    print(
        "======================================"
    )

    print(
        "Kaydedilen fiyat:",
        total_saved
    )

    print(
        "Doğrulanan uçuş:",
        total_verified
    )

    print(
        "Gönderilen Telegram alarmı:",
        total_alerts
    )

    print(
        "Uçuş kimliği sistemi aktif."
    )

    print(
        "Verification Engine aktif."
    )

    print(
        "Telegram Alarm Engine aktif."
    )


# ============================================================
# BAŞLAT
# ============================================================

if __name__ == "__main__":

    main()
