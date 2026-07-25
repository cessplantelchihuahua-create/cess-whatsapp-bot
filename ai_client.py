"""
ai_client.py — Wrapper para la OpenAI Responses API.

Características:
- Construye el prompt de sistema con el contexto de CESS.
- Maneja output_text None y tool calls de traspaso.
- Retorna una estructura normalizada para que app.py no tenga lógica de IA.
- Logging estructurado de tokens y duración.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI, APIError, APITimeoutError, RateLimitError

from config import (
    OPENAI_API_KEY,
    OPENAI_MODEL,
    TOOLS_TRASPASO,
    MENSAJES_RESPALDO,
    MENSAJE_RESPALDO_GENERICO,
)
from context_loader import get_contexto

logger = logging.getLogger(__name__)

_client = OpenAI(api_key=OPENAI_API_KEY)

_ETIQUETAS_TRASPASO: dict[str, str] = {
    "listo_para_inscribir": "🔥 LISTO PARA INSCRIBIR",
    "duda_sin_resolver": "❓ DUDA SIN RESOLVER",
    "tramite_administrativo": "🗂 TRÁMITE ADMINISTRATIVO",
}

_INSTRUCCION_INSCRIPCION = (
    "Para continuar con tu inscripción, comunícate al número 6144150015 "
    'con el mensaje "estoy listo para la inscripcion" o haz clic en este enlace: '
    "https://wa.me/526144150015?text=estoy%20listo%20para%20la%20inscripcion"
)


@dataclass
class RespuestaIA:
    """Resultado normalizado de una llamada a la IA."""
    texto: str
    tipo_traspaso: Optional[str] = None
    datos_traspaso: dict = field(default_factory=dict)
    hay_traspaso: bool = False


def _construir_instrucciones(ad_context: str = "") -> str:
    """Genera el prompt de sistema con el contexto privado de CESS."""
    contexto = get_contexto()
    base = (
        "Eres un asistente de servicio al cliente automatizado y amable.\n"
        "Usa ÚNICAMENTE el siguiente contexto para responder la pregunta del usuario.\n"
        "REGLA CRÍTICA: Si la respuesta no se encuentra explícitamente en el contexto, "
        "sigue las reglas de escalamiento definidas en el contexto (mensaje de escalación + "
        "llamada a la función notificar_traspaso). No inventes ni asumas información.\n\n"
        f"Contexto:\n{contexto}"
    )
    if ad_context:
        base += (
            f"\n\nContexto de origen del anuncio:\n{ad_context}\n"
            "IMPORTANTE: Saluda amigablemente haciendo alusión al anuncio de forma natural "
            "y prioriza la información del contexto privado relacionada con el tema del anuncio."
        )
    return base


def procesar_mensaje(
    historial: list[dict],
    texto_usuario: str,
    ad_context: str = "",
) -> RespuestaIA:
    """
    Llama a la OpenAI Responses API y retorna un RespuestaIA normalizado.

    Args:
        historial: Lista de mensajes previos [{role, content}, …].
        texto_usuario: Mensaje actual del usuario.
        ad_context: Contexto de anuncio de Facebook (puede estar vacío).

    Returns:
        RespuestaIA con el texto final y datos de traspaso si aplica.
    """
    instrucciones = _construir_instrucciones(ad_context)
    entrada = historial + [{"role": "user", "content": texto_usuario}]

    t0 = time.perf_counter()
    try:
        response = _client.responses.create(
            model=OPENAI_MODEL,
            instructions=instrucciones,
            input=entrada,
            temperature=0,
            max_output_tokens=2048,
            store=True,
            tools=TOOLS_TRASPASO,
        )
    except (APITimeoutError, RateLimitError) as exc:
        logger.error("OpenAI error transitorio: %s", exc)
        return RespuestaIA(texto=MENSAJE_RESPALDO_GENERICO)
    except APIError as exc:
        logger.error("OpenAI APIError: %s", exc)
        return RespuestaIA(texto=MENSAJE_RESPALDO_GENERICO)

    elapsed = time.perf_counter() - t0
    logger.info("OpenAI respondió en %.2fs", elapsed)

    # ── Extraer texto y function calls ─────────────────────────────────────
    respuesta_texto: str = response.output_text or ""
    tipo_traspaso: Optional[str] = None
    datos_traspaso: dict = {}

    for item in response.output:
        if getattr(item, "type", None) == "function_call" and item.name == "notificar_traspaso":
            try:
                datos_traspaso = json.loads(item.arguments)
            except (json.JSONDecodeError, TypeError):
                datos_traspaso = {}
            tipo_traspaso = datos_traspaso.get("tipo")
            logger.info("Traspaso detectado: tipo=%s", tipo_traspaso)
            break  # Solo procesamos el primer traspaso

    # ── Texto de respaldo si la IA no generó texto ─────────────────────────
    if not respuesta_texto:
        respuesta_texto = MENSAJES_RESPALDO.get(tipo_traspaso or "", MENSAJE_RESPALDO_GENERICO)

    # ── Asegurar info de contacto en traspasos de inscripción ──────────────
    elif tipo_traspaso == "listo_para_inscribir":
        if "6144150015" not in respuesta_texto and "614 415 0015" not in respuesta_texto:
            respuesta_texto = respuesta_texto.strip() + "\n\n" + _INSTRUCCION_INSCRIPCION

    return RespuestaIA(
        texto=respuesta_texto,
        tipo_traspaso=tipo_traspaso,
        datos_traspaso=datos_traspaso,
        hay_traspaso=tipo_traspaso is not None,
    )
