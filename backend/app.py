import os
import time
import json
import hmac
import hashlib
from uuid import uuid4
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


# ============================================================
# SETTINGS
# ============================================================

RAGNER_API_URL = "https://ragnergiftcard.com/api/v1"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
RAGNER_API_KEY = os.environ.get("RAGNER_API_KEY", "")

REQUIRE_TELEGRAM_AUTH = (
    os.environ.get("REQUIRE_TELEGRAM_AUTH", "true").lower() == "true"
)

try:
    MAX_AUTH_AGE = int(os.environ.get("MAX_AUTH_AGE", "86400"))
except ValueError:
    MAX_AUTH_AGE = 86400


# ============================================================
# PUBG MOBILE PRODUCTS
# ============================================================

PRODUCTS = {
    60: {
        "product_id": 50,
        "price": 79,
    },
    325: {
        "product_id": 53,
        "price": 423,
    },
    385: {
        "product_id": 57,
        "price": 500,
    },
    660: {
        "product_id": 58,
        "price": 823,
    },
    720: {
        "product_id": 59,
        "price": 900,
    },
    1320: {
        "product_id": 61,
        "price": 1645,
    },
    1800: {
        "product_id": 62,
        "price": 2055,
    },
    3850: {
        "product_id": 65,
        "price": 3999,
    },
    8100: {
        "product_id": 67,
        "price": 7850,
    },
}


# ============================================================
# TELEGRAM MINI APP AUTH
# ============================================================

def validate_telegram_init_data(init_data):
    """
    Проверяет подпись Telegram Mini App initData.
    """

    if not init_data:
        return False, None

    if not TELEGRAM_BOT_TOKEN:
        return False, None

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return False, None

        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TELEGRAM_BOT_TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return False, None

        auth_date = parsed.get("auth_date")

        if auth_date:
            try:
                auth_date = int(auth_date)

                if time.time() - auth_date > MAX_AUTH_AGE:
                    return False, None

            except ValueError:
                return False, None

        user_data = None

        if parsed.get("user"):
            try:
                user_data = json.loads(parsed["user"])
            except Exception:
                user_data = None

        return True, user_data

    except Exception:
        return False, None


def get_init_data_from_request():
    """
    Получает Telegram initData из заголовка или тела запроса.
    """

    init_data = request.headers.get("X-Telegram-Init-Data")

    if init_data:
        return init_data

    try:
        body = request.get_json(silent=True) or {}

        init_data = body.get("initData")

        if init_data:
            return init_data

    except Exception:
        pass

    return None


# ============================================================
# RAGNER
# ============================================================

def ragner_headers():
    return {
        "X-API-KEY": RAGNER_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def extract_nickname(data):
    """
    Достаёт настоящий никнейм из ответа сервиса.

    Никаких искусственных Player_XXXX здесь нет.
    """

    if not isinstance(data, dict):
        return None

    # Основной ожидаемый вариант:
    # {
    #   "success": true,
    #   "data": {
    #       "nickname": "PlayerOne"
    #   }
    # }

    nested = data.get("data")

    if isinstance(nested, dict):

        nickname = nested.get("nickname")

        if nickname:
            return str(nickname).strip()

        nickname = nested.get("player_name")

        if nickname:
            return str(nickname).strip()

        nickname = nested.get("username")

        if nickname:
            return str(nickname).strip()

        player = nested.get("player")

        if isinstance(player, dict):
            nickname = player.get("nickname")

            if nickname:
                return str(nickname).strip()

    nickname = data.get("nickname")

    if nickname:
        return str(nickname).strip()

    nickname = data.get("player_name")

    if nickname:
        return str(nickname).strip()

    nickname = data.get("username")

    if nickname:
        return str(nickname).strip()

    return None


def validate_player_with_ragner(product_id, player_id):
    """
    Проверяет Player ID через сервис проверки.
    """

    if not RAGNER_API_KEY:
        return False, None, "service_unavailable"

    payload = {
        "product_id": product_id,
        "player_id": player_id,
    }

    try:
        response = requests.post(
            f"{RAGNER_API_URL}/validate-player",
            headers=ragner_headers(),
            json=payload,
            timeout=20,
        )

    except requests.RequestException:
        return False, None, "service_unavailable"

    try:
        data = response.json()
    except Exception:
        return False, None, "service_unavailable"

    if response.status_code < 200 or response.status_code >= 300:
        return False, None, "player_not_found"

    if not data.get("success"):
        return False, None, "player_not_found"

    nickname = extract_nickname(data)

    # Если сервис подтвердил игрока, но настоящий ник не пришёл,
    # НЕ показываем пользователю выдуманный ник.
    if not nickname:
        return False, None, "nickname_unavailable"

    return True, nickname, None


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "success": True
    })


