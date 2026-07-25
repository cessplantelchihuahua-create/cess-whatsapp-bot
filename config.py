"""
config.py — Configuración centralizada para el bot CESS.

Todas las variables de entorno se validan aquí al arranque.
Si falta una variable crítica, el proceso falla rápido con un mensaje claro.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Meta / WhatsApp ──────────────────────────────────────────────────────────
META_TOKEN: str = os.environ["META_TOKEN"]
PHONE_NUMBER_ID: str = os.environ["PHONE_NUMBER_ID"]
VERIFY_TOKEN: str = os.environ.get("VERIFY_TOKEN", "vibecode")
NUMERO_ASESOR: str = os.environ["NUMERO_ASESOR"]
APP_SECRET: Optional[str] = os.environ.get("APP_SECRET")
API_VERSION: str = os.environ.get("META_API_VERSION", "v25.0")

# ── OpenAI ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
OPENAI_MODEL: str = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ── Historial ────────────────────────────────────────────────────────────────
VENTANA_HISTORIAL: int = int(os.environ.get("VENTANA_HISTORIAL", "10"))
MAX_GUARDADOS_POR_NUMERO: int = int(os.environ.get("MAX_GUARDADOS_POR_NUMERO", "30"))

# ── Base de datos ─────────────────────────────────────────────────────────────
DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")

# ── Mensajes de respaldo ──────────────────────────────────────────────────────
MENSAJES_RESPALDO = {
    "listo_para_inscribir": (
        "¡Perfecto! Para continuar con tu inscripción, comunícate al número 6144150015 "
        'con el mensaje "estoy listo para la inscripcion" o haz clic en el siguiente '
        "enlace: https://wa.me/526144150015?text=estoy%20listo%20para%20la%20inscripcion 🙂"
    ),
    "duda_sin_resolver": "En un momento te atiende un asesor para resolver tu duda 🙂",
    "tramite_administrativo": "En un momento te atiende un asesor para ayudarte con ese trámite 🙂",
}
MENSAJE_RESPALDO_GENERICO: str = "En un momento te atiende un asesor 🙂"

# ── Etiquetas de traspaso (usadas en app.py y tests) ─────────────────────────
_ETIQUETAS_TRASPASO_LABELS = {
    "listo_para_inscribir": "🔥 LISTO PARA INSCRIBIR",
    "duda_sin_resolver": "❓ DUDA SIN RESOLVER",
    "tramite_administrativo": "🗂 TRÁMITE ADMINISTRATIVO",
}

# ── Tool definitions (OpenAI) ────────────────────────────────────────────────
TOOLS_TRASPASO = [
    {
        "type": "function",
        "name": "notificar_traspaso",
        "description": (
            "Notifica a un asesor humano que un prospecto está siendo transferido. "
            "Llámala junto con tu respuesta normal al cliente, nunca en lugar de ella."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tipo": {
                    "type": "string",
                    "enum": ["listo_para_inscribir", "duda_sin_resolver", "tramite_administrativo"],
                    "description": "Motivo del traspaso.",
                },
                "programa": {
                    "type": "string",
                    "description": "Programa de interés del prospecto, si se conoce.",
                },
                "resumen": {
                    "type": "string",
                    "description": "1 línea de contexto para el asesor.",
                },
            },
            "required": ["tipo", "resumen"],
        },
    }
]

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()


def configurar_logging() -> None:
    """Configura el logging estructurado para producción."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ── Validación al importar ────────────────────────────────────────────────────
def _validar_config() -> None:
    """Falla rápido si faltan variables críticas."""
    required = {
        "META_TOKEN": META_TOKEN,
        "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
        "NUMERO_ASESOR": NUMERO_ASESOR,
        "OPENAI_API_KEY": OPENAI_API_KEY,
    }
    faltantes = [k for k, v in required.items() if not v]
    if faltantes:
        raise EnvironmentError(
            "Variables de entorno faltantes: {}. "
            "Configúralas en el panel de Render → Environment.".format(", ".join(faltantes))
        )
    if not APP_SECRET:
        logger.warning(
            "APP_SECRET no configurado — la validación HMAC de Meta está DESACTIVADA. "
            "Cualquier cliente puede hacer POST a /webhook."
        )


_validar_config()
