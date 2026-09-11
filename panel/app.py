import os
import sqlite3
import threading
import time
import traceback
import importlib.util
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string


# ============================================================
# TEMEL AYARLAR
# ============================================================

BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

DB_PATH = os.path.join(
    BASE_DIR,
    "prices.db"
)

app = Flask(__name__)


# ============================================================
# RADAR DURUM DEĞİŞKENLERİ
# ============================================================

radar_thread = None
radar_lock = threading.Lock()

radar_started = False
radar_last_start = None
radar_last_finish = None
radar_last_error = None

RADAR_INTERVAL_MINUTES = int(
    os.getenv(
        "RADAR_INTERVAL_MINUTES",
        "30"
    )
)


# ============================================================
# SAĞLAYICI DURUMU
# ============================================================

def get_serpapi_key():
    return os.getenv(
        "SERPAPI_API_KEY",
        ""
    ).strip()


def get_ignav_key():
    return os.getenv(
        "IGNAV_API_KEY",
        ""
    ).strip()


def get_telegram_token():
    return os.getenv(
        "TELEGRAM_BOT_TOKEN",
        ""
    ).strip()


def get_telegram_chat_id():
    return os.getenv(
        "TELEGRAM_CHAT_ID",
        ""
    ).strip()


def get_provider_status():

    serpapi_configured = bool(
        get_serpapi_key()
    )

    ignav_configured = bool(
        get_ignav_key()
    )

    return {
        "active_provider": (
            "SERPAPI / GOOGLE FLIGHTS"
            if serpapi_configured
            else "SERPAPI API KEY EKSIK"
        ),
        "serpapi": (
            "OK"
            if serpapi_configured
            else "NOT_CONFIGURED"
        ),
        "ignav_legacy": (
            "CONFIGURED"
            if ignav_configured
            else "NOT_CONFIGURED"
        )
    }


