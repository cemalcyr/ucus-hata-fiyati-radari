import os
import sys
import sqlite3
import threading
import time
import importlib.util
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string


# ============================================================
# TEMEL AYARLAR
# ============================================================

BASE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")
)

DB_PATH = os.path.join(BASE_DIR, "prices.db")

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
    os.getenv("RADAR_INTERVAL_MINUTES", "30")
)


# ============================================================
# VERİTABANI
# ============================================================

def get_db():
    """
    SQLite veritabanı bağlantısı oluşturur.
    """
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )

    conn.row_factory = sqlite3.Row

    return conn


def get_tables():
    """
    Veritabanındaki tabloları döndürür.
    """
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

        return [row["name"] for row in rows]

    except Exception:
        return []


def get_table_columns(table_name):
    """
    Belirtilen tablonun kolonlarını döndürür.
    """
    try:
        conn = get_db()

        rows = conn.execute(
            f'PRAGMA table_info("{table_name}")'
        ).fetchall()

        conn.close()

        return [row["name"] for row in rows]

    except Exception:
        return []


# ============================================================
# TABLO BULMA
# ============================================================

def find_price_table():
    """
    Fiyat kayıtlarının bulunduğu tabloyu bulmaya çalışır.

    Öncelik:
    1. price_observations
    2. prices
    3. diğer uygun tablolar
    """

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

    # Dinamik arama
    for table in tables:

        if table.startswith("sqlite_"):
            continue

        columns = get_table_columns(table)

        normalized = [
            str(column).lower().replace("_", "")
            for column in columns
        ]

        has_price = any(
            "price" in column
            or "fare" in column
            or "amount" in column
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
    """
    Veritabanındaki son fiyat kayıtlarını mümkün olduğunca
    esnek biçimde okur.
    """

    if not os.path.exists(DB_PATH):
        return []

    table = find_price_table()

    if not table:
        return []

    try:
        conn = get_db()

        columns = get_table_columns(table)

        if not columns:
            conn.close()
            return []

        # Öncelikli tarih/zaman kolonları
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
            (int(limit),)
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
            f"Veritabani okuma hatasi: {repr(e)}",
            flush=True
        )

        return []


# ============================================================
# İSTATİSTİKLER
# ============================================================

def calculate_stats(data):
    """
    Fiyat verileri üzerinden basit istatistikler üretir.
    """

    prices = []

    for row in data:

        for key, value in row.items():

            normalized = normalize_column_name(key)

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
                        prices.append(number)

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
        "average": sum(prices) / len(prices)
    }


# ============================================================
# RADAR MOTORU
# ============================================================

