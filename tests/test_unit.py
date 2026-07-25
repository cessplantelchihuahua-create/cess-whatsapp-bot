"""
tests/test_unit.py — Pruebas unitarias del bot CESS.

Sin red, sin OpenAI real, sin Meta real.
Todo se mockea. Prueban funciones individuales de cada módulo.
"""

import hashlib
import hmac
import json
import os
import pytest


# ════════════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════════════

class TestConfig:
    """Tests del módulo config.py"""

    def test_config_carga_correctamente(self):
        """Verifica que config.py cargue sin errores con las env vars de test."""
        import config
        assert config.META_TOKEN == "test_meta_token"
        assert config.PHONE_NUMBER_ID == "123456789"
        assert config.OPENAI_MODEL == "gpt-4o-mini"
        assert config.VENTANA_HISTORIAL == 5

    def test_mensajes_respaldo_existen(self):
        """Los tres tipos de traspaso deben tener mensajes de respaldo."""
        import config
        assert "listo_para_inscribir" in config.MENSAJES_RESPALDO
        assert "duda_sin_resolver" in config.MENSAJES_RESPALDO
        assert "tramite_administrativo" in config.MENSAJES_RESPALDO

    def test_etiquetas_traspaso_existen(self):
        """Las etiquetas de traspaso deben estar definidas."""
        import config
        assert "listo_para_inscribir" in config._ETIQUETAS_TRASPASO_LABELS
        assert "duda_sin_resolver" in config._ETIQUETAS_TRASPASO_LABELS

    def test_tools_traspaso_formato(self):
        """La definición del tool debe tener el formato correcto para OpenAI."""
        import config
        assert len(config.TOOLS_TRASPASO) == 1
        tool = config.TOOLS_TRASPASO[0]
        assert tool["type"] == "function"
        assert tool["name"] == "notificar_traspaso"
        assert "parameters" in tool


# ════════════════════════════════════════════════════════════════════════════
# DATABASE
# ════════════════════════════════════════════════════════════════════════════

class TestDatabase:
    """Tests del módulo db.py con SQLite en memoria."""

    def test_guardar_y_obtener_mensaje(self):
        """Un mensaje guardado debe recuperarse correctamente."""
        import db
        db.guardar_mensaje("521234567890", "user", "Hola, ¿cuánto cuesta?")
        db.guardar_mensaje("521234567890", "assistant", "El curso cuesta $900.")

        historial = db.obtener_historial("521234567890")
        assert len(historial) == 2
        assert historial[0]["role"] == "user"
        assert historial[0]["content"] == "Hola, ¿cuánto cuesta?"
        assert historial[1]["role"] == "assistant"

    def test_historial_limite(self):
        """El historial debe respetar el límite de ventana."""
        import db
        # Guardar 10 mensajes
        for i in range(10):
            db.guardar_mensaje("526141111111", "user", f"Mensaje {i}")

        # Obtener con límite de 3
        historial = db.obtener_historial("526141111111", limite=3)
        assert len(historial) == 3
        # Deben ser los últimos 3
        assert historial[-1]["content"] == "Mensaje 9"

    def test_historial_separado_por_numero(self):
        """El historial de dos números distintos no debe mezclarse."""
        import db
        db.guardar_mensaje("111", "user", "Mensaje de usuario 111")
        db.guardar_mensaje("222", "user", "Mensaje de usuario 222")

        h1 = db.obtener_historial("111")
        h2 = db.obtener_historial("222")
        assert len(h1) == 1
        assert len(h2) == 1
        assert h1[0]["content"] == "Mensaje de usuario 111"
        assert h2[0]["content"] == "Mensaje de usuario 222"

    def test_limpiar_historial_antiguo(self):
        """La limpieza debe conservar solo los últimos MAX mensajes."""
        import db
        import config

        max_msgs = config.MAX_GUARDADOS_POR_NUMERO
        # Guardar el doble del máximo
        for i in range(max_msgs * 2):
            db.guardar_mensaje("526142222222", "user", f"Turno {i}")

        db.limpiar_historial_antiguo("526142222222")

        # Verificar que no se supere el máximo
        import sqlite3
        con = sqlite3.connect(db._SQLITE_PATH)
        count = con.execute(
            "SELECT COUNT(*) FROM historial WHERE numero = ?", ("526142222222",)
        ).fetchone()[0]
        con.close()
        assert count <= max_msgs

    def test_historial_vacio_retorna_lista(self):
        """Un número sin historial debe retornar lista vacía, no error."""
        import db
        h = db.obtener_historial("numero_sin_historial")
        assert h == []

    def test_inicializar_db_idempotente(self):
        """Llamar inicializar_db varias veces no debe causar errores."""
        import db
        db.inicializar_db()
        db.inicializar_db()  # Segunda llamada — no debe fallar

    def test_deduplicacion_wamid(self):
        """Un wamid registrado debe retornar True en es_wamid_procesado."""
        import db
        wamid = "wamid.test_unique_123"
        assert db.es_wamid_procesado(wamid) is False
        db.registrar_wamid(wamid)
        assert db.es_wamid_procesado(wamid) is True
        # Registrar de nuevo (ON CONFLICT IGNORE) no debe fallar
        db.registrar_wamid(wamid)
        assert db.es_wamid_procesado(wamid) is True