# ============================================================
# VERİTABANI
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def get_tables():

    if not os.path.exists(DB_PATH):
        return []

    try:

        conn = get_db()

        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            ORDER BY name
            """
        ).fetchall()

        conn.close()

        return [
            row["name"]
            for row in rows
        ]

    except Exception as e:

        print(
            "Tablo listesi okunamadi:",
            repr(e),
            flush=True
        )

        return []


def get_table_columns(table_name):

    try:

        conn = get_db()

        rows = conn.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()

        conn.close()

        return [
            row["name"]
            for row in rows
        ]

    except Exception:

        return []


# ============================================================
# TABLO BULMA
# ============================================================

def find_price_table():

    tables = get_tables()

    if not tables:
        return None

    preferred_tables = [
        "price_observations",
        "prices",
        "flight_prices",
        "price_data"
    ]

    for table in preferred_tables:

        if table in tables:
            return table

    for table in tables:

        if table.startswith("sqlite_"):
            continue

        columns = get_table_columns(
            table
        )

        normalized = [
            str(column)
            .lower()
            .replace("_", "")
            for column in columns
        ]

        has_price = any(
            (
                "price" in column
                or "fare" in column
                or "amount" in column
            )
            for column in normalized
        )

        if has_price:
            return table

    return None


# ============================================================
# KOLON NORMALİZASYONU
# ============================================================

def normalize_column_name(name):

    return (
        str(name)
        .strip()
        .lower()
        .replace("_", "")
        .replace("-", "")
        .replace(" ", "")
    )


# ============================================================
# FİYAT VERİLERİ
# ============================================================

def get_price_data(limit=100):

    if not os.path.exists(DB_PATH):
        return []

    table = find_price_table()

    if not table:
        return []

    try:

        conn = get_db()

        columns = get_table_columns(
            table
        )

        if not columns:

            conn.close()

            return []

        order_column = None

        preferred_order_columns = [
            "created_at",
            "observed_at",
            "timestamp",
            "searched_at",
            "date",
            "datetime",
            "id"
        ]

        for candidate in preferred_order_columns:

            if candidate in columns:

                order_column = candidate

                break

        if order_column:

            query = f'''
                SELECT *
                FROM "{table}"
                ORDER BY "{order_column}" DESC
                LIMIT ?
            '''

        else:

            query = f'''
                SELECT *
                FROM "{table}"
                LIMIT ?
            '''

        rows = conn.execute(
            query,
            (
                int(limit),
            )
        ).fetchall()

        conn.close()

        result = []

        for row in rows:

            item = {}

            for column in columns:

                try:
                    value = row[column]

                except Exception:
                    value = None

                item[column] = value

            result.append(item)

        return result

    except Exception as e:

        print(
            "Veritabani okuma hatasi:",
            repr(e),
            flush=True
        )

        return []


# ============================================================
# İSTATİSTİKLER
# ============================================================

def calculate_stats(data):

    prices = []

    for row in data:

        for key, value in row.items():

            normalized = normalize_column_name(
                key
            )

            if (
                "price" in normalized
                or "fare" in normalized
                or normalized in (
                    "amount",
                    "total",
                    "totalprice"
                )
            ):

                try:

                    if value is None:
                        continue

                    number = float(value)

                    if number >= 0:
                        prices.append(
                            number
                        )

                    break

                except (
                    TypeError,
                    ValueError
                ):

                    continue

    if not prices:

        return {
            "count": len(data),
            "min": None,
            "max": None,
            "average": None
        }

    return {
        "count": len(data),
        "min": min(prices),
        "max": max(prices),
        "average": (
            sum(prices) /
            len(prices)
        )
    }


# ============================================================
# RADAR MOTORU
# ============================================================

def run_radar_once():

    global radar_last_start
    global radar_last_finish
    global radar_last_error

    radar_last_start = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    radar_last_finish = None
    radar_last_error = None

    print(
        "",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    print(
        "RADAR MOTORU BASLATILIYOR...",
        flush=True
    )

    print(
        f"Proje dizini: {BASE_DIR}",
        flush=True
    )

    print(
        f"Veritabani: {DB_PATH}",
        flush=True
    )

    provider = get_provider_status()

    print(
        "Aktif veri saglayici: "
        f"{provider['active_provider']}",
        flush=True
    )

    print(
        "SERPAPI API key: "
        + (
            "EVET"
            if get_serpapi_key()
            else "HAYIR"
        ),
        flush=True
    )

    print(
        "IGNAV API key legacy: "
        + (
            "EVET"
            if get_ignav_key()
            else "HAYIR"
        ),
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    old_cwd = os.getcwd()

    try:

        # ----------------------------------------------------
        # KÖK PROJE DİZİNİ
        # ----------------------------------------------------

        os.chdir(
            BASE_DIR
        )

        print(
            f"Calisma dizini: {os.getcwd()}",
            flush=True
        )

        # ----------------------------------------------------
        # GERÇEK RADAR APP.PY
        # ----------------------------------------------------

        radar_file = os.path.join(
            BASE_DIR,
            "app.py"
        )

        print(
            f"Radar dosyasi: {radar_file}",
            flush=True
        )

        if not os.path.isfile(
            radar_file
        ):

            raise FileNotFoundError(
                "Radar ana dosyasi bulunamadi: "
                + radar_file
            )

        print(
            "Radar ana dosyasi bulundu.",
            flush=True
        )

        # ----------------------------------------------------
        # DOSYA MODÜLÜNÜ YÜKLE
        # ----------------------------------------------------

        spec = (
            importlib.util
            .spec_from_file_location(
                "radar_engine_v36",
                radar_file
            )
        )

        if spec is None:

            raise ImportError(
                "Radar app.py icin "
                "import spec olusturulamadi."
            )

        if spec.loader is None:

            raise ImportError(
                "Radar app.py loader bulunamadi."
            )

        radar_app = (
            importlib.util
            .module_from_spec(
                spec
            )
        )

        print(
            "Radar modulu yukleniyor...",
            flush=True
        )

        # ----------------------------------------------------
        # KRİTİK:
        # panel/app.py ile kök app.py birbirine karışmasın.
        # ----------------------------------------------------

        spec.loader.exec_module(
            radar_app
        )

        print(
            "Ana radar app.py yuklendi.",
            flush=True
        )

        # ----------------------------------------------------
        # MAIN KONTROLÜ
        # ----------------------------------------------------

        if not hasattr(
            radar_app,
            "main"
        ):

            raise AttributeError(
                "Kok dizindeki app.py icinde "
                "main() fonksiyonu bulunamadi."
            )

        print(
            "Radar main() fonksiyonu bulundu.",
            flush=True
        )

        # ----------------------------------------------------
        # MAIN ÇALIŞTIR
        # ----------------------------------------------------

        print(
            "Radar taramasi baslatiliyor...",
            flush=True
        )

        radar_app.main()

        print(
            "Radar taramasi basariyla tamamlandi.",
            flush=True
        )

        print(
            "=" * 70,
            flush=True
        )

    except Exception as e:

        radar_last_error = (
            f"{type(e).__name__}: {e}"
        )

        print(
            "",
            flush=True
        )

        print(
            "=" * 70,
            flush=True
        )

        print(
            "!!! RADAR HATASI !!!",
            flush=True
        )

        print(
            f"Hata tipi: {type(e).__name__}",
            flush=True
        )

        print(
            f"Hata: {e}",
            flush=True
        )

        print(
            "DETAYLI TRACEBACK:",
            flush=True
        )

        traceback.print_exc()

        print(
            "=" * 70,
            flush=True
        )

    finally:

        radar_last_finish = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

        try:

            os.chdir(
                old_cwd
            )

        except Exception:

            pass


# ============================================================
# RADAR DÖNGÜSÜ
# ============================================================

def radar_loop():

    global radar_started

    radar_started = True

    print(
        "",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    print(
        "RADAR ARKA PLAN DONGUSU AKTIF.",
        flush=True
    )

    print(
        f"Tarama araligi: "
        f"{RADAR_INTERVAL_MINUTES} dakika",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    # --------------------------------------------------------
    # İLK TARAMA HEMEN
    # --------------------------------------------------------

    while True:

        acquired = radar_lock.acquire(
            blocking=False
        )

        if acquired:

            try:

                run_radar_once()

            except Exception as e:

                # run_radar_once zaten hata yakalıyor.
                # Buradaki blok ek güvenliktir.

                print(
                    "Radar loop beklenmeyen hatasi:",
                    repr(e),
                    flush=True
                )

                traceback.print_exc()

            finally:

                radar_lock.release()

        else:

            print(
                "Radar zaten calisiyor, "
                "bu tur atlandi.",
                flush=True
            )

        wait_seconds = max(
            1,
            RADAR_INTERVAL_MINUTES * 60
        )

        print(
            f"Sonraki radar taramasi "
            f"{RADAR_INTERVAL_MINUTES} dakika sonra.",
            flush=True
        )

        try:

            time.sleep(
                wait_seconds
            )

        except Exception as e:

            print(
                "Radar bekleme hatasi:",
                repr(e),
                flush=True
            )

            time.sleep(
                10
            )


# ============================================================
# RADAR THREAD BAŞLATMA
# ============================================================

def start_radar_background():

    global radar_thread

    # --------------------------------------------------------
    # ZATEN ÇALIŞIYORSA TEKRAR BAŞLATMA
    # --------------------------------------------------------

    if radar_thread is not None:

        if radar_thread.is_alive():

            print(
                "Radar thread zaten aktif.",
                flush=True
            )

            return

    radar_thread = threading.Thread(
        target=radar_loop,
        name="radar-background",
        daemon=True
    )

    radar_thread.start()

    print(
        "Radar background thread baslatildi.",
        flush=True
    )


# ============================================================
# HTML
# ============================================================

HTML_TEMPLATE = r"""
<!DOCTYPE html>

