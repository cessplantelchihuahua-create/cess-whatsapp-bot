"""
app.py — Bot WhatsApp CESS · Producción en Render

Entry point Flask. Contiene SOLO las rutas HTTP.
Toda la lógica de negocio está en los módulos:
  config.py         — variables de entorno y constantes
  db.py             — historial y deduplicación en PostgreSQL (o SQLite)
  ai_client.py      — wrapper OpenAI Responses API
  meta_client.py    — wrapper Meta WhatsApp API con retry + HMAC
  context_loader.py — carga datosCESS.txt
"""
from __future__ import annotations

import logging
import threading
from typing import Optional
from time import sleep

from flask import Flask, request, jsonify

import config  # noqa: F401 — dispara validación al arranque
from config import VERIFY_TOKEN, NUMERO_ASESOR, _ETIQUETAS_TRASPASO_LABELS
from db import (
    inicializar_db,
    guardar_mensaje,
    obtener_historial,
    limpiar_historial_antiguo,
    es_wamid_procesado,
    registrar_wamid,
    ya_se_notifico_no_texto,
    registrar_notificacion_no_texto,
    conversacion_en_manos_admin,
    marcar_conversacion_admin_activa,
    marcar_conversacion_admin_inactiva,
    agregar_mensaje_sin_procesar,
    obtener_mensajes_sin_procesar,
    limpiar_mensajes_sin_procesar,
)
from ai_client import procesar_mensaje
from meta_client import validar_firma_meta, enviar_whatsapp, armar_notificacion_traspaso

# ── Logging ─────────────────────────────────────────────────────────────[...]
config.configurar_logging()
logger = logging.getLogger(__name__)

# ── Flask App ────────────────────────────────────────────────────────────[...]
app = Flask(__name__)

# Inicializar DB al arrancar (idempotente)
inicializar_db()

_MENSAJE_NO_TEXTO = (
    "Por el momento solo puedo leer mensajes de texto. Si tienes alguna duda sobre "
    "nuestros programas, costos o inscripciones, ¡escríbemela por aquí y con gusto te ayudo! 🙂"
)

# ── Control de síntesis de mensajes ─────────────────────────────────────[...]
# Diccionario de timers activos por usuario para agrupar mensajes
_mensaje_timers: dict[str, threading.Timer] = {}
_VENTANA_AGRUPACION = 5  # segundos para agrupar mensajes del mismo usuario


# ── Routes ─────────────────────────────────────────────────────────────[...]

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
    Retorna 200 OK inmediatamente a Meta para cumplir con el timeout de 20s.
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


@app.route("/admin/pausar/<numero>", methods=["POST"])
def pausar_ia(numero: str):
    """
    Endpoint para que el admin active el modo de control manual.
    Uso: POST /admin/pausar/34612345678
    """
    marcar_conversacion_admin_activa(numero)
    logger.info("🛑 IA pausada para conversación con %s — admin tomó el control", numero)
    return jsonify({"status": "ok", "mensaje": f"Conversación {numero} bajo control manual"}), 200


@app.route("/admin/reanudar/<numero>", methods=["POST"])
def reanudar_ia(numero: str):
    """
    Endpoint para que el admin libere el control manual.
    Uso: POST /admin/reanudar/34612345678
    """
    marcar_conversacion_admin_inactiva(numero)
    logger.info("▶️ IA reanudada para conversación con %s — admin liberó el control", numero)
    return jsonify({"status": "ok", "mensaje": f"Conversación {numero} vuelve a automático"}), 200


# ── Business Logic ──────────────────────────────────────────────────────────[...]

def _procesar_payload(data: dict) -> None:
    """Itera sobre el payload de Meta y procesa cada mensaje aisladamente."""
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            if change.get("field") != "messages" or value.get("messaging_product") != "whatsapp":
                continue

            contacts = value.get("contacts", [])
            nombre_usuario = contacts[0].get("profile", {}).get("name", "Usuario") if contacts else "Usuario"
            phone_number_id = value.get("metadata", {}).get("phone_number_id")

            for message in value.get("messages", []):
                try:
                    _procesar_mensaje_individual(message, nombre_usuario, phone_number_id)
                except Exception as exc:
                    logger.exception("Error procesando mensaje individual %s: %s", message.get("id"), exc)


