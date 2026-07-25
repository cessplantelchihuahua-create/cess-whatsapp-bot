"""
app.py — Bot WhatsApp CESS · Producción en Render

Entry point Flask. Contiene SOLO las rutas HTTP.
Toda la lógica de negocio está en los módulos:
  config.py         — variables de entorno y constantes
  db.py             — historial en PostgreSQL (o SQLite)
  ai_client.py      — wrapper OpenAI Responses API
  meta_client.py    — wrapper Meta WhatsApp API con retry + HMAC
  context_loader.py — carga datosCESS.txt
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask, request, jsonify

import config  # noqa: F401 — dispara validación al arranque
from config import VERIFY_TOKEN, NUMERO_ASESOR, _ETIQUETAS_TRASPASO_LABELS
from db import inicializar_db, guardar_mensaje, obtener_historial, limpiar_historial_antiguo
from ai_client import procesar_mensaje
from meta_client import validar_firma_meta, enviar_whatsapp, armar_notificacion_traspaso

# ── Logging ───────────────────────────────────────────────────────────────────
config.configurar_logging()
logger = logging.getLogger(__name__)

# ── Flask App ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

# Inicializar DB al arrancar (idempotente)
inicializar_db()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health_check():
    """Health check endpoint — Render lo usa para verificar que el servicio está vivo."""
    return "CESS WhatsApp Bot activo ✅", 200


@app.route("/webhook", methods=["GET"])
def verificar_webhook():
    """Verificación de webhook de Meta."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            logger.info("Webhook verificado correctamente.")
            return challenge, 200
        logger.warning("Verificación de webhook fallida. Token recibido: %s", token)
        return "Validación fallida", 403

    return "Mal formato", 400


@app.route("/webhook", methods=["POST"])
def recibir_mensaje():
    """
    Recibe eventos de Meta WhatsApp Business API.
    Retorna 200 inmediatamente (Meta reenvía si tarda > 20s).
    """
    raw_body = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not validar_firma_meta(raw_body, signature):
        logger.warning("Firma HMAC inválida — petición rechazada.")
        return jsonify({"error": "invalid signature"}), 401

    data = request.get_json(silent=True) or {}

    try:
        _procesar_payload(data)
    except Exception as exc:
        logger.exception("Error inesperado procesando payload de Meta: %s", exc)

    return jsonify({"status": "ok"}), 200


# ── Business Logic ────────────────────────────────────────────────────────────

def _procesar_payload(data: dict) -> None:
    """Itera sobre el payload de Meta y procesa cada mensaje de texto."""
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            if change.get("field") != "messages" or value.get("messaging_product") != "whatsapp":
                continue

            contacts = value.get("contacts", [])
            nombre_usuario = contacts[0].get("profile", {}).get("name", "Usuario") if contacts else "Usuario"
            phone_number_id = value.get("metadata", {}).get("phone_number_id")

            for message in value.get("messages", []):
                _procesar_mensaje_individual(message, nombre_usuario, phone_number_id)


def _procesar_mensaje_individual(
    message: dict,
    nombre_usuario: str,
    phone_number_id: Optional[str],
) -> None:
    """Procesa un único mensaje entrante de WhatsApp."""
    numero_usuario: str = message.get("from", "")

    texto_usuario: Optional[str] = None
    if message.get("type") == "text":
        texto_usuario = message.get("text", {}).get("body")

    ad_context = ""
    referral = message.get("referral")
    if referral:
        headline = referral.get("headline", "")
        body = referral.get("body", "")
        source_id = referral.get("source_id", "")
        ad_context = (
            "\n[El usuario hizo clic en el anuncio de Facebook: "
            "'{}' - '{}' (ID: {})]"
        ).format(headline, body, source_id)
        if not texto_usuario:
            texto_usuario = "Hola, me interesa el anuncio: {}".format(headline)

    if not texto_usuario:
        return

    logger.info("📩 %s (%s): %s", nombre_usuario, numero_usuario, texto_usuario[:80])

    historial = obtener_historial(numero_usuario)
    resultado = procesar_mensaje(historial, texto_usuario, ad_context)

    if resultado.hay_traspaso:
        etiqueta = _ETIQUETAS_TRASPASO_LABELS.get(
            resultado.tipo_traspaso or "", "TRASPASO"
        )
        aviso = armar_notificacion_traspaso(
            etiqueta=etiqueta,
            nombre_usuario=nombre_usuario,
            numero_usuario=numero_usuario,
            programa=resultado.datos_traspaso.get("programa", "N/A"),
            resumen=resultado.datos_traspaso.get("resumen", "Sin detalle"),
        )
        enviar_whatsapp(NUMERO_ASESOR, aviso, phone_number_id)

    enviado = enviar_whatsapp(numero_usuario, resultado.texto, phone_number_id)
    if enviado:
        logger.info("🤖 Respuesta enviada a %s: %s", numero_usuario, resultado.texto[:80])

    guardar_mensaje(numero_usuario, "user", texto_usuario)
    guardar_mensaje(numero_usuario, "assistant", resultado.texto)
    limpiar_historial_antiguo(numero_usuario)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
