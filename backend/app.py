import os
import time
import json
import hmac
import hashlib
from urllib.parse import parse_qsl

import requests
from flask import Flask, request, jsonify
from flask_cors import CORS


app = Flask(__name__)
CORS(app)


RAGNER_API_URL = "https://ragnergiftcard.com/api/v1"
RAGNER_API_KEY = os.environ.get("RAGNER_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

REQUIRE_TELEGRAM_AUTH = os.environ.get(
    "REQUIRE_TELEGRAM_AUTH", "true"
).lower() == "true"

MAX_AUTH_AGE = int(os.environ.get("MAX_AUTH_AGE", "86400"))


# Наши товары OPIUM UC
PRODUCTS = {
    60: {"product_id": 50, "price": 79},
    325: {"product_id": 53, "price": 423},
    385: {"product_id": 57, "price": 500},
    660: {"product_id": 58, "price": 823},
    720: {"product_id": 59, "price": 900},
    1320: {"product_id": 61, "price": 1645},
    1800: {"product_id": 62, "price": 2055},
    3850: {"product_id": 65, "price": 3999},
    8100: {"product_id": 67, "price": 7850},
}


def check_telegram_init_data(init_data: str):
    """
    Проверяет Telegram Mini App initData.
    """

    if not TELEGRAM_BOT_TOKEN:
        return False, "TELEGRAM_BOT_TOKEN is not configured"

    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.pop("hash", None)

        if not received_hash:
            return False, "Telegram hash is missing"

        auth_date = int(data.get("auth_date", "0"))

        if auth_date <= 0:
            return False, "Telegram auth_date is missing"

        if time.time() - auth_date > MAX_AUTH_AGE:
            return False, "Telegram initData has expired"

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TELEGRAM_BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return False, "Telegram initData signature is invalid"

        return True, data

    except Exception as e:
        return False, str(e)


def telegram_auth_required():
    """
    Проверяет Telegram Mini App авторизацию.
    """

    if not REQUIRE_TELEGRAM_AUTH:
        return True, None

    init_data = request.headers.get("X-Telegram-Init-Data", "")

    if not init_data:
        return False, "Telegram initData is missing"

    return check_telegram_init_data(init_data)


@app.get("/")
def home():
    return jsonify({
        "success": True,
        "service": "OPIUM UC Ragner Backend",
        "status": "online"
    })


@app.get("/health")
def health():
    return jsonify({
        "success": True,
        "status": "ok"
    })


@app.post("/api/validate-player")
@app.post("/validate-player")
def validate_player():

    # Проверяем Telegram
    auth_ok, auth_data = telegram_auth_required()

    if not auth_ok:
        return jsonify({
            "success": False,
            "error": auth_data
        }), 401

    data = request.get_json(silent=True) or {}

    player_id = str(data.get("player_id", "")).strip()

    # Поддерживаем как UC, так и product_id
    uc_amount = data.get("uc")
    product_id = data.get("product_id")

    if not player_id:
        return jsonify({
            "success": False,
            "error": "Player ID is required"
        }), 400

    # Если frontend отправляет UC
    if uc_amount is not None:
        try:
            uc_amount = int(uc_amount)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "Invalid UC amount"
            }), 400

        product = PRODUCTS.get(uc_amount)

        if not product:
            return jsonify({
                "success": False,
                "error": "Unknown UC package"
            }), 400

        product_id = product["product_id"]

    # Если frontend отправляет product_id
    else:
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            return jsonify({
                "success": False,
                "error": "product_id is required"
            }), 400

    if not RAGNER_API_KEY:
        return jsonify({
            "success": False,
            "error": "RAGNER_API_KEY is not configured"
        }), 500

    headers = {
        "X-API-KEY": RAGNER_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "product_id": product_id,
        "player_id": player_id,
    }

    try:
        response = requests.post(
            f"{RAGNER_API_URL}/validate-player",
            headers=headers,
            json=payload,
            timeout=30,
        )

        try:
            result = response.json()
        except Exception:
            result = {
                "success": False,
                "error": response.text
            }

        if response.status_code >= 400:
            return jsonify({
                "success": False,
                "error": result.get("error", "Ragner validation failed"),
                "ragner_response": result
            }), response.status_code

        # Ragner подтвердил игрока
        return jsonify({
            "success": bool(result.get("success", False)),
            "data": result.get("data", {}),
            "player_id": player_id,
            "product_id": product_id,
        })

    except requests.RequestException as e:
        return jsonify({
            "success": False,
            "error": f"Ragner connection error: {str(e)}"
        }), 502


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