def _procesar_mensaje_individual(
    message: dict,
    nombre_usuario: str,
    phone_number_id: Optional[str],
) -> None:
    """Procesa un único mensaje entrante de WhatsApp con deduplicación y soporte multimedia."""
    numero_usuario: str = message.get("from", "")
    wamid: str = message.get("id", "")

    # 1. Deduplicación por wamid
    if wamid and es_wamid_procesado(wamid):
        logger.info("🔁 Mensaje duplicado omitido (wamid: %s)", wamid)
        return

    # Registrar el wamid para prevenir procesamiento duplicado concurrente/posterior
    if wamid:
        registrar_wamid(wamid)

    # 2. Verificar si la conversación está siendo manejada por un admin
    if conversacion_en_manos_admin(numero_usuario):
        logger.info("⏸️ Conversación pausada (admin en control): mensaje de %s ignorado", numero_usuario)
        # No procesar con IA, guardar el mensaje como historial pero no responder
        texto_guardado = message.get("text", {}).get("body") if message.get("type") == "text" else "[mensaje no-texto]"
        guardar_mensaje(numero_usuario, "user", texto_guardado)
        return

    # 3. Manejo de tipos de mensaje
    msg_type = message.get("type")
    texto_usuario: Optional[str] = None

    if msg_type == "text":
        texto_usuario = message.get("text", {}).get("body")
    elif msg_type in ("audio", "image", "document", "video", "sticker", "voice", "location"):
        logger.info("📎 Mensaje no-texto recibido (%s) de %s", msg_type, numero_usuario)
        # Enviar notificación SOLO SI NO SE HA ENVIADO ANTES
        if not ya_se_notifico_no_texto(numero_usuario):
            enviar_whatsapp(numero_usuario, _MENSAJE_NO_TEXTO, phone_number_id)
            registrar_notificacion_no_texto(numero_usuario)
            logger.info("✉️ Notificación no-texto enviada a %s (primera vez)", numero_usuario)
        else:
            logger.info("⏭️ Notificación no-texto ya fue enviada a %s — ignorando mensaje", numero_usuario)
        return

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

    # 4. Agregar mensaje a la cola de síntesis y programar procesamiento
    agregar_mensaje_sin_procesar(numero_usuario, texto_usuario)
    _reprogramar_procesamiento_sintetizado(numero_usuario, nombre_usuario, phone_number_id)


def _reprogramar_procesamiento_sintetizado(
    numero_usuario: str,
    nombre_usuario: str,
    phone_number_id: Optional[str],
) -> None:
    """
    Cancela el timer anterior (si existe) y programa uno nuevo.
    Así se agrupan los mensajes llegados dentro de la ventana de tiempo.
    """
    global _mensaje_timers
    
    # Cancelar timer anterior si existe
    if numero_usuario in _mensaje_timers:
        _mensaje_timers[numero_usuario].cancel()
        logger.info("⏱️ Timer anterior cancelado para %s (nuevo mensaje llegó)", numero_usuario)
    
    # Programar nuevo timer
    timer = threading.Timer(
        _VENTANA_AGRUPACION,
        _procesar_mensajes_agrupados,
        args=(numero_usuario, nombre_usuario, phone_number_id)
    )
    _mensaje_timers[numero_usuario] = timer
    timer.daemon = True
    timer.start()
    logger.info("⏱️ Timer iniciado para %s (%ds) — esperando más mensajes...", numero_usuario, _VENTANA_AGRUPACION)


def _procesar_mensajes_agrupados(
    numero_usuario: str,
    nombre_usuario: str,
    phone_number_id: Optional[str],
) -> None:
    """
    Obtiene todos los mensajes sin procesar del usuario, los sintetiza
    en uno solo y genera una única respuesta.
    """
    try:
        # Obtener todos los mensajes en la ventana
        mensajes_buffer = obtener_mensajes_sin_procesar(numero_usuario, ventana_segundos=_VENTANA_AGRUPACION)
        
        if not mensajes_buffer:
            logger.info("ℹ️ Sin mensajes para procesar de %s", numero_usuario)
            return
        
        # Sintetizar los mensajes en uno solo
        if len(mensajes_buffer) == 1:
            texto_sintetizado = mensajes_buffer[0]
            logger.info("📬 1 mensaje de %s: %s", numero_usuario, texto_sintetizado[:80])
        else:
            # Agrupar los mensajes como un solo contexto
            texto_sintetizado = " ".join(mensajes_buffer)
            logger.info(
                "📬 %d mensajes agrupados de %s → sintetizado a: %s",
                len(mensajes_buffer),
                numero_usuario,
                texto_sintetizado[:100]
            )
        
        # Procesar con IA
        historial = obtener_historial(numero_usuario)
        resultado = procesar_mensaje(historial, texto_sintetizado, "")
        
        # Manejar traspaso
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
            marcar_conversacion_admin_activa(numero_usuario)
            logger.info("🔄 Conversación pausada tras traspaso para %s", numero_usuario)
        
        # Enviar respuesta
        enviado = enviar_whatsapp(numero_usuario, resultado.texto, phone_number_id)
        if enviado:
            logger.info("🤖 Respuesta enviada a %s: %s", numero_usuario, resultado.texto[:80])
        
        # Guardar en historial (todos los mensajes como uno + respuesta)
        guardar_mensaje(numero_usuario, "user", texto_sintetizado)
        guardar_mensaje(numero_usuario, "assistant", resultado.texto)
        limpiar_historial_antiguo(numero_usuario)
        
        # Limpiar buffer
        limpiar_mensajes_sin_procesar(numero_usuario)
        
    except Exception as exc:
        logger.exception("Error procesando mensajes agrupados de %s: %s", numero_usuario, exc)
    finally:
        # Limpiar el timer del diccionario
        global _mensaje_timers
        if numero_usuario in _mensaje_timers:
            del _mensaje_timers[numero_usuario]


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
