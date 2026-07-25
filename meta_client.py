"""
meta_client.py — Wrapper para la Meta WhatsApp Business API.

Características:
- Retry exponencial (3 intentos) ante errores transitorios de red o 5xx de Meta.
- Timeout de 10 segundos por intento para no bloquear el worker de gunicorn.
- Logging estructurado de cada intento y resultado.
- Validación HMAC del header X-Hub-Signature-256 (opcional si APP_SECRET está configurado).
"""

import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Importamos solo lo que necesitamos de config para evitar importaciones circulares
from config import API_VERSION, META_TOKEN, PHONE_NUMBER_ID, APP_SECRET

_REQUEST_TIMEOUT: int = 10  # segundos por intento
_MAX_RETRIES: int = 3
_RETRY_BASE_DELAY: float = 0.5  # segundos (exponencial: 0.5 → 1.0 → 2.0)


# ── HMAC Validation ───────────────────────────────────────────────────────────

def validar_firma_meta(payload_bytes: bytes, signature_header: Optional[str]) -> bool:
    """
    Valida el header X-Hub-Signature-256 enviado por Meta.

    Retorna True si:
      - APP_SECRET no está configurado (validación desactivada).
      - La firma coincide con el HMAC calculado.

    Retorna False (→ 401) si APP_SECRET está configurado pero la firma no coincide.
    """
    if not APP_SECRET:
        return True  # Validación desactivada — se emite warning en config.py

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("Petición sin header X-Hub-Signature-256 o formato inválido.")
        return False

    expected = "sha256=" + hmac.new(
        APP_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    # Comparación segura contra timing attacks
    if not hmac.compare_digest(expected, signature_header):
        logger.warning(
            "Firma HMAC inválida. Expected=%s… Received=%s…",
            expected[:20],
            signature_header[:20],
        )
        return False

    return True


# ── Send Message ──────────────────────────────────────────────────────────────

def enviar_whatsapp(
    numero: str,
    texto: str,
    phone_number_id: Optional[str] = None,
) -> bool:
    """
    Envía un mensaje de texto via WhatsApp Business API.

    Args:
        numero: Número del destinatario en formato E.164 (ej. "526144150015").
        texto: Cuerpo del mensaje.
        phone_number_id: ID del número de teléfono de negocio. Usa el default si es None.

    Returns:
        True si el envío fue exitoso, False si todos los reintentos fallaron.
    """
    pid = phone_number_id or PHONE_NUMBER_ID
    url = f"https://graph.facebook.com/{API_VERSION}/{pid}/messages"
    headers = {
        "Authorization": f"Bearer {META_TOKEN}",
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
                    numero,
                    intento,
                    resp.status_code,
                )
                return True

            # 5xx → reintentable
            if resp.status_code >= 500:
                logger.warning(
                    "Meta respondió %d en intento %d/%d. Reintentando…",
                    resp.status_code,
                    intento,
                    _MAX_RETRIES,
                )
            else:
                # 4xx → error del cliente, no reintentar
                logger.error(
                    "Meta rechazó el mensaje. status=%d body=%s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

        except requests.exceptions.Timeout:
            logger.warning("Timeout en intento %d/%d hacia Meta.", intento, _MAX_RETRIES)
        except requests.exceptions.RequestException as exc:
            logger.warning("Error de red en intento %d/%d: %s", intento, _MAX_RETRIES, exc)

        if intento < _MAX_RETRIES:
            delay = _RETRY_BASE_DELAY * (2 ** (intento - 1))
            time.sleep(delay)

    logger.error(
        "Falló el envío a %s tras %d intentos.", numero, _MAX_RETRIES
    )
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
        f"{etiqueta}\n"
        f"Programa: {programa}\n"
        f"Cliente: {nombre_usuario} ({numero_usuario})\n"
        f"Nota: {resumen}"
    )