# ============================================================
# PLAYER VALIDATION
# ============================================================

@app.route("/api/validate-player", methods=["POST"])
def validate_player():

    # --------------------------------------------------------
    # Telegram authentication
    # --------------------------------------------------------

    if REQUIRE_TELEGRAM_AUTH:

        init_data = get_init_data_from_request()

        valid, telegram_user = validate_telegram_init_data(init_data)

        if not valid:
            return jsonify({
                "success": False,
                "error": "auth_required"
            }), 401

    else:
        telegram_user = None

    # --------------------------------------------------------
    # Request body
    # --------------------------------------------------------

    try:
        body = request.get_json(silent=True) or {}
    except Exception:
        body = {}

    player_id = str(body.get("player_id", "")).strip()

    # Можно передавать как количество UC,
    # так и product_id.
    uc_amount = body.get("uc")
    product_id = body.get("product_id")

    # --------------------------------------------------------
    # Validate Player ID
    # --------------------------------------------------------

    if not player_id:
        return jsonify({
            "success": False,
            "error": "invalid_player_id",
            "message": "Player ID не указан"
        }), 400

    # Не разрешаем слишком длинные значения.
    if len(player_id) > 64:
        return jsonify({
            "success": False,
            "error": "invalid_player_id",
            "message": "Player ID не прошёл проверку"
        }), 400

    # --------------------------------------------------------
    # Find product
    # --------------------------------------------------------

    selected_product = None

    if uc_amount is not None:

        try:
            uc_amount = int(uc_amount)
        except (ValueError, TypeError):
            uc_amount = None

        if uc_amount in PRODUCTS:
            selected_product = PRODUCTS[uc_amount]

    if selected_product is None and product_id is not None:

        try:
            product_id = int(product_id)
        except (ValueError, TypeError):
            product_id = None

        if product_id:
            for product in PRODUCTS.values():

                if product["product_id"] == product_id:
                    selected_product = product
                    break

    if selected_product is None:
        return jsonify({
            "success": False,
            "error": "invalid_product"
        }), 400

    # --------------------------------------------------------
    # Real validation
    # --------------------------------------------------------

    success, nickname, error_code = validate_player_with_ragner(
        selected_product["product_id"],
        player_id
    )

    if not success:

        if error_code == "nickname_unavailable":

            return jsonify({
                "success": False,
                "error": "nickname_unavailable",
                "message": "Не удалось получить данные игрока"
            }), 422

        if error_code == "service_unavailable":

            return jsonify({
                "success": False,
                "error": "service_unavailable",
                "message": "Сервис проверки временно недоступен"
            }), 503

        return jsonify({
            "success": False,
            "error": "player_not_found",
            "message": "Player ID не прошёл проверку"
        }), 422

    # --------------------------------------------------------
    # Success
    # --------------------------------------------------------

    return jsonify({
        "success": True,
        "data": {
            "nickname": nickname,
            "player_id": player_id,
            "product_id": selected_product["product_id"]
        }
    })


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "success": False,
        "error": "not_found"
    }), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({
        "success": False,
        "error": "method_not_allowed"
    }), 405


@app.errorhandler(500)
def internal_error(error):
    return jsonify({
        "success": False,
        "error": "internal_error"
    }), 500


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
