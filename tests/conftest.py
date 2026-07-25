"""
tests/conftest.py — Fixtures compartidas para toda la suite de tests.

Configura las variables de entorno ANTES de importar los módulos del bot,
para que config.py no falle por variables faltantes durante los tests.
Usa SQLite en memoria como base de datos.
"""

import os
import pytest

# ── Variables de entorno de test (deben setearse ANTES de cualquier import del bot) ──
_ENV_TEST = {
    "META_TOKEN": "test_meta_token",
    "PHONE_NUMBER_ID": "123456789",
    "VERIFY_TOKEN": "test_verify_token",
    "NUMERO_ASESOR": "+521234567890",
    "OPENAI_API_KEY": "sk-test-fake-key",
    "APP_SECRET": "test_secret_key_for_hmac",
    "OPENAI_MODEL": "gpt-4o-mini",
    "VENTANA_HISTORIAL": "5",
    "MAX_GUARDADOS_POR_NUMERO": "10",
    "HISTORIAL_DB_PATH": ":memory:",  # SQLite en memoria para tests
    "CONTEXTO_PRIVADO": "Contexto de prueba CESS. Programas: Auxiliar en Enfermería.",
    "LOG_LEVEL": "WARNING",  # Silenciar logs en tests
}

# Setear antes de cargar módulos
for k, v in _ENV_TEST.items():
    os.environ.setdefault(k, v)


@pytest.fixture(autouse=True)
def reset_db(monkeypatch):
    """
    Reinicializa la DB SQLite en memoria antes de cada test.
    Como usamos :memory:, cada conexión crea una nueva DB.
    Para tests de DB, forzamos una ruta temporal compartida.
    """
    import tempfile
    tmp = tempfile.mktemp(suffix=".db")
    monkeypatch.setenv("HISTORIAL_DB_PATH", tmp)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    # Reimportar db con la nueva ruta
    import importlib
    import db
    monkeypatch.setattr(db, "_SQLITE_PATH", tmp)
    monkeypatch.setattr(db, "_USE_POSTGRES", False)
    db.inicializar_db()
    yield
    # Cleanup
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass


@pytest.fixture
def client():
    """Flask test client con la app configurada para testing."""
    # Aseguramos que db esté inicializado antes de crear la app
    import db
    db.inicializar_db()

    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def meta_payload_texto():
    """Payload estándar de Meta con un mensaje de texto."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": "123456789",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Test User"},
                                    "wa_id": "526141234567",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "526141234567",
                                    "id": "wamid.test123",
                                    "timestamp": "1750275992",
                                    "text": {"body": "¿Cuánto cuesta el curso de enfermería?"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


@pytest.fixture
def meta_payload_referral():
    """Payload de Meta con referral de anuncio de Facebook."""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "102290129340398",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550783881",
                                "phone_number_id": "123456789",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Prospecto Facebook"},
                                    "wa_id": "526149999999",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "526149999999",
                                    "id": "wamid.ad123",
                                    "timestamp": "1750275993",
                                    "text": {"body": "Hola, vi su anuncio"},
                                    "type": "text",
                                    "referral": {
                                        "source_url": "https://fb.me/test",
                                        "source_id": "120226305854810726",
                                        "source_type": "ad",
                                        "body": "Inscríbete ahora",
                                        "headline": "Auxiliar en Enfermería",
                                    },
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
