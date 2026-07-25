"""
tests/test_functional.py — Pruebas funcionales del bot CESS.

Usan el Flask test client para hacer peticiones HTTP reales contra la app.
OpenAI y Meta se mockean para no hacer llamadas reales.
"""

import hashlib
import hmac
import json
import pytest


def _firma_meta(payload: bytes, secret: str = "test_secret_key_for_hmac") -> str:
    """Calcula la firma HMAC correcta para un payload."""
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def _post_webhook(client, payload: dict, con_firma: bool = True):
    """Helper para hacer POST al webhook con firma HMAC opcional."""
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if con_firma:
        headers["X-Hub-Signature-256"] = _firma_meta(body)
    return client.post("/webhook", data=body, headers=headers)


# ════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ════════════════════════════════════════════════════════════════════════════

class TestHealthCheck:
    """Tests del endpoint raíz /"""

    def test_health_check_retorna_200(self, client):
        """GET / debe retornar 200."""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_health_check_contiene_texto(self, client):
        """GET / debe retornar texto indicando que el servicio está activo."""
        resp = client.get("/")
        assert b"activo" in resp.data or b"Bot" in resp.data or b"CESS" in resp.data


# ════════════════════════════════════════════════════════════════════════════
# WEBHOOK VERIFICATION (GET)
# ════════════════════════════════════════════════════════════════════════════

class TestWebhookVerification:
    """Tests de la verificación GET del webhook de Meta."""

    def test_verificacion_token_correcto(self, client):
        """Con token correcto, debe retornar el challenge y 200."""
        resp = client.get(
            "/webhook?hub.mode=subscribe&hub.verify_token=test_verify_token&hub.challenge=CHALLENGE_XYZ"
        )
        assert resp.status_code == 200
        assert b"CHALLENGE_XYZ" in resp.data

    def test_verificacion_token_incorrecto(self, client):
        """Con token incorrecto, debe retornar 403."""
        resp = client.get(
            "/webhook?hub.mode=subscribe&hub.verify_token=TOKEN_INCORRECTO&hub.challenge=XYZ"
        )
        assert resp.status_code == 403

    def test_verificacion_sin_parametros(self, client):
        """Sin parámetros, debe retornar 400."""
        resp = client.get("/webhook")
        assert resp.status_code == 400

    def test_verificacion_modo_incorrecto(self, client):
        """Con hub.mode distinto de 'subscribe', debe retornar 403."""
        resp = client.get(
            "/webhook?hub.mode=unsubscribe&hub.verify_token=test_verify_token&hub.challenge=XYZ"
        )
        assert resp.status_code == 403


# ════════════════════════════════════════════════════════════════════════════
# WEBHOOK POST — Recepción de mensajes
# ════════════════════════════════════════════════════════════════════════════

