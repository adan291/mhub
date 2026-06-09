"""Autenticación Discord OAuth2 y sesión por cookie firmada (Req 7).

Este módulo es deliberadamente independiente de `discord`: solo usa `httpx`
para hablar con la API de Discord (intercambio de `code` y `GET /users/@me`) e
`itsdangerous` para firmar/verificar la cookie de sesión. Eso permite montar las
rutas en `build_app(bot)` y testearlas con un `TestClient` y un bot ligero
(`SimpleNamespace`), sin red ni un bot real.

Flujo Authorization Code (diseño, sección "Autenticación Discord OAuth2"):
  1. `GET /auth/login` → redirige a Discord `authorize` con `client_id`,
     `redirect_uri`, `scope=identify` y un `state` anti-CSRF (guardado en una
     cookie de corta vida y firmada).
  2. `GET /auth/callback?code&state` → valida `state`, intercambia `code` por un
     token (`POST /oauth2/token`), llama a `GET /users/@me`, crea la Sesion_Web
     (cookie firmada con el `user_id`) y redirige a `FRONTEND_ORIGIN` (Req 7.2).
  3. `POST /auth/logout` → borra la cookie de sesión.
  4. `GET /api/me` → devuelve la Identidad de la sesión vía `get_current_identity`.

La identidad de toda Escritura_Autenticada se toma SIEMPRE de la Sesion_Web y
nunca del cuerpo de la petición (Req 7.4). La dependencia `get_current_identity`
devuelve 401 cuando la cookie falta o es inválida (Req 7.3).
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

# Nombres de cookie y endpoints de Discord.
SESSION_COOKIE = "mhub_session"
STATE_COOKIE = "mhub_oauth_state"

DISCORD_API_BASE = "https://discord.com/api"
DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"
DISCORD_USER_URL = f"{DISCORD_API_BASE}/users/@me"

# Edad máxima de la sesión (segundos). 7 días por defecto.
SESSION_MAX_AGE = 7 * 24 * 60 * 60
# Edad máxima del `state` anti-CSRF (segundos).
STATE_MAX_AGE = 10 * 60


@dataclass(frozen=True)
class Identity:
    """Identidad autenticada derivada de la Sesion_Web.

    `user_id` es el identificador de Discord; es el único dato necesario para
    aplicar Escritura_Autenticada. `username`/`avatar` son metadatos opcionales
    para la UI.
    """

    user_id: str
    username: Optional[str] = None
    avatar: Optional[str] = None

    def to_public_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "avatar": self.avatar,
        }


@dataclass(frozen=True)
class OAuthConfig:
    """Configuración OAuth2 leída del entorno.

    Se resuelve con `OAuthConfig.from_env()` en `build_app`. Los tests pueden
    inyectar una instancia directamente para no depender del entorno.
    """

    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""
    session_secret: str = ""
    frontend_origin: str = ""
    cookie_secure: bool = True

    @classmethod
    def from_env(cls) -> "OAuthConfig":
        # `SESSION_SECRET` es obligatorio para firmar cookies. Si no está en el
        # entorno (p. ej. arranque local o test sin override), se genera uno
        # efímero para no romper el arranque; en producción debe definirse para
        # que las sesiones sobrevivan a reinicios.
        session_secret = os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)
        return cls(
            client_id=os.getenv("DISCORD_CLIENT_ID", ""),
            client_secret=os.getenv("DISCORD_CLIENT_SECRET", ""),
            redirect_uri=os.getenv("OAUTH_REDIRECT_URI", ""),
            session_secret=session_secret,
            frontend_origin=os.getenv("FRONTEND_ORIGIN", ""),
            cookie_secure=os.getenv("COOKIE_SECURE", "1") not in ("0", "false", "False"),
        )


class SessionManager:
    """Serializa/deserializa la Sesion_Web en una cookie firmada (stateless).

    Usa `itsdangerous.URLSafeTimedSerializer` para firmar el contenido con
    `SESSION_SECRET`. La cookie no se cifra (no contiene secretos más allá del
    `user_id`), pero sí se firma para impedir manipulación (Req 7.3/7.4).
    """

    def __init__(self, secret: str, *, cookie_secure: bool = True):
        if not secret:
            raise ValueError("SESSION_SECRET no puede estar vacío")
        self._session_serializer = URLSafeTimedSerializer(secret, salt="mhub-session")
        self._state_serializer = URLSafeTimedSerializer(secret, salt="mhub-oauth-state")
        self._cookie_secure = cookie_secure

    # --- Sesión -----------------------------------------------------------
    def serialize(self, identity: Identity) -> str:
        return self._session_serializer.dumps(
            {
                "user_id": identity.user_id,
                "username": identity.username,
                "avatar": identity.avatar,
            }
        )

    def deserialize(self, token: str) -> Optional[Identity]:
        """Devuelve la `Identity` o `None` si la cookie es inválida/expirada."""
        try:
            data = self._session_serializer.loads(token, max_age=SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return None
        user_id = data.get("user_id") if isinstance(data, dict) else None
        if not user_id:
            return None
        return Identity(
            user_id=str(user_id),
            username=data.get("username"),
            avatar=data.get("avatar"),
        )

    def set_session_cookie(self, response: Response, identity: Identity) -> None:
        response.set_cookie(
            key=SESSION_COOKIE,
            value=self.serialize(identity),
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=self._cookie_secure,
            samesite="lax",
            path="/",
        )

    def clear_session_cookie(self, response: Response) -> None:
        response.delete_cookie(key=SESSION_COOKIE, path="/")

    # --- State anti-CSRF --------------------------------------------------
    def make_state(self) -> str:
        return self._state_serializer.dumps(secrets.token_urlsafe(16))

    def verify_state(self, token: Optional[str]) -> bool:
        if not token:
            return False
        try:
            self._state_serializer.loads(token, max_age=STATE_MAX_AGE)
            return True
        except (BadSignature, SignatureExpired):
            return False

    def set_state_cookie(self, response: Response, state: str) -> None:
        response.set_cookie(
            key=STATE_COOKIE,
            value=state,
            max_age=STATE_MAX_AGE,
            httponly=True,
            secure=self._cookie_secure,
            samesite="lax",
            path="/",
        )

    def clear_state_cookie(self, response: Response) -> None:
        response.delete_cookie(key=STATE_COOKIE, path="/")


class DiscordOAuthClient:
    """Cliente mínimo de Discord OAuth2, fácilmente mockeable en tests.

    Encapsula las dos llamadas de red del callback (intercambio de `code` y
    `GET /users/@me`). En los tests se monkeypatchea `exchange_code` y
    `fetch_user` para no tocar la red.
    """

    def __init__(self, config: OAuthConfig):
        self._config = config

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self._config.client_id,
            "redirect_uri": self._config.redirect_uri,
            "response_type": "code",
            "scope": "identify",
            "state": state,
        }
        return f"{DISCORD_AUTHORIZE_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict:
        """Intercambia el `code` por un token de acceso (POST /oauth2/token)."""
        data = {
            "client_id": self._config.client_id,
            "client_secret": self._config.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._config.redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = httpx.post(DISCORD_TOKEN_URL, data=data, headers=headers, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def fetch_user(self, access_token: str) -> dict:
        """Obtiene el perfil del usuario autenticado (GET /users/@me)."""
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = httpx.get(DISCORD_USER_URL, headers=headers, timeout=10.0)
        resp.raise_for_status()
        return resp.json()


def get_current_identity(request: Request) -> Identity:
    """Dependencia FastAPI: identidad de la Sesion_Web o 401 (Req 7.3/7.4).

    Lee la cookie de sesión, la verifica con el `SessionManager` inyectado en
    `app.state.session_manager` y devuelve la `Identity`. Si la cookie falta o es
    inválida, lanza `401 Unauthorized`. La identidad procede SIEMPRE de la
    sesión, nunca del cuerpo de la petición (Req 7.4).
    """
    session_manager: Optional[SessionManager] = getattr(
        request.app.state, "session_manager", None
    )
    if session_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Sesión no configurada",
        )

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado"
        )

    identity = session_manager.deserialize(token)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Sesión inválida"
        )
    return identity


def register_auth_routes(app, *, config: OAuthConfig) -> None:
    """Monta las rutas de autenticación y deja el estado en `app.state`.

    Guarda en `app.state` el `SessionManager`, el `DiscordOAuthClient` y la
    `OAuthConfig` para que las dependencias (`get_current_identity`) y las rutas
    los consuman. Llamado desde `build_app(bot)`.
    """
    session_manager = SessionManager(
        config.session_secret, cookie_secure=config.cookie_secure
    )
    oauth_client = DiscordOAuthClient(config)

    app.state.oauth_config = config
    app.state.session_manager = session_manager
    app.state.oauth_client = oauth_client

    @app.get("/auth/login")
    async def auth_login(request: Request):
        """Redirige a Discord para iniciar el flujo OAuth2 (Req 7.1)."""
        oauth: DiscordOAuthClient = request.app.state.oauth_client
        sm: SessionManager = request.app.state.session_manager
        state = sm.make_state()
        response = RedirectResponse(
            url=oauth.authorize_url(state),
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )
        sm.set_state_cookie(response, state)
        return response

    @app.get("/auth/callback")
    async def auth_callback(request: Request, code: str = "", state: str = ""):
        """Valida `state`, intercambia `code`, crea la Sesion_Web (Req 7.2)."""
        oauth: DiscordOAuthClient = request.app.state.oauth_client
        sm: SessionManager = request.app.state.session_manager
        cfg: OAuthConfig = request.app.state.oauth_config

        # Anti-CSRF: el `state` recibido debe estar firmado y coincidir con la
        # cookie emitida en /auth/login.
        cookie_state = request.cookies.get(STATE_COOKIE)
        if not code or not state or state != cookie_state or not sm.verify_state(state):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Estado OAuth inválido",
            )

        try:
            token_data = oauth.exchange_code(code)
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError("token de acceso ausente")
            user = oauth.fetch_user(access_token)
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Fallo en el intercambio OAuth",
            ) from exc

        user_id = user.get("id")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Perfil de Discord sin id",
            )

        # La Identidad se deriva EXCLUSIVAMENTE del perfil de Discord (sesión),
        # nunca de datos del cuerpo de la petición (Req 7.4).
        identity = Identity(
            user_id=str(user_id),
            username=user.get("username"),
            avatar=user.get("avatar"),
        )

        redirect_to = cfg.frontend_origin or "/"
        response = RedirectResponse(
            url=redirect_to, status_code=status.HTTP_307_TEMPORARY_REDIRECT
        )
        sm.set_session_cookie(response, identity)
        sm.clear_state_cookie(response)
        return response

    @app.post("/auth/logout")
    async def auth_logout(request: Request):
        """Cierra la Sesion_Web borrando la cookie."""
        sm: SessionManager = request.app.state.session_manager
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        sm.clear_session_cookie(response)
        return response

    @app.get("/api/me")
    async def api_me(identity: Identity = Depends(get_current_identity)):
        """Identidad de la sesión actual (401 si no hay sesión, Req 7.3)."""
        return identity.to_public_dict()