<html lang="tr">

<head>

<meta charset="UTF-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>Uçuş Hata Fiyatı Radarı</title>

<style>

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 0;
    background: #f4f6f8;
    color: #17202a;
    font-family:
        Arial,
        Helvetica,
        sans-serif;
}

.container {
    width: min(1400px, 96%);
    margin: 25px auto 50px;
}

.header {
    background: white;
    border-radius: 16px;
    padding: 25px;
    box-shadow:
        0 4px 20px rgba(0,0,0,.06);
    margin-bottom: 20px;
}

.header h1 {
    margin: 0 0 8px;
    font-size: 28px;
}

.header p {
    margin: 0;
    color: #6b7280;
}

.status-grid {
    display: grid;
    grid-template-columns:
        repeat(auto-fit, minmax(220px, 1fr));
    gap: 15px;
    margin-bottom: 20px;
}

.card {
    background: white;
    border-radius: 14px;
    padding: 20px;
    box-shadow:
        0 4px 20px rgba(0,0,0,.05);
}

.card-title {
    color: #6b7280;
    font-size: 14px;
    margin-bottom: 8px;
}

.card-value {
    font-size: 20px;
    font-weight: bold;
    word-break: break-word;
}

.active {
    color: #16803c;
}

.error {
    color: #c62828;
}

