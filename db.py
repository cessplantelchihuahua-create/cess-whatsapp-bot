"""
db.py — Capa de acceso a datos para el historial de conversaciones.

En producción (Render): usa PostgreSQL via DATABASE_URL.
En tests locales: usa SQLite en memoria si DATABASE_URL no está configurado.

Expone una interfaz única independiente del motor subyacente.
"""

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

logger = logging.getLogger(__name__)

# Detectamos el motor disponible
_DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

# psycopg2 solo se importa si está disponible y hay DATABASE_URL configurado
_USE_POSTGRES: bool = False

if _DATABASE_URL:
    try:
        import psycopg2
        import psycopg2.extras
        _USE_POSTGRES = True
        logger.info("Motor de base de datos: PostgreSQL")
    except ImportError:
        logger.warning(
            "psycopg2 no instalado. Usando SQLite como fallback. "
            "En producción instala: pip install psycopg2-binary"
        )

if not _USE_POSTGRES:
    import sqlite3
    _SQLITE_PATH: str = os.environ.get("HISTORIAL_DB_PATH", ":memory:")
    logger.info("Motor de base de datos: SQLite (%s)", _SQLITE_PATH)


# ── Connection context managers ───────────────────────────────────────────────

@contextmanager
def _pg_connection() -> Generator:
    """Obtiene una conexión PostgreSQL del pool."""
    conn = psycopg2.connect(_DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _sqlite_connection() -> Generator:
    """Obtiene una conexión SQLite."""
    conn = sqlite3.connect(_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _connection():
    """Retorna el context manager correcto según el motor configurado."""
    return _pg_connection() if _USE_POSTGRES else _sqlite_connection()


# ── Schema ────────────────────────────────────────────────────────────────────

_CREATE_TABLE_PG = """
    CREATE TABLE IF NOT EXISTS historial (
        id SERIAL PRIMARY KEY,
        numero TEXT NOT NULL,
        rol TEXT NOT NULL,
        contenido TEXT NOT NULL,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_historial_numero ON historial(numero);
"""

_CREATE_TABLE_SQLITE = """
    CREATE TABLE IF NOT EXISTS historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        numero TEXT NOT NULL,
        rol TEXT NOT NULL,
        contenido TEXT NOT NULL,
        creado_en TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_historial_numero ON historial(numero);
"""


def inicializar_db() -> None:
    """Crea las tablas si no existen. Idempotente."""
    sql = _CREATE_TABLE_PG if _USE_POSTGRES else _CREATE_TABLE_SQLITE
    with _connection() as conn:
        cur = conn.cursor()
        # PostgreSQL admite múltiples statements; para SQLite hay que dividir
        if _USE_POSTGRES:
            cur.execute(sql)
        else:
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
    logger.info("Base de datos inicializada correctamente.")


# ── CRUD ──────────────────────────────────────────────────────────────────────

def guardar_mensaje(numero: str, rol: str, contenido: str) -> None:
    """Persiste un mensaje en el historial."""
    ahora = datetime.now(timezone.utc).isoformat()
    sql = "INSERT INTO historial (numero, rol, contenido, creado_en) VALUES (%s, %s, %s, %s)"
    if not _USE_POSTGRES:
        sql = sql.replace("%s", "?")
    with _connection() as conn:
        conn.cursor().execute(sql, (numero, rol, contenido, ahora))


def obtener_historial(numero: str, limite: int | None = None) -> list[dict]:
    """
    Devuelve los últimos `limite` mensajes del número, en orden cronológico,
    listos para pasarse como `input` a la OpenAI Responses API.
    """
    from config import VENTANA_HISTORIAL  # evitar importación circular al nivel módulo
    if limite is None:
        limite = VENTANA_HISTORIAL

    sql = (
        "SELECT rol, contenido FROM historial "
        "WHERE numero = %s ORDER BY id DESC LIMIT %s"
    )
    if not _USE_POSTGRES:
        sql = sql.replace("%s", "?")

    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, (numero, limite))
        filas = cur.fetchall()

    filas = list(reversed(filas))  # más viejo → más nuevo
    return [{"role": f[0], "content": f[1]} for f in filas]


def limpiar_historial_antiguo(numero: str) -> None:
    """
    Elimina registros viejos dejando solo los últimos MAX_GUARDADOS_POR_NUMERO
    mensajes del número. Evita crecimiento ilimitado de la tabla.
    """
    from config import MAX_GUARDADOS_POR_NUMERO

    if _USE_POSTGRES:
        sql = """
            DELETE FROM historial
            WHERE numero = %s AND id NOT IN (
                SELECT id FROM historial WHERE numero = %s ORDER BY id DESC LIMIT %s
            )
        """
    else:
        sql = """
            DELETE FROM historial
            WHERE numero = ? AND id NOT IN (
                SELECT id FROM historial WHERE numero = ? ORDER BY id DESC LIMIT ?
            )
        """

    with _connection() as conn:
        conn.cursor().execute(sql, (numero, numero, MAX_GUARDADOS_POR_NUMERO))