# ════════════════════════════════════════════════════════════════════════════
# CONTEXT LOADER
# ════════════════════════════════════════════════════════════════════════════

class TestContextLoader:
    """Tests del módulo context_loader.py"""

    def test_contexto_cargado_desde_env(self):
        """Con CONTEXTO_PRIVADO en env, debe retornar ese texto."""
        import context_loader
        contexto = context_loader.get_contexto()
        assert "CESS" in contexto
        assert "Auxiliar en Enfermería" in contexto


# ════════════════════════════════════════════════════════════════════════════
# META CLIENT — HMAC
# ════════════════════════════════════════════════════════════════════════════

class TestMetaClientHMAC:
    """Tests de la validación HMAC en meta_client.py"""

    def _calcular_firma(self, payload: bytes, secret: str) -> str:
        """Helper: calcula la firma HMAC correcta."""
        return "sha256=" + hmac.new(
            secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()

    def test_hmac_valido_retorna_true(self):
        """Una firma HMAC correcta debe ser aceptada."""
        from meta_client import validar_firma_meta
        payload = b'{"test": "payload"}'
        firma = self._calcular_firma(payload, "test_secret_key_for_hmac")
        assert validar_firma_meta(payload, firma) is True

    def test_hmac_invalido_retorna_false(self):
        """Una firma HMAC incorrecta debe ser rechazada."""
        from meta_client import validar_firma_meta
        payload = b'{"test": "payload"}'
        firma_incorrecta = "sha256=aaabbbccc000111"
        assert validar_firma_meta(payload, firma_incorrecta) is False

    def test_hmac_sin_header_retorna_false(self):
        """Sin header de firma, la validación debe fallar."""
        from meta_client import validar_firma_meta
        assert validar_firma_meta(b'{"test": "data"}', None) is False

    def test_hmac_formato_invalido_retorna_false(self):
        """Un header sin prefijo sha256= debe fallar."""
        from meta_client import validar_firma_meta
        assert validar_firma_meta(b'data', "invalid-format-no-prefix") is False

    def test_hmac_desactivado_sin_secret(self, monkeypatch):
        """Sin APP_SECRET configurado, la validación siempre pasa (con warning)."""
        monkeypatch.setattr("meta_client.APP_SECRET", None)
        from meta_client import validar_firma_meta
        # Sin secret → siempre True (modo permisivo)
        assert validar_firma_meta(b'cualquier_payload', None) is True


# ════════════════════════════════════════════════════════════════════════════
# META CLIENT — armar_notificacion_traspaso
# ════════════════════════════════════════════════════════════════════════════

class TestMetaClientNotificacion:
    """Tests del formateo de notificaciones de traspaso."""

    def test_notificacion_contiene_datos(self):
        """La notificación debe incluir etiqueta, programa, cliente y nota."""
        from meta_client import armar_notificacion_traspaso
        resultado = armar_notificacion_traspaso(
            etiqueta="🔥 LISTO PARA INSCRIBIR",
            nombre_usuario="Juan Pérez",
            numero_usuario="526141234567",
            programa="Auxiliar en Enfermería",
            resumen="Prospecto listo para inscribirse",
        )
        assert "LISTO PARA INSCRIBIR" in resultado
        assert "Juan Pérez" in resultado
        assert "526141234567" in resultado
        assert "Auxiliar en Enfermería" in resultado
        assert "Prospecto listo para inscribirse" in resultado


# ════════════════════════════════════════════════════════════════════════════
# AI CLIENT — RespuestaIA
# ════════════════════════════════════════════════════════════════════════════

class TestAIClientRespuesta:
    """Tests de la dataclass RespuestaIA."""

    def test_respuesta_ia_defaults(self):
        """RespuestaIA debe tener valores por defecto correctos."""
        from ai_client import RespuestaIA
        r = RespuestaIA(texto="Hola")
        assert r.texto == "Hola"
        assert r.tipo_traspaso is None
        assert r.datos_traspaso == {}
        assert r.hay_traspaso is False

    def test_respuesta_ia_con_traspaso(self):
        """RespuestaIA con traspaso debe reflejarlo correctamente."""
        from ai_client import RespuestaIA
        r = RespuestaIA(
            texto="Te conecto con un asesor.",
            tipo_traspaso="duda_sin_resolver",
            datos_traspaso={"resumen": "No sé la respuesta"},
            hay_traspaso=True,
        )
        assert r.hay_traspaso is True
        assert r.tipo_traspaso == "duda_sin_resolver"
