import os
import sys
import sqlite3
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, render_template, request


# ============================================================
# YOLLAR
# ============================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

DB_PATH = os.path.join(
    BASE_DIR,
    "prices.db"
)

app = Flask(__name__)


# ============================================================
# RADAR DURUMU
# ============================================================

radar_thread = None
radar_lock = threading.Lock()
radar_started = False
radar_last_start = None
radar_last_finish = None
radar_last_error = None


# ============================================================
# DATABASE
# ============================================================

def get_db():
    if not os.path.exists(DB_PATH):
        return None

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def get_tables(conn):
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()

    return [
        row["name"]
        for row in rows
    ]


def get_table_columns(
    conn,
    table_name
):
    safe_name = table_name.replace(
        '"',
        '""'
    )

    rows = conn.execute(
        f'PRAGMA table_info("{safe_name}")'
    ).fetchall()

    return [
        row["name"]
        for row in rows
    ]


def find_price_table(conn):
    tables = get_tables(conn)

    if not tables:
        return None

    preferred = [
        "prices",
        "price_observations",
        "price_history",
        "price_history_v2",
        "fares",
        "flights",
        "results",
    ]

    for name in preferred:
        if name in tables:
            return name

    best_table = None
    best_score = -1

    keywords = [
        "price",
        "fiyat",
        "route",
        "rota",
        "origin",
        "destination",
        "departure",
        "score",
        "flight",
    ]

    for table in tables:

        columns = get_table_columns(
            conn,
            table
        )

        score = 0

        for column in columns:

            column_lower = (
                column.lower()
            )

            for keyword in keywords:

                if keyword in column_lower:
                    score += 1

        if score > best_score:
            best_score = score
            best_table = table

    return best_table


def normalize_column_name(
    columns,
    candidates
):
    lower_map = {
        column.lower(): column
        for column in columns
    }

    for candidate in candidates:

        if candidate.lower() in lower_map:
            return lower_map[
                candidate.lower()
            ]

    for column in columns:

        column_lower = column.lower()

        for candidate in candidates:

            if candidate.lower() in column_lower:
                return column

    return None


# ============================================================
# FİYAT VERİLERİ
# ============================================================

def get_price_data(limit=200):

    conn = get_db()

    if conn is None:

        return {
            "available": False,
            "table": None,
            "columns": [],
            "rows": [],
            "message": "prices.db bulunamadi.",
        }

    try:

        table = find_price_table(conn)

        if not table:

            return {
                "available": True,
                "table": None,
                "columns": [],
                "rows": [],
                "message": (
                    "Veritabani tablosu bulunamadi."
                ),
            }

        columns = get_table_columns(
            conn,
            table
        )

        safe_table = table.replace(
            '"',
            '""'
        )

        order_column = normalize_column_name(
            columns,
            [
                "observed_at",
                "recorded_at",
                "seen_at",
                "created_at",
                "checked_at",
                "timestamp",
                "searched_at",
                "date",
                "departure_date",
                "id",
            ],
        )

        if order_column:

            safe_order = (
                order_column.replace(
                    '"',
                    '""'
                )
            )

            query = (
                f'SELECT * FROM "{safe_table}" '
                f'ORDER BY "{safe_order}" DESC '
                f'LIMIT ?'
            )

        else:

            query = (
                f'SELECT * FROM "{safe_table}" '
                f'LIMIT ?'
            )

        rows = conn.execute(
            query,
            (limit,)
        ).fetchall()

        result_rows = []

        for row in rows:

            item = {}

            for column in columns:

                value = row[column]

                if isinstance(
                    value,
                    bytes
                ):

                    value = value.decode(
                        "utf-8",
                        errors="replace"
                    )

                item[column] = value

            result_rows.append(item)

        return {
            "available": True,
            "table": table,
            "columns": columns,
            "rows": result_rows,
            "message": None,
        }

    except Exception as exc:

        return {
            "available": False,
            "table": None,
            "columns": [],
            "rows": [],
            "message": (
                f"Veritabani okunamadi: {exc}"
            ),
        }

    finally:

        conn.close()


# ============================================================
# İSTATİSTİKLER
# ============================================================