.warning {
    color: #b26a00;
}

.info {
    color: #1565c0;
}

.table-card {
    background: white;
    border-radius: 16px;
    padding: 20px;
    box-shadow:
        0 4px 20px rgba(0,0,0,.06);
    overflow-x: auto;
}

.table-card h2 {
    margin-top: 0;
}

table {
    width: 100%;
    border-collapse: collapse;
    min-width: 700px;
}

th,
td {
    text-align: left;
    padding: 11px 10px;
    border-bottom: 1px solid #e5e7eb;
    font-size: 14px;
}

th {
    background: #f8fafc;
    font-weight: bold;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
}

.small {
    font-size: 12px;
    color: #6b7280;
    margin-top: 8px;
}

.empty {
    padding: 30px;
    text-align: center;
    color: #6b7280;
}

.refresh {
    display: inline-block;
    margin-top: 12px;
    padding: 9px 15px;
    border-radius: 8px;
    background: #17202a;
    color: white;
    text-decoration: none;
}

</style>

</head>

<body>

<div class="container">

    <div class="header">

        <h1>
            ✈️ Uçuş Hata Fiyatı Radarı
        </h1>

        <p>
            Google Flights / SerpApi tabanlı
            otomatik uçuş fiyat radarı.
        </p>

        <a
            class="refresh"
            href="/"
        >
            Yenile
        </a>

    </div>


    <div class="status-grid">

        <div class="card">

            <div class="card-title">
                Sistem
            </div>

            <div class="card-value active">
                Aktif
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Radar
            </div>

            <div class="card-value">

                {% if radar_running %}

                    <span class="active">
                        Çalışıyor
                    </span>

                {% elif radar_error %}

                    <span class="error">
                        Hata
                    </span>

                {% else %}

                    <span class="warning">
                        Bekliyor
                    </span>

                {% endif %}

            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Aktif veri sağlayıcı
            </div>

            <div class="card-value info">
                {{ provider_status.active_provider }}
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                SerpApi
            </div>

            <div class="card-value">

                {% if provider_status.serpapi == "OK" %}

                    <span class="active">
                        Hazır
                    </span>

                {% else %}

                    <span class="error">
                        API anahtarı eksik
                    </span>

                {% endif %}

            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Telegram
            </div>

            <div class="card-value">

                {% if telegram_status == "OK" %}

                    <span class="active">
                        Hazır
                    </span>

                {% else %}

                    <span class="warning">
                        Yapılandırılmamış
                    </span>

                {% endif %}

            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Tarama aralığı
            </div>

            <div class="card-value">
                {{ interval }} dakika
            </div>

        </div>

    </div>


    {% if radar_error %}

    <div
        class="card"
        style="margin-bottom:20px;"
    >

        <div class="card-title">
            Son radar hatası
        </div>

        <pre class="error">{{ radar_error }}</pre>

    </div>

    {% endif %}


    <div class="status-grid">

        <div class="card">

            <div class="card-title">
                Son başlangıç
            </div>

            <div class="card-value">
                {{ radar_last_start or "-" }}
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Son bitiş
            </div>

            <div class="card-value">
                {{ radar_last_finish or "-" }}
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Fiyat kayıtları
            </div>

            <div class="card-value">
                {{ stats.count }}
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                En düşük
            </div>

            <div class="card-value">

                {% if stats.min is not none %}

                    {{ "%.2f"|format(stats.min) }}

                {% else %}

                    -

                {% endif %}

            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Ortalama
            </div>

            <div class="card-value">

                {% if stats.average is not none %}

                    {{ "%.2f"|format(stats.average) }}

                {% else %}

                    -

                {% endif %}

            </div>

        </div>


        <div class="card">

            <div class="card-title">
                En yüksek
            </div>

            <div class="card-value">

                {% if stats.max is not none %}

                    {{ "%.2f"|format(stats.max) }}

                {% else %}

                    -

                {% endif %}

            </div>

        </div>

    </div>


    <div class="table-card">

        <h2>
            Son fiyat kayıtları
        </h2>

        {% if rows %}

        <table>

            <thead>

                <tr>

                    {% for column in columns %}

                    <th>
                        {{ column }}
                    </th>

                    {% endfor %}

                </tr>

            </thead>

            <tbody>

                {% for row in rows %}

                <tr>

                    {% for column in columns %}

                    <td>
                        {{ row.get(column, "") }}
                    </td>

                    {% endfor %}

                </tr>

                {% endfor %}

            </tbody>

        </table>

        {% else %}

        <div class="empty">

            Henüz fiyat kaydı bulunmuyor.

            <div class="small">
                Radar ilk başarılı taramayı tamamladığında
                kayıtlar burada görünecektir.
            </div>

        </div>

        {% endif %}

    </div>

