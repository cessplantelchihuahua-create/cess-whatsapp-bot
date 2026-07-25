"""
context_loader.py — Carga el contexto privado del asistente CESS.

Busca datosCESS.txt relativo a este archivo (funciona en cualquier CWD).
Permite override completo via variable de entorno CONTEXTO_PRIVADO
(útil para tests sin tocar el filesystem).
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_OVERRIDE: str | None = os.environ.get("CONTEXTO_PRIVADO")

if _OVERRIDE is not None:
    _contexto: str = _OVERRIDE
    logger.info("Contexto cargado desde variable de entorno CONTEXTO_PRIVADO.")
else:
    _ruta = Path(__file__).parent / "datosCESS.txt"
    try:
        _contexto = _ruta.read_text(encoding="utf-8")
        logger.info("Contexto cargado desde %s (%d chars)", _ruta, len(_contexto))
    except FileNotFoundError:
        _contexto = ""
        logger.warning(
            "datosCESS.txt no encontrado en %s. "
            "Las respuestas de la IA operarán sin contexto.",
            _ruta,
        )


def get_contexto() -> str:
    """Retorna el contexto privado del asistente."""
    return _contexto