def run_radar_once():
    """
    Kök dizindeki GERÇEK app.py dosyasını çalıştırır.

    ÖNEMLİ:
    Burada kesinlikle:
        import app
    kullanılmıyor.

    Çünkü Render:
        panel/app.py
    dosyasını uygulama olarak çalıştırıyor.

    "import app" kullanılırsa Python yanlışlıkla panel/app.py
    dosyasını tekrar yükleyebilir.

    Bunun yerine kök dizindeki:
        /opt/render/project/src/app.py
    doğrudan dosya yolundan yükleniyor.
    """

    global radar_last_start
    global radar_last_finish
    global radar_last_error

    radar_last_start = datetime.now(
        timezone.utc
    ).isoformat()

    radar_last_error = None

    print("=" * 60, flush=True)
    print(
        "RADAR MOTORU BASLATILIYOR...",
        flush=True
    )
    print(
        f"Proje dizini: {BASE_DIR}",
        flush=True
    )

    old_cwd = os.getcwd()

    try:

        # Kök proje dizinine geçiyoruz.
        os.chdir(BASE_DIR)

        # Kök dizindeki gerçek radar app.py
        radar_file = os.path.join(
            BASE_DIR,
            "app.py"
        )

        if not os.path.isfile(radar_file):

            raise FileNotFoundError(
                "Radar ana dosyasi bulunamadi: "
                + radar_file
            )

        # Dosyayı import app şeklinde değil,
        # doğrudan dosya yolundan yükle.
        spec = importlib.util.spec_from_file_location(
            "radar_engine",
            radar_file
        )

        if spec is None:
            raise ImportError(
                "Radar app.py icin import spec olusturulamadi."
            )

        if spec.loader is None:
            raise ImportError(
                "Radar app.py loader bulunamadi."
            )

        radar_app = importlib.util.module_from_spec(
            spec
        )

        # DİKKAT:
        # Bunu sys.modules["app"] olarak kaydetmiyoruz.
        #
        # Böylece panel/app.py ile isim çakışması olmaz.
        spec.loader.exec_module(
            radar_app
        )

        print(
            "Ana radar app.py yuklendi.",
            flush=True
        )

        # main() kontrolü
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

        print(
            "Radar taramasi baslatiliyor...",
            flush=True
        )

        # GERÇEK RADAR
        radar_app.main()

        print(
            "Radar taramasi basariyla tamamlandi.",
            flush=True
        )

    except Exception as e:

        radar_last_error = repr(e)

        print("=" * 60, flush=True)

        print(
            "!!! RADAR HATASI !!!",
            flush=True
        )

        print(
            repr(e),
            flush=True
        )

        print("=" * 60, flush=True)

    finally:

        radar_last_finish = datetime.now(
            timezone.utc
        ).isoformat()

        try:
            os.chdir(old_cwd)
        except Exception:
            pass


# ============================================================
# RADAR DÖNGÜSÜ
# ============================================================

def radar_loop():
    """
    Radar motorunu ilk açılışta hemen çalıştırır.

    Sonrasında:
        RADAR_INTERVAL_MINUTES
    kadar bekleyip tekrar çalıştırır.
    """

    global radar_started

    radar_started = True

    print("=" * 60, flush=True)
    print(
        "RADAR ARKA PLAN DONGUSU AKTIF.",
        flush=True
    )
    print("=" * 60, flush=True)

    # İlk tarama hemen
    while True:

        # Aynı anda ikinci tarama başlamasın.
        acquired = radar_lock.acquire(
            blocking=False
        )

        if acquired:

            try:
                run_radar_once()

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

        time.sleep(wait_seconds)


# ============================================================
# RADAR THREAD BAŞLATMA
# ============================================================