</div>

</body>

</html>
"""


# ============================================================
# ANA SAYFA
# ============================================================

@app.route("/")
def index():

    data = get_price_data(
        limit=100
    )

    stats = calculate_stats(
        data
    )

    columns = []

    if data:

        columns = list(
            data[0].keys()
        )

    radar_running = False

    if radar_thread is not None:

        radar_running = (
            radar_thread.is_alive()
        )

    return render_template_string(

        HTML_TEMPLATE,

        rows=data,

        columns=columns,

        stats=stats,

        radar_running=radar_running,

        radar_last_start=
            radar_last_start,

        radar_last_finish=
            radar_last_finish,

        radar_error=
            radar_last_error,

        interval=
            RADAR_INTERVAL_MINUTES,

        db_exists=
            os.path.exists(
                DB_PATH
            ),

        provider_status=
            get_provider_status(),

        telegram_status=
            get_telegram_status()

    )


# ============================================================
# API - VERİLER
# ============================================================

@app.route("/api/data")
def api_data():

    data = get_price_data(
        limit=100
    )

    return jsonify({

        "success": True,

        "count": len(data),

        "data": data

    })


# ============================================================
# API - RADAR DURUMU
# ============================================================

@app.route("/api/radar-status")
def api_radar_status():

    running = False

    if radar_thread is not None:

        running = (
            radar_thread.is_alive()
        )

    return jsonify({

        "running": running,

        "started": radar_started,

        "last_start":
            radar_last_start,

        "last_finish":
            radar_last_finish,

        "last_error":
            radar_last_error,

        "interval_minutes":
            RADAR_INTERVAL_MINUTES

    })


# ============================================================
# SERPAPI DURUMU
# ============================================================

@app.route("/api/serpapi-status")
def api_serpapi_status():

    configured = bool(
        get_serpapi_key()
    )

    return jsonify({

        "provider":
            "SERPAPI / GOOGLE FLIGHTS",

        "configured":
            configured,

        "status":
            "OK"
            if configured
            else "NOT_CONFIGURED"

    })


# ============================================================
# IGNAV DURUMU
# ============================================================

def get_ignav_status():

    api_key = get_ignav_key()

    if not api_key:

        return "NOT_CONFIGURED"

    # --------------------------------------------------------
    # IGNAV artık radar sağlayıcısı değildir.
    #
    # Eski anahtar Render'da kalabilir.
    # Bu nedenle burada yalnızca LEGACY olarak gösteriyoruz.
    # --------------------------------------------------------

    return "LEGACY_CONFIGURED"


@app.route("/api/ignav-status")
def api_ignav_status():

    return jsonify({

        "status":
            get_ignav_status(),

        "configured":
            bool(get_ignav_key()),

        "active_provider":
            "SERPAPI / GOOGLE FLIGHTS"

    })


# ============================================================
# TELEGRAM DURUMU
# ============================================================

def get_telegram_status():

    token = get_telegram_token()

    chat_id = get_telegram_chat_id()

    if not token or not chat_id:

        return "NOT_CONFIGURED"

    return "OK"


@app.route("/api/telegram-status")
def api_telegram_status():

    token = get_telegram_token()

    chat_id = get_telegram_chat_id()

    return jsonify({

        "status":
            get_telegram_status(),

        "token_configured":
            bool(token),

        "chat_id_configured":
            bool(chat_id)

    })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    running = False

    if radar_thread is not None:

        running = (
            radar_thread.is_alive()
        )

    provider = get_provider_status()

    return jsonify({

        "status": "ok",

        "service":
            "ucus-hata-fiyati-panel",

        "database":
            os.path.exists(DB_PATH),

        "provider":
            provider,

        "radar": {

            "running":
                running,

            "started":
                radar_started,

            "last_start":
                radar_last_start,

            "last_finish":
                radar_last_finish,

            "last_error":
                radar_last_error,

            "interval_minutes":
                RADAR_INTERVAL_MINUTES

        },

        "telegram":
            get_telegram_status()

    })


# ============================================================
# UYGULAMA BAŞLANGICI
# ============================================================

def should_start_background_radar():

    # --------------------------------------------------------
    # Flask development server için:
    # WERKZEUG_RUN_MAIN = "true" olan gerçek child process'tir.
    #
    # Gunicorn için bu değişken normalde yoktur.
    # --------------------------------------------------------

    werkzeug_main = os.getenv(
        "WERKZEUG_RUN_MAIN"
    )

    if werkzeug_main is None:

        return True

    return werkzeug_main == "true"


if should_start_background_radar():

    print(
        "=" * 70,
        flush=True
    )

    print(
        "PANEL APP V3.6 YUKLENDI.",
        flush=True
    )

    print(
        "SERPAPI:",
        (
            "HAZIR"
            if get_serpapi_key()
            else "API KEY EKSIK"
        ),
        flush=True
    )

    print(
        "TELEGRAM:",
        (
            "HAZIR"
            if get_telegram_token()
            and get_telegram_chat_id()
            else "EKSIK"
        ),
        flush=True
    )

    print(
        f"RADAR INTERVAL: "
        f"{RADAR_INTERVAL_MINUTES} dakika",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    start_radar_background()


# ============================================================
# LOKAL ÇALIŞTIRMA
# ============================================================

if __name__ == "__main__":

    app.run(

        host="0.0.0.0",

        port=int(
            os.getenv(
                "PORT",
                "5000"
            )
        ),

        debug=False

    )
