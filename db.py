"""
db.py — Capa de acceso a datos para el historial de conversaciones.

En producción (Render): usa PostgreSQL via DATABASE_URL.
En tests locales: usa SQLite si DATABASE_URL no está configurado.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List, Optional

logger = logging.getLogger(__name__)

_DATABASE_URL: Optional[str] = os.environ.get("DATABASE_URL")

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
    _SQLITE_PATH: str = os.environ.get("HISTORIAL_DB_PATH", "historial_cess.db")
    logger.info("Motor de base de datos: SQLite (%s)", _SQLITE_PATH)


@contextmanager
def _pg_connection() -> Generator:
    """Obtiene una conexión PostgreSQL."""
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


_DB_INICIALIZADA = False
_ULTIMO_DB_TARGET = None


def _connection_raw():
    return _pg_connection() if _USE_POSTGRES else _sqlite_connection()


def _connection():
    inicializar_db()  # Reintenta si no se pudo inicializar en el arranque o falló
    return _connection_raw()


_CREATE_TABLE_PG = """
    CREATE TABLE IF NOT EXISTS historial (
        id SERIAL PRIMARY KEY,
        numero TEXT NOT NULL,
        rol TEXT NOT NULL,
        contenido TEXT NOT NULL,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_historial_numero ON historial(numero);

    CREATE TABLE IF NOT EXISTS wamids_procesados (
        wamid TEXT PRIMARY KEY,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS usuarios_notificados_no_texto (
        numero TEXT PRIMARY KEY,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS conversaciones_admin (
        numero TEXT PRIMARY KEY,
        activa BOOLEAN NOT NULL DEFAULT TRUE,
        creado_en TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        actualizado_en TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
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

    CREATE TABLE IF NOT EXISTS wamids_procesados (
        wamid TEXT PRIMARY KEY,
        creado_en TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS usuarios_notificados_no_texto (
        numero TEXT PRIMARY KEY,
        creado_en TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS conversaciones_admin (
        numero TEXT PRIMARY KEY,
        activa INTEGER NOT NULL DEFAULT 1,
        creado_en TEXT NOT NULL,
        actualizado_en TEXT NOT NULL
    );
"""


def inicializar_db() -> None:
    """Crea las tablas si no existen. Idempotente."""
    global _DB_INICIALIZADA, _ULTIMO_DB_TARGET
    db_target = _DATABASE_URL if _USE_POSTGRES else _SQLITE_PATH
    if _DB_INICIALIZADA and _ULTIMO_DB_TARGET == db_target:
        return
    sql = _CREATE_TABLE_PG if _USE_POSTGRES else _CREATE_TABLE_SQLITE
    try:
        with _connection_raw() as conn:
            cur = conn.cursor()
            if _USE_POSTGRES:
                cur.execute(sql)
            else:
                for stmt in sql.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        cur.execute(stmt)
        _DB_INICIALIZADA = True
        _ULTIMO_DB_TARGET = db_target
        logger.info("Base de datos inicializada correctamente.")
    except Exception as exc:
        logger.error(
            "⚠️ Error inicializando la base de datos (se reintentará bajo demanda): %s",
            exc
        )


def es_wamid_procesado(wamid: str) -> bool:
    """Retorna True si el mensaje con ese ID (wamid) ya fue procesado."""
    if not wamid:
        return False
    sql = "SELECT 1 FROM wamids_procesados WHERE wamid = %s"
    if not _USE_POSTGRES:
        sql = sql.replace("%s", "?")
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, (wamid,))
        row = cur.fetchone()
        return row is not None


def registrar_wamid(wamid: str) -> None:
    """Registra el wamid en la tabla de deduplicación."""
    if not wamid:
        return
    ahora = datetime.now(timezone.utc).isoformat()
    sql = "INSERT INTO wamids_procesados (wamid, creado_en) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    if not _USE_POSTGRES:
        sql = "INSERT OR IGNORE INTO wamids_procesados (wamid, creado_en) VALUES (?, ?)"
    with _connection() as conn:
        conn.cursor().execute(sql, (wamid, ahora))


def ya_se_notifico_no_texto(numero: str) -> bool:
    """Retorna True si ya se envió el mensaje 'no puedo procesar' a este usuario."""
    if not numero:
        return False
    sql = "SELECT 1 FROM usuarios_notificados_no_texto WHERE numero = %s"
    if not _USE_POSTGRES:
        sql = sql.replace("%s", "?")
    with _connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, (numero,))
        row = cur.fetchone()
        return row is not None


def registrar_notificacion_no_texto(numero: str) -> None:
    """Registra que ya se notificó al usuario sobre mensajes no-texto."""
    if not numero:
        return
    ahora = datetime.now(timezone.utc).isoformat()
    sql = "INSERT INTO usuarios_notificados_no_texto (numero, creado_en) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    if not _USE_POSTGRES:
        sql = "INSERT OR IGNORE INTO usuarios_notificados_no_texto (numero, creado_en) VALUES (?, ?)"
    with _connection() as conn:
        conn.cursor().execute(sql, (numero, ahora))


def conversacion_en_manos_admin(numero: str) -> bool:
    """Retorna True si la conversación está siendo manejada activamente por un admin."""
    if not numero:
        return False
    sql = "SELECT 1 FROM conversaciones_admin WHERE numero = %s AND activa = %s"
    if not _USE_POSTGRES:
        sql = "SELECT 1 FROM conversaciones_admin WHERE numero = ? AND activa = 1"
        with _connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (numero,))
    else:
        with _connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, (numero, True))
    
    row = cur.fetchone()
    return row is not None


def marcar_conversacion_admin_activa(numero: str) -> None:
    """Marca que un admin está manejando esta conversación."""
    if not numero:
        return
    ahora = datetime.now(timezone.utc).isoformat()
    if _USE_POSTGRES:
        sql = """
            INSERT INTO conversaciones_admin (numero, activa, creado_en, actualizado_en)
            VALUES (%s, TRUE, %s, %s)
            ON CONFLICT (numero) DO UPDATE
            SET activa = TRUE, actualizado_en = %s
        """
        with _connection() as conn:
            conn.cursor().execute(sql, (numero, ahora, ahora, ahora))
    else:
        sql = """
            INSERT OR REPLACE INTO conversaciones_admin (numero, activa, creado_en, actualizado_en)
            VALUES (?, 1, ?, ?)
        """
        with _connection() as conn:
            conn.cursor().execute(sql, (numero, ahora, ahora))


def marcar_conversacion_admin_inactiva(numero: str) -> None:
    """Marca que el admin liberó esta conversación (puede volver a procesar con IA)."""
    if not numero:
        return
    ahora = datetime.now(timezone.utc).isoformat()
    if _USE_POSTGRES:
        sql = """
            UPDATE conversaciones_admin
            SET activa = FALSE, actualizado_en = %s
            WHERE numero = %s
        """
        with _connection() as conn:
            conn.cursor().execute(sql, (ahora, numero))
    else:
        sql = "UPDATE conversaciones_admin SET activa = 0, actualizado_en = ? WHERE numero = ?"
        with _connection() as conn:
            conn.cursor().execute(sql, (ahora, numero))


def guardar_mensaje(numero: str, rol: str, contenido: str) -> None:
    """Persiste un mensaje en el historial."""
    ahora = datetime.now(timezone.utc).isoformat()
    sql = "INSERT INTO historial (numero, rol, contenido, creado_en) VALUES (%s, %s, %s, %s)"
    if not _USE_POSTGRES:
        sql = sql.replace("%s", "?")
    with _connection() as conn:
        conn.cursor().execute(sql, (numero, rol, contenido, ahora))


def obtener_historial(numero: str, limite: Optional[int] = None) -> List[dict]:
    """
    Devuelve los últimos `limite` mensajes del número en orden cronológico,
    listos para pasarse como `input` a la OpenAI Responses API.
    """
    from config import VENTANA_HISTORIAL
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

    filas = list(reversed(filas))
    return [{"role": f[0], "content": f[1]} for f in filas]


def limpiar_historial_antiguo(numero: str) -> None:
    """Elimina registros viejos dejando solo los últimos MAX_GUARDADOS_POR_NUMERO."""
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