def calculate_stats(data):

    rows = data.get(
        "rows",
        []
    )

    columns = data.get(
        "columns",
        []
    )

    if not rows:

        return {
            "total": 0,
            "verified": 0,
            "best_score": 0,
            "best_opportunity": 0,
            "lowest_price": None,
        }

    def get_value(
        row,
        candidates
    ):

        column = normalize_column_name(
            columns,
            candidates
        )

        if column is None:
            return None

        return row.get(column)

    verified = 0
    scores = []
    opportunities = []
    prices = []

    for row in rows:

        status = get_value(
            row,
            [
                "status",
                "verification_status",
                "verify_status",
                "durum",
                "price_status",
            ]
        )

        if status:

            status_text = (
                str(status)
                .lower()
            )

            if (
                "dogrulan" in status_text
                or "verified" in status_text
                or "confirmed" in status_text
            ):
                verified += 1

        score = get_value(
            row,
            [
                "score",
                "total_score",
                "skor",
            ]
        )

        opportunity = get_value(
            row,
            [
                "opportunity",
                "opportunity_score",
                "firsat",
                "firsat_skoru",
            ]
        )

        price = get_value(
            row,
            [
                "price",
                "total_price",
                "amount",
                "fiyat",
            ]
        )

        try:

            if score is not None:
                scores.append(
                    float(score)
                )

        except (
            ValueError,
            TypeError
        ):
            pass

        try:

            if opportunity is not None:
                opportunities.append(
                    float(opportunity)
                )

        except (
            ValueError,
            TypeError
        ):
            pass

        try:

            if price is not None:
                prices.append(
                    float(price)
                )

        except (
            ValueError,
            TypeError
        ):
            pass

    return {
        "total": len(rows),
        "verified": verified,
        "best_score": (
            max(scores)
            if scores
            else 0
        ),
        "best_opportunity": (
            max(opportunities)
            if opportunities
            else 0
        ),
        "lowest_price": (
            min(prices)
            if prices
            else None
        ),
    }


# ============================================================
# RADAR MOTORUNU BAŞLAT
# ============================================================

def run_radar_once():

    global radar_last_start
    global radar_last_finish
    global radar_last_error

    try:

        print(
            "=" * 60
        )

        print(
            "RADAR MOTORU BASLATILIYOR..."
        )

        print(
            "Proje dizini:",
            BASE_DIR
        )

        radar_last_start = (
            datetime.now().isoformat()
        )

        # Ana proje dizinini çalışma dizini yap.
        #
        # Ana app.py şu dosyaları göreli yollarla
        # kullanıyor:
        #
        # config/settings.json
        # prices.db
        #
        os.chdir(BASE_DIR)

        if BASE_DIR not in sys.path:
            sys.path.insert(
                0,
                BASE_DIR
            )

        import app as radar_app

        print(
            "Ana radar app.py yuklendi."
        )

        radar_app.main()

        radar_last_finish = (
            datetime.now().isoformat()
        )

        radar_last_error = None

        print(
            "RADAR CALISMASI TAMAMLANDI."
        )

        print(
            "=" * 60
        )

    except Exception as exc:

        radar_last_error = repr(
            exc
        )

        radar_last_finish = (
            datetime.now().isoformat()
        )

        print(
            "!!! RADAR HATASI !!!"
        )

        print(
            repr(exc)
        )

        print(
            "=" * 60
        )


def radar_loop():

    global radar_started

    with radar_lock:

        if radar_started:
            print(
                "Radar zaten calisiyor."
            )
            return

        radar_started = True

    print(
        "=" * 60
    )

    print(
        "RADAR ARKA PLAN DONGUSU AKTIF."
    )

    print(
        "=" * 60
    )

    # İlk taramayı hemen yap.
    run_radar_once()

    while True:

        try:

            interval_minutes = float(
                os.getenv(
                    "RADAR_INTERVAL_MINUTES",
                    "30"
                )
                or 30
            )

        except (
            TypeError,
            ValueError
        ):

            interval_minutes = 30

        interval_minutes = max(
            1,
            interval_minutes
        )

        print(
            f"Sonraki radar taramasi "
            f"{interval_minutes:g} dakika sonra."
        )

        time.sleep(
            interval_minutes * 60
        )

        run_radar_once()


