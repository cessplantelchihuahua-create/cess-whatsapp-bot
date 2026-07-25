"""
meta_client.py — Wrapper para la Meta WhatsApp Business API.

- Retry exponencial (3 intentos) ante errores transitorios.
- Timeout de 10 segundos por intento.
- Validación HMAC del header X-Hub-Signature-256.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

from config import API_VERSION, META_TOKEN, PHONE_NUMBER_ID, APP_SECRET

_REQUEST_TIMEOUT: int = 10
_MAX_RETRIES: int = 3
_RETRY_BASE_DELAY: float = 0.5


def validar_firma_meta(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """
    Valida el header X-Hub-Signature-256 enviado por Meta.
    Retorna True si APP_SECRET no está configurado (validación desactivada)
    o si la firma coincide.
    """
    if not APP_SECRET:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Petición sin header X-Hub-Signature-256 o formato inválido.")
        return False

    expected = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        logger.warning(
            "Firma HMAC inválida. Expected=%s… Received=%s…",
            expected[:20],
            signature_header[:20],
        )
        return False

    return True


def enviar_whatsapp(
    numero: str,
    texto: str,
    phone_number_id: Optional[str] = None,
) -> bool:
    """
    Envía un mensaje de texto via WhatsApp Business API con retry exponencial.
    """
    pid = phone_number_id or PHONE_NUMBER_ID
    url = "https://graph.facebook.com/{}/{}/messages".format(API_VERSION, pid)
    headers = {
        "Authorization": "Bearer {}".format(META_TOKEN),
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "text",
        "text": {"body": texto},
    }

    for intento in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                logger.info(
                    "Mensaje enviado a %s (intento %d). status=%d",
                    numero, intento, resp.status_code,
                )
                return True

            if resp.status_code >= 500:
                logger.warning(
                    "Meta respondió %d en intento %d/%d. Reintentando…",
                    resp.status_code, intento, _MAX_RETRIES,
                )
            else:
                logger.error(
                    "Meta rechazó el mensaje. status=%d body=%s",
                    resp.status_code, resp.text[:200],
                )
                return False

        except requests.exceptions.Timeout:
            logger.warning("Timeout en intento %d/%d hacia Meta.", intento, _MAX_RETRIES)
        except requests.exceptions.RequestException as exc:
            logger.warning("Error de red en intento %d/%d: %s", intento, _MAX_RETRIES, exc)

        if intento < _MAX_RETRIES:
            delay = _RETRY_BASE_DELAY * (2 ** (intento - 1))
            time.sleep(delay)

    logger.error("Falló el envío a %s tras %d intentos.", numero, _MAX_RETRIES)
    return False


def armar_notificacion_traspaso(
    etiqueta: str,
    nombre_usuario: str,
    numero_usuario: str,
    programa: str,
    resumen: str,
) -> str:
    """Formatea el mensaje de aviso interno al asesor."""
    return (
        "{}\n"
        "Programa: {}\n"
        "Cliente: {} ({})\n"
        "Nota: {}"
    ).format(etiqueta, programa, nombre_usuario, numero_usuario, resumen)
