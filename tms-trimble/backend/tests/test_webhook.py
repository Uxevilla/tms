"""Tests de seguridad del webhook de TransFollow (Basic auth, fail closed)."""
import base64

import main
import config


def test_fail_closed_sin_password(monkeypatch):
    monkeypatch.setattr(config, "TRANSFOLLOW_WEBHOOK_PASSWORD", "")
    monkeypatch.setattr(config, "TRANSFOLLOW_WEBHOOK_USER", "transfollow")
    assert main._webhook_autenticado("") is False
    assert main._webhook_autenticado("Basic cualquiera") is False


def test_basic_auth_correcto(monkeypatch):
    monkeypatch.setattr(config, "TRANSFOLLOW_WEBHOOK_PASSWORD", "secreto")
    monkeypatch.setattr(config, "TRANSFOLLOW_WEBHOOK_USER", "transfollow")
    token = "Basic " + base64.b64encode(b"transfollow:secreto").decode()
    assert main._webhook_autenticado(token) is True


def test_basic_auth_incorrecto(monkeypatch):
    monkeypatch.setattr(config, "TRANSFOLLOW_WEBHOOK_PASSWORD", "secreto")
    monkeypatch.setattr(config, "TRANSFOLLOW_WEBHOOK_USER", "transfollow")
    assert main._webhook_autenticado("Basic " + base64.b64encode(b"x:y").decode()) is False
    assert main._webhook_autenticado("") is False
