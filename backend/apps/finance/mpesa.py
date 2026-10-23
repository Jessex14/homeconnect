import base64
import hashlib
import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.cache import cache


class MpesaGatewayError(Exception):
    pass


def _fernet():
    key = settings.MPESA_CREDENTIALS_ENCRYPTION_KEY
    if not key:
        if not settings.DEBUG:
            raise MpesaGatewayError("M-Pesa credential encryption is not configured.")
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()).decode("ascii")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise MpesaGatewayError("M-Pesa credential encryption key is invalid.") from exc


def encrypt_secret(value):
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value):
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise MpesaGatewayError("Stored M-Pesa credentials cannot be decrypted. Check the encryption key.") from exc


def callback_url(token):
    base = settings.MPESA_CALLBACK_BASE_URL
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc:
        raise MpesaGatewayError("Set MPESA_CALLBACK_BASE_URL to a public HTTPS address before requesting payments.")
    return f"{base}/api/v1/payment-callbacks/mpesa/{token}"


def _base_url(environment):
    return "https://sandbox.safaricom.co.ke" if environment == "sandbox" else "https://api.safaricom.co.ke"


def _json_request(url, *, method="GET", body=None, headers=None, timeout=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout or settings.MPESA_HTTP_TIMEOUT) as response:
            payload = response.read(1024 * 1024).decode("utf-8")
    except HTTPError as exc:
        payload = exc.read(64 * 1024).decode("utf-8", errors="replace")
        try:
            detail = json.loads(payload).get("errorMessage") or json.loads(payload).get("ResponseDescription")
        except (ValueError, AttributeError):
            detail = ""
        raise MpesaGatewayError(detail or f"Safaricom returned HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise MpesaGatewayError("Could not reach Safaricom. Try again after checking the connection.") from exc
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise MpesaGatewayError("Safaricom returned an unreadable response.") from exc


def _access_token(account, consumer_key, consumer_secret):
    cache_key = "mpesa:oauth:" + hashlib.sha256(f"{account.environment}:{consumer_key}:{consumer_secret}".encode("utf-8")).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return cached
    credentials = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode("utf-8")).decode("ascii")
    response = _json_request(
        f"{_base_url(account.environment)}/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {credentials}", "Accept": "application/json"},
    )
    token = response.get("access_token")
    if not token:
        raise MpesaGatewayError("Safaricom did not return an access token. Check the Daraja app credentials.")
    cache.set(cache_key, token, timeout=3400)
    return token


def initiate_stk_push(account, attempt):
    consumer_key = decrypt_secret(account.consumer_key_encrypted)
    consumer_secret = decrypt_secret(account.consumer_secret_encrypted)
    passkey = decrypt_secret(account.passkey_encrypted)
    token = _access_token(account, consumer_key, consumer_secret)
    timestamp = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y%m%d%H%M%S")
    password = base64.b64encode(f"{account.business_shortcode}{passkey}{timestamp}".encode("utf-8")).decode("ascii")
    transaction_type = "CustomerPayBillOnline" if account.merchant_type == "paybill" else "CustomerBuyGoodsOnline"
    payload = {
        "BusinessShortCode": account.business_shortcode,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": transaction_type,
        "Amount": attempt.amount,
        "PartyA": attempt.phone_number,
        "PartyB": account.shortcode,
        "PhoneNumber": attempt.phone_number,
        "CallBackURL": callback_url(attempt.callback_token),
        "AccountReference": f"HC{attempt.pk.hex[:10]}",
        "TransactionDesc": "HomeConnect",
    }
    response = _json_request(
        f"{_base_url(account.environment)}/mpesa/stkpush/v1/processrequest",
        method="POST",
        body=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
    )
    if str(response.get("ResponseCode")) != "0" or not response.get("CheckoutRequestID"):
        raise MpesaGatewayError(response.get("errorMessage") or response.get("ResponseDescription") or "Safaricom did not accept the payment request.")
    return response
