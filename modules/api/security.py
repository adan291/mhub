"""Seguridad transversal de la API_Web (Req 12).

Este módulo añade dos protecciones transversales sobre la app FastAPI, sin
acoplar el resto de endpoints:

  - **Rate limiting en memoria** (Req 12.4/12.5): un middleware que cuenta las
    peticiones por cliente dentro de una ventana deslizante y responde `429`
    cuando se supera el límite configurado. La clave de cliente es la
    Identidad (cookie de sesión) si está presente, y en su defecto la IP de
    origen, de modo que el límite se aplica "por IP/identidad". El límite se
    configura con la variable de entorno `RATE_LIMIT_PER_MINUTE`:
      * sin definir  → sin límite (rate limiting desactivado).
      * `0`          → se rechaza TODA petición con `429` (Req 12.5).
      * `N` (> 0)    → se permiten hasta `N` peticiones por ventana (Req 12.4).

  - **CORS restringido** a `FRONTEND_ORIGIN` (Req 12): solo ese origen puede
    hacer peticiones desde el navegador con credenciales (cookies de sesión).

La exigencia de Sesion_Web para toda Escritura_Autenticada (Req 12.2) ya la
garantizan los endpoints de `write_routes.py` mediante la dependencia
`get_current_identity` (401 si falta o es inválida); aquí no se duplica.

`build_app(bot)` invoca `register_security(app)` para montar ambas piezas. El
límite vigente se guarda en `app.state.rate_limit_per_minute` para que pueda
ajustarse en tests, y el contador en memoria en `app.state.rate_limiter`.
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


# Ventana por defecto del rate limiting: un minuto (la unidad de
# `RATE_LIMIT_PER_MINUTE`).
DEFAULT_WINDOW_SECONDS = 60.0


def _load_session_cookie_name() -> str:
    """Obtiene el nombre de la cookie de sesión desde `auth`, de forma robusta.

    Dentro del paquete `modules.api` funciona el import relativo. Cuando los
    módulos se cargan por ruta (tests con importlib, sin contexto de paquete),
    se carga `auth.py` por ruta junto a este fichero. Si algo falla, se usa el
    nombre por defecto conocido.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import auth  # type: ignore
        return auth.SESSION_COOKIE
    except ImportError:
        try:
            import importlib.util
            import sys

            auth_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "auth.py")
            spec = importlib.util.spec_from_file_location(
                "mhub_api_auth", auth_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules["mhub_api_auth"] = module
            spec.loader.exec_module(module)
            return module.SESSION_COOKIE
        except Exception:
            return "mhub_session"


def rate_limit_from_env() -> Optional[int]:
    """Lee `RATE_LIMIT_PER_MINUTE` del entorno.

    Devuelve `None` si la variable no está definida (rate limiting
    desactivado), o el entero configurado en otro caso (incluido `0`, que
    significa rechazar todo, Req 12.5). Un valor no numérico se trata como
    `None` para no romper el arranque.
    """
    raw = os.getenv("RATE_LIMIT_PER_MINUTE")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


class RateLimiter:
    """Contador de peticiones por clave con ventana deslizante en memoria.

    Mantiene, por cada clave de cliente, una cola con las marcas de tiempo de
    las peticiones recientes. En cada comprobación descarta las que caen fuera
    de la ventana y decide si la nueva petición cabe dentro del límite. Es
    seguro entre hilos (la API corre en un hilo de Uvicorn aparte del bot).
    """

    def __init__(self, window_seconds: float = DEFAULT_WINDOW_SECONDS):
        self._window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: Optional[int], *, now: Optional[float] = None) -> bool:
        """Indica si una petición de `key` está permitida bajo `limit`.

        - `limit is None` → sin límite: siempre permitido.
        - `limit <= 0`    → se rechaza siempre (Req 12.5).
        - `limit > 0`     → permitido si en la ventana hay menos de `limit`
          peticiones previas; en ese caso registra la nueva (Req 12.4).
        """
        if limit is None:
            return True
        if limit <= 0:
            return False

        current = time.monotonic() if now is None else now
        cutoff = current - self._window
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(current)
            return True


def client_key(request, session_cookie: str) -> str:
    """Calcula la clave de cliente para el rate limiting (identidad o IP).

    Si la petición trae cookie de sesión, se usa esa Identidad como clave (el
    límite es por identidad). En su defecto se usa la IP de origen.
    """
    token = request.cookies.get(session_cookie)
    if token:
        return f"session:{token}"
    client = request.client
    ip = client.host if client is not None else "unknown"
    return f"ip:{ip}"


def register_security(app, *, frontend_origin: Optional[str] = None,
                      rate_limit_per_minute: Optional[int] = "__from_env__",
                      window_seconds: float = DEFAULT_WINDOW_SECONDS) -> None:
    """Monta el rate limiting y el CORS restringido sobre la app (Req 12).

    - `frontend_origin`: origen permitido para CORS. Si es `None`, se lee de
      `FRONTEND_ORIGIN`. Si queda vacío, no se permite ningún origen
      cross-site.
    - `rate_limit_per_minute`: límite por ventana. Por defecto se lee del
      entorno; los tests pueden pasar un entero explícito (incluido `0`).
    """
    session_cookie = _load_session_cookie_name()

    if rate_limit_per_minute == "__from_env__":
        rate_limit_per_minute = rate_limit_from_env()

    # Estado mutable para que el límite pueda ajustarse en tiempo de ejecución
    # (y en tests) sin reconstruir la app.
    app.state.rate_limit_per_minute = rate_limit_per_minute
    app.state.rate_limiter = RateLimiter(window_seconds=window_seconds)

    @app.middleware("http")
    async def _rate_limit_middleware(request, call_next):
        limiter: RateLimiter = request.app.state.rate_limiter
        limit = request.app.state.rate_limit_per_minute
        key = client_key(request, session_cookie)
        if not limiter.allow(key, limit):
            return JSONResponse(
                status_code=429,
                content={"detail": "Demasiadas peticiones"},
            )
        return await call_next(request)

    # CORS restringido a FRONTEND_ORIGIN (Req 12). Con credenciales (cookies),
    # el origen no puede ser comodín: solo se permite el origen configurado.
    if frontend_origin is None:
        frontend_origin = os.getenv("FRONTEND_ORIGIN", "")
    allowed_origins = [frontend_origin] if frontend_origin else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