def start_radar_background():

    global radar_thread

    if (
        radar_thread is not None
        and radar_thread.is_alive()
    ):

        return

    radar_thread = threading.Thread(
        target=radar_loop,
        name="radar-worker",
        daemon=True
    )

    radar_thread.start()

    print(
        "Radar background thread baslatildi."
    )


# ============================================================
# WEB PANEL
# ============================================================

@app.route("/")
def index():

    data = get_price_data(
        200
    )

    stats = calculate_stats(
        data
    )

    return render_template(
        "index.html",
        data=data,
        stats=stats,
        updated_at=datetime.now().strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
    )


@app.route("/api/data")
def api_data():

    try:

        limit = int(
            request.args.get(
                "limit",
                "200"
            )
        )

    except ValueError:

        limit = 200

    limit = max(
        1,
        min(
            limit,
            1000
        )
    )

    data = get_price_data(
        limit
    )

    stats = calculate_stats(
        data
    )

    return jsonify(
        {
            "success": True,
            "database": data,
            "stats": stats,
            "updated_at": datetime.now().isoformat(),
        }
    )


# ============================================================
# RADAR DURUM API
# ============================================================

@app.route("/api/radar-status")
def radar_status():

    return jsonify(
        {
            "success": True,
            "started": radar_started,
            "thread_alive": (
                radar_thread is not None
                and radar_thread.is_alive()
            ),
            "last_start": radar_last_start,
            "last_finish": radar_last_finish,
            "last_error": radar_last_error,
        }
    )


# ============================================================
# IGNAV DURUM API
# ============================================================

@app.route("/api/ignav-status")
def ignav_status():

    result = {
        "available": False,
        "billing_blocked": None,
        "billing_status": None,
        "http_status": None,
        "endpoint": None,
        "updated_at": None,
    }

    try:

        os.chdir(BASE_DIR)

        if BASE_DIR not in sys.path:
            sys.path.insert(
                0,
                BASE_DIR
            )

        import app as radar_app

        radar_app.init_db()

        status = (
            radar_app.ignav_billing_status()
        )

        result.update(
            {
                "available": True,
                "billing_blocked": status[
                    "blocked"
                ],
                "billing_status": status[
                    "status"
                ],
                "http_status": status[
                    "http_status"
                ],
                "endpoint": status[
                    "endpoint"
                ],
                "updated_at": status[
                    "updated_at"
                ],
            }
        )

    except Exception as exc:

        result["error"] = repr(
            exc
        )

    return jsonify(result)


# ============================================================
# TELEGRAM DURUM API
# ============================================================

@app.route("/api/telegram-status")
def telegram_status():

    token_exists = bool(
        os.getenv(
            "TELEGRAM_BOT_TOKEN"
        )
    )

    chat_exists = bool(
        os.getenv(
            "TELEGRAM_CHAT_ID"
        )
    )

    return jsonify(
        {
            "configured": (
                token_exists
                and chat_exists
            ),
            "bot_token_present": token_exists,
            "chat_id_present": chat_exists,
            "telegram_test": os.getenv(
                "TELEGRAM_TEST",
                "0"
            ),
        }
    )


# ============================================================
# HEALTH
# ============================================================

@app.route("/health")
def health():

    db_exists = os.path.exists(
        DB_PATH
    )

    thread_alive = (
        radar_thread is not None
        and radar_thread.is_alive()
    )

    return jsonify(
        {
            "status": "ok",
            "database_exists": db_exists,
            "database": DB_PATH,
            "radar_started": radar_started,
            "radar_thread_alive": thread_alive,
            "radar_last_start": radar_last_start,
            "radar_last_finish": radar_last_finish,
            "radar_last_error": radar_last_error,
        }
    )


# ============================================================
# STARTUP
# ============================================================

# Gunicorn import ettiğinde bu bölüm çalışır.
#
# Web server açılırken radar da arka planda başlar.
#
# RUN_MAIN kontrolü sadece Flask development server
# ile olası çift başlangıcı azaltmak içindir.

if os.getenv(
    "WERKZEUG_RUN_MAIN"
) in (
    None,
    "true"
):

    start_radar_background()


# ============================================================
# DOĞRUDAN ÇALIŞTIRMA
# ============================================================

if __name__ == "__main__":

    port = int(
        os.getenv(
            "PORT",
            "5000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