class TestWebhookPost:
    """Tests del endpoint POST /webhook."""

    def test_mensaje_texto_retorna_200(self, client, mocker, meta_payload_texto):
        """Un mensaje de texto válido debe retornar 200."""
        # Mock OpenAI
        mock_respuesta = mocker.MagicMock()
        mock_respuesta.output_text = "Hola, el curso de Auxiliar cuesta $900 de inscripción."
        mock_respuesta.output = []
        mocker.patch("ai_client._client.responses.create", return_value=mock_respuesta)

        # Mock Meta send
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        resp = _post_webhook(client, meta_payload_texto)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["status"] == "ok"

    def test_mensaje_texto_llama_openai(self, client, mocker, meta_payload_texto):
        """Un mensaje de texto debe resultar en una llamada a OpenAI."""
        mock_create = mocker.patch("ai_client._client.responses.create")
        mock_create.return_value = mocker.MagicMock(
            output_text="Respuesta de prueba.",
            output=[],
        )
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        _post_webhook(client, meta_payload_texto)
        mock_create.assert_called_once()

    def test_mensaje_texto_llama_meta_send(self, client, mocker, meta_payload_texto):
        """Un mensaje de texto debe resultar en un envío a Meta."""
        mocker.patch("ai_client._client.responses.create", return_value=mocker.MagicMock(
            output_text="Respuesta.",
            output=[],
        ))
        mock_post = mocker.patch(
            "meta_client.requests.post",
            return_value=mocker.MagicMock(status_code=200),
        )

        _post_webhook(client, meta_payload_texto)
        assert mock_post.called

    def test_50_peticiones_sin_crash(self, client, mocker):
        """50 peticiones rápidas al webhook no deben crashear el servidor."""
        mocker.patch("ai_client._client.responses.create", return_value=mocker.MagicMock(
            output_text="OK", output=[]
        ))
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        errores = 0
        for i in range(50):
            msg_id = f"wamid.flood{i}"
            msg_body = f"Mensaje flood {i}"
            payload = {
                "object": "whatsapp_business_account",
                "entry": [{
                    "id": "test",
                    "changes": [{
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "123456789"},
                            "contacts": [{"profile": {"name": "Flood"}, "wa_id": "flood_num"}],
                            "messages": [{
                                "from": "flood_num",
                                "id": msg_id,
                                "timestamp": "1750275992",
                                "text": {"body": msg_body},
                                "type": "text",
                            }],
                        },
                        "field": "messages",
                    }],
                }]
            }
            try:
                resp = _post_webhook(client, payload)
                if resp.status_code not in (200, 401):
                    errores += 1
            except Exception:
                errores += 1

        assert errores == 0, f"Hubo {errores} errores en 50 peticiones"

    def test_mensaje_no_texto_envia_aviso(self, client, mocker):
        """Un mensaje de tipo imagen/audio debe enviar aviso amable al usuario sin llamar a OpenAI."""
        mock_create = mocker.patch("ai_client._client.responses.create")
        mock_post = mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "102290129340398",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "123456789"},
                        "contacts": [{"profile": {"name": "User"}, "wa_id": "526141234567"}],
                        "messages": [{
                            "from": "526141234567",
                            "id": "wamid.img123_unique",
                            "timestamp": "1750275992",
                            "type": "image",  # No es texto
                            "image": {"id": "img_id"}
                        }],
                    },
                    "field": "messages",
                }],
            }],
        }
        resp = _post_webhook(client, payload)
        assert resp.status_code == 200
        # No debe haberse llamado a OpenAI
        mock_create.assert_not_called()
        # Debe haberse llamado a Meta para notificar al usuario que solo leemos texto
        assert mock_post.called

    def test_mensaje_duplicado_omitido(self, client, mocker, meta_payload_texto):
        """Un segundo envío del mismo mensaje (mismo wamid) no debe llamar a OpenAI dos veces."""
        mock_create = mocker.patch("ai_client._client.responses.create", return_value=mocker.MagicMock(
            output_text="Respuesta.", output=[]
        ))
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        # Primer envío
        res1 = _post_webhook(client, meta_payload_texto)
        assert res1.status_code == 200
        assert mock_create.call_count == 1

        # Segundo envío con el mismo wamid
        res2 = _post_webhook(client, meta_payload_texto)
        assert res2.status_code == 200
        # OpenAI NO debió ser llamado una segunda vez
        assert mock_create.call_count == 1


    def test_payload_campo_incorrecto_ignorado(self, client, mocker):
        """Un cambio con field distinto de 'messages' debe ignorarse."""
        mock_create = mocker.patch("ai_client._client.responses.create")
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "test",
                "changes": [{
                    "value": {"messaging_product": "whatsapp"},
                    "field": "statuses",  # No es 'messages'
                }],
            }],
        }
        resp = _post_webhook(client, payload)
        assert resp.status_code == 200
        mock_create.assert_not_called()

    def test_referral_anuncio_inyecta_contexto(self, client, mocker, meta_payload_referral):
        """Un mensaje con referral debe incluir contexto del anuncio en el prompt."""
        mock_create = mocker.patch("ai_client._client.responses.create")
        mock_create.return_value = mocker.MagicMock(
            output_text="¡Hola! Vi que te interesa el curso de enfermería.",
            output=[],
        )
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        _post_webhook(client, meta_payload_referral)

        # Verificar que se llamó con contexto de anuncio
        call_args = mock_create.call_args
        instrucciones = call_args.kwargs.get("instructions", "")
        assert "anuncio" in instrucciones.lower() or "facebook" in instrucciones.lower()

    def test_traspaso_detectado_notifica_asesor(self, client, mocker, meta_payload_texto):
        """Cuando la IA detecta un traspaso, debe notificarse al asesor."""
        # Simular function call de traspaso en el output
        mock_fc = mocker.MagicMock()
        mock_fc.type = "function_call"
        mock_fc.name = "notificar_traspaso"
        mock_fc.arguments = json.dumps({
            "tipo": "listo_para_inscribir",
            "programa": "Auxiliar en Enfermería",
            "resumen": "Cliente listo para inscribirse",
        })

        mocker.patch("ai_client._client.responses.create", return_value=mocker.MagicMock(
            output_text="¡Excelente! Para continuar con tu inscripción...",
            output=[mock_fc],
        ))

        post_calls = []
        def mock_requests_post(*args, **kwargs):
            post_calls.append(kwargs.get("json", {}).get("to", args))
            return mocker.MagicMock(status_code=200)

        mocker.patch("meta_client.requests.post", side_effect=mock_requests_post)

        _post_webhook(client, meta_payload_texto)

        # Debe haber al menos 2 llamadas a Meta: una al asesor y otra al usuario
        assert len(post_calls) >= 2
        # El asesor debe estar en uno de los destinatarios
        assert any("+521234567890" in str(dest) for dest in post_calls)

    def test_historial_se_guarda(self, client, mocker, meta_payload_texto):
        """Después de procesar un mensaje, el historial debe guardarse en DB."""
        import db
        mocker.patch("ai_client._client.responses.create", return_value=mocker.MagicMock(
            output_text="El curso cuesta $900.",
            output=[],
        ))
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        _post_webhook(client, meta_payload_texto)

        historial = db.obtener_historial("526141234567")
        assert len(historial) == 2  # user + assistant
        assert historial[0]["role"] == "user"
        assert historial[1]["role"] == "assistant"

    def test_error_openai_retorna_200_igualmente(self, client, mocker, meta_payload_texto):
        """Incluso si OpenAI falla, el webhook debe retornar 200 a Meta."""
        mocker.patch(
            "ai_client._client.responses.create",
            side_effect=Exception("OpenAI network error simulated"),
        )
        mocker.patch("meta_client.requests.post", return_value=mocker.MagicMock(status_code=200))

        resp = _post_webhook(client, meta_payload_texto)
        assert resp.status_code == 200

    def test_payload_vacio_retorna_200(self, client, mocker):
        """Un payload JSON vacío o malformado no debe crashear el servidor."""
        resp = _post_webhook(client, {})
        assert resp.status_code == 200
