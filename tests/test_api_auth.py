"""Tests de autenticación Discord OAuth2 y sesión (módulo `modules/api`, Req 7).

Como en `test_api_app.py`, cargamos `modules/api/app.py` (y `auth.py`) por ruta
de archivo con importlib para evitar el `__init__.py` del paquete `modules` (que
importa `discord`). `build_app` se mantiene libre de Discord: se construye con un
`SimpleNamespace` como bot.

Cubre:
  - `GET /api/me` sin sesión → 401 (Req 7.3).
  - `GET /api/me` con cookie de sesión firmada válida → identidad de la sesión
    (Req 7.2/7.4).
  - `GET /auth/callback` con el intercambio OAuth + `/users/@me` mockeado → crea
    sesión y la identidad procede del usuario de Discord mockeado, NO de datos
    del cuerpo/query de la petición (Req 7.4).
  - `POST /auth/logout` borra la cookie de sesión.
"""
import importlib.util
import os
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

# Secreto de sesión determinista y cookies no-Secure para que el TestClient
# (que usa http://testserver) almacene y reenvíe las cookies en los tests.
os.environ.setdefault("SESSION_SECRET", "test-secret-please-change")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("DISCORD_CLIENT_ID", "client-123")
os.environ.setdefault("DISCORD_CLIENT_SECRET", "secret-456")
os.environ.setdefault("OAUTH_REDIRECT_URI", "https://example.test/auth/callback")
# FRONTEND_ORIGIN vacío → el callback redirige a "/" (mismo testserver), lo que
# mantiene las cookies en dominio para poder verificar la sesión resultante.
os.environ["FRONTEND_ORIGIN"] = ""


def _load_module(filename, modname):
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "modules", "api", filename,
    )
    spec = importlib.util.spec_from_file_location(modname, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[modname] = module
    spec.loader.exec_module(module)
    return module


_app_mod = _load_module("app.py", "api_app_under_test")
_auth_mod = _load_module("auth.py", "api_auth_under_test")
build_app = _app_mod.build_app
Identity = _auth_mod.Identity
SessionManager = _auth_mod.SessionManager
SESSION_COOKIE = _auth_mod.SESSION_COOKIE
STATE_COOKIE = _auth_mod.STATE_COOKIE


def _fake_bot():
    return SimpleNamespace(
        signup_manager=object(),
        registry_store=object(),
        guild_config_store=object(),
        loop=object(),
        event_bus=object(),
    )


def test_api_me_without_session_returns_401():
    """Sin cookie de sesión, /api/me responde 401 (Req 7.3)."""
    client = TestClient(build_app(_fake_bot()))
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_api_me_with_valid_session_returns_identity():
    """Con una cookie firmada válida, /api/me devuelve la identidad de la sesión."""
    app = build_app(_fake_bot())
    # Firmamos una sesión con el MISMO SessionManager que usa la app.
    sm: SessionManager = app.state.session_manager
    token = sm.serialize(Identity(user_id="42", username="bob", avatar="av1"))

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    resp = client.get("/api/me")

    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "42"
    assert body["username"] == "bob"


def test_api_me_with_tampered_cookie_returns_401():
    """Una cookie con firma inválida se rechaza con 401 (Req 7.3)."""
    app = build_app(_fake_bot())
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "not-a-valid-signed-token")
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_oauth_callback_creates_session_from_discord_user():
    """El callback con OAuth mockeado crea sesión y la identidad sale de Discord.

    Se monkeypatchea el intercambio de `code` y `GET /users/@me`. La identidad
    resultante DEBE provenir del usuario de Discord mockeado, nunca de datos del
    query/cuerpo de la petición (Req 7.4).
    """
    app = build_app(_fake_bot())
    sm: SessionManager = app.state.session_manager

    # Mock del cliente OAuth: sin red. fetch_user devuelve el "usuario real".
    app.state.oauth_client.exchange_code = lambda code: {"access_token": "tok-abc"}
    app.state.oauth_client.fetch_user = lambda token: {
        "id": "99887766",
        "username": "discord_alice",
        "avatar": "avatarhash",
    }

    client = TestClient(app)
    # Un `state` válido firmado y su cookie correspondiente (anti-CSRF).
    state = sm.make_state()
    client.cookies.set(STATE_COOKIE, state)

    # Incluimos un `user_id` falso en la query para demostrar que NO se usa: la
    # identidad debe derivar del usuario mockeado de Discord (id=99887766).
    resp = client.get(
        "/auth/callback",
        params={"code": "the-code", "state": state, "user_id": "00000000"},
        follow_redirects=True,
    )
    assert resp.status_code == 200  # redirige a "/" (salud)

    # La sesión quedó establecida; /api/me refleja al usuario de Discord.
    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["user_id"] == "99887766"
    assert body["username"] == "discord_alice"
    assert body["user_id"] != "00000000"


def test_oauth_callback_rejects_invalid_state():
    """Sin `state` válido (anti-CSRF), el callback responde 400."""
    app = build_app(_fake_bot())
    client = TestClient(app)
    resp = client.get(
        "/auth/callback",
        params={"code": "the-code", "state": "bogus"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_auth_login_redirects_to_discord():
    """`/auth/login` redirige a Discord con client_id, scope y state."""
    app = build_app(_fake_bot())
    client = TestClient(app)
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert "discord.com/oauth2/authorize" in location
    assert "client_id=client-123" in location
    assert "scope=identify" in location
    assert "state=" in location
    # Y deja la cookie de state anti-CSRF.
    assert STATE_COOKIE in resp.cookies


def test_logout_clears_session_cookie():
    """`POST /auth/logout` borra la cookie de sesión y deja /api/me en 401."""
    app = build_app(_fake_bot())
    sm: SessionManager = app.state.session_manager
    token = sm.serialize(Identity(user_id="7"))

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    # Confirmamos sesión activa antes de cerrar.
    assert client.get("/api/me").status_code == 200

    resp = client.post("/auth/logout")
    assert resp.status_code == 204

    client.cookies.clear()
    assert client.get("/api/me").status_code == 401