def start_radar_background():
    """
    Radar thread'ini yalnızca bir kez başlatır.
    """

    global radar_thread

    if radar_thread is not None:

        if radar_thread.is_alive():
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
# ANA PANEL
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
    font-size: 22px;
    font-weight: bold;
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
            Uçuş fiyatlarını otomatik olarak tarayan
            radar ve kontrol paneli.
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
                Sistem durumu
            </div>

            <div
                class="card-value active"
            >
                Aktif
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Radar durumu
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
                Son radar başlangıcı
            </div>

            <div class="card-value">
                {{ radar_last_start or "-" }}
            </div>

        </div>


        <div class="card">

            <div class="card-title">
                Son radar bitişi
            </div>

            <div class="card-value">
                {{ radar_last_finish or "-" }}
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


        <div class="card">

            <div class="card-title">
                Veritabanı
            </div>

            <div class="card-value">

                {% if db_exists %}
                    <span class="active">
                        Hazır
                    </span>
                {% else %}
                    <span class="warning">
                        Henüz oluşmadı
                    </span>
                {% endif %}

            </div>

        </div>

    </div>


    {% if radar_error %}

    <div class="card"
         style="margin-bottom:20px;">

        <div class="card-title">
            Son radar hatası
        </div>

        <pre class="error">
{{ radar_error }}
        </pre>

    </div>

    {% endif %}


    <div class="status-grid">

        <div class="card">

            <div class="card-title">
                IGNAV
            </div>

            <div class="card-value">

                {% if ignav_status == "OK" %}
                    <span class="active">
                        Hazır
                    </span>
                {% elif ignav_status == "BILLING_BLOCKED" %}
                    <span class="error">
                        402 / Billing
                    </span>
                {% elif ignav_status == "NOT_CONFIGURED" %}
                    <span class="warning">
                        API anahtarı yok
                    </span>
                {% else %}
                    <span class="warning">
                        Kontrol bekliyor
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
                {% elif telegram_status == "NOT_CONFIGURED" %}
                    <span class="warning">
                        Yapılandırılmamış
                    </span>
                {% else %}
                    <span class="warning">
                        Kontrol bekliyor
                    </span>
                {% endif %}

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
                En düşük fiyat
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
                Ortalama fiyat
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
                En yüksek fiyat
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
        radar_running = radar_thread.is_alive()

    return render_template_string(
        HTML_TEMPLATE,

        rows=data,

        columns=columns,

        stats=stats,

        radar_running=radar_running,

        radar_last_start=radar_last_start,

        radar_last_finish=radar_last_finish,

        radar_error=radar_last_error,

        interval=RADAR_INTERVAL_MINUTES,

        db_exists=os.path.exists(DB_PATH),

        ignav_status=get_ignav_status(),

        telegram_status=get_telegram_status()
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
        running = radar_thread.is_alive()

    return jsonify({

        "running": running,

        "started": radar_started,

        "last_start": radar_last_start,

        "last_finish": radar_last_finish,

        "last_error": radar_last_error,

        "interval_minutes":
            RADAR_INTERVAL_MINUTES

    })


# ============================================================
# IGNAV DURUMU
# ============================================================

def get_ignav_status():

    api_key = os.getenv(
        "IGNAV_API_KEY",
        ""
    ).strip()

    if not api_key:
        return "NOT_CONFIGURED"

    # Root app'in persistent 402 durumu varsa
    # bunu okumaya çalışıyoruz.
    try:

        conn = get_db()

        tables = get_tables()

        if "api_state" in tables:

            columns = get_table_columns(
                "api_state"
            )

            rows = conn.execute(
                "SELECT * FROM api_state"
            ).fetchall()

            conn.close()

            for row in rows:

                values = [
                    str(row[column])
                    for column in columns
                    if row[column] is not None
                ]

                text = " ".join(
                    values
                ).lower()

                if (
                    "402" in text
                    or "billing" in text
                    or "blocked" in text
                ):
                    return "BILLING_BLOCKED"

        else:
            conn.close()

    except Exception:
        pass

    return "OK"


@app.route("/api/ignav-status")
def api_ignav_status():

    return jsonify({

        "status": get_ignav_status(),

        "configured": bool(
            os.getenv(
                "IGNAV_API_KEY",
                ""
            ).strip()
        )

    })


# ============================================================
# TELEGRAM DURUMU
# ============================================================

def get_telegram_status():

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        ""
    ).strip()

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID",
        ""
    ).strip()

    if not token or not chat_id:
        return "NOT_CONFIGURED"

    return "OK"


@app.route("/api/telegram-status")
def api_telegram_status():

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        ""
    ).strip()

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID",
        ""
    ).strip()

    return jsonify({

        "status": get_telegram_status(),

        "token_configured": bool(token),

        "chat_id_configured": bool(chat_id)

    })


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():

    running = False

    if radar_thread is not None:
        running = radar_thread.is_alive()

    return jsonify({

        "status": "ok",

        "service": "ucus-hata-fiyati-panel",

        "database": os.path.exists(
            DB_PATH
        ),

        "radar": {

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

        },

        "ignav":
            get_ignav_status(),

        "telegram":
            get_telegram_status()

    })


# ============================================================
# UYGULAMA BAŞLANGICI
# ============================================================

# Gunicorn import ettiğinde radar başlatılır.
#
# Render'da WEB_CONCURRENCY=1 olduğu için tek worker
# üzerinden tek radar thread'i çalışacaktır.

if os.getenv(
    "WERKZEUG_RUN_MAIN"
) in (
    None,
    "true"
):

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
