"""Endpoints de Lectura_Publica de la API_Web (Req 6).

Estos endpoints NO requieren Sesion_Web (Req 6.4) y exponen exclusivamente
datos sin `user_id` (Req 6.3, Property 1) proyectando los objetos de dominio a
los DTOs de `dtos.py`. Leen siempre desde la fuente de verdad compartida con el
bot (`app.state.signup_manager`, `app.state.registry_store`,
`app.state.bridge_service`), sin reimplementar lógica de negocio (Req 5.2, 5.5,
13.1, 13.2).

Endpoints:
  - GET /api/events                          → Eventos activos (Req 6.1, 11.4)
  - GET /api/events/archived                 → Historial archivado (Req 11.4)
  - GET /api/events/{id}                      → Detalle de un evento
  - GET /api/registry                        → Directorio (Req 6.2)
  - GET /api/registry/search?categoria=&q=    → Búsqueda en el directorio
  - GET /api/bridge/events/{id}/suggestions   → Sugerencias por rol (Req 4)

`register_read_routes(app)` se invoca desde `build_app(bot)`. El import de los
DTOs es robusto frente a la carga por ruta de archivo (los tests cargan
`app.py` con importlib sin contexto de paquete, para evitar `discord`).
"""
from fastapi import HTTPException, Request, status


def _load_dtos():
    """Importa el submódulo `dtos` de forma robusta.

    Dentro del paquete `modules.api` funciona el import relativo. Cuando
    `app.py`/`read_routes.py` se cargan por ruta (tests con importlib, sin
    contexto de paquete), se carga `dtos.py` por ruta junto a este fichero.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from . import dtos  # type: ignore
        return dtos
    except ImportError:
        import importlib.util
        import os
        import sys

        dtos_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "dtos.py")
        spec = importlib.util.spec_from_file_location("mhub_api_dtos", dtos_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["mhub_api_dtos"] = module
        spec.loader.exec_module(module)
        return module


def register_read_routes(app):
    """Monta los endpoints de Lectura_Publica en la app."""
    dtos = _load_dtos()

    @app.get("/api/events")
    async def list_events(request: Request):
        """Eventos activos con composición y ocupación/libres por rol (Req 6.1)."""
        manager = request.app.state.signup_manager
        if manager is None:
            return []
        events = manager.list_active()
        return [dtos.event_to_dto(e).model_dump() for e in events]

    @app.get("/api/events/archived")
    async def list_archived_events(request: Request):
        """Historial de eventos archivados, separado de los activos (Req 11.4)."""
        manager = request.app.state.signup_manager
        if manager is None:
            return []
        events = manager.list_archived()
        return [dtos.event_to_dto(e).model_dump() for e in events]

    @app.get("/api/events/{event_id}")
    async def get_event(event_id: int, request: Request):
        """Detalle de un evento por su id (message_id)."""
        manager = request.app.state.signup_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")
        event = manager.get(event_id)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")
        return dtos.event_to_dto(event).model_dump()

    @app.get("/api/registry")
    async def list_registry(request: Request):
        """Directorio completo: categoría, nombre, enlace y proveedor (Req 6.2)."""
        store = request.app.state.registry_store
        if store is None:
            return []
        rows = store.list_all()
        return [d.model_dump() for d in dtos.list_all_to_entry_dtos(rows)]

    @app.get("/api/registry/search")
    async def search_registry(request: Request, categoria: str = "", q: str = ""):
        """Búsqueda por categoría (`categoria`) y nombre (`q`) en el directorio."""
        store = request.app.state.registry_store
        if store is None:
            return []
        rows = store.search(categoria, q)
        return [d.model_dump() for d in dtos.search_to_entry_dtos(categoria, rows)]

    @app.get("/api/bridge/events/{event_id}/suggestions")
    async def event_suggestions(event_id: int, request: Request):
        """Sugerencias de Personas por rol para un evento (Req 4), sin `user_id`."""
        bridge = getattr(request.app.state, "bridge_service", None)
        if bridge is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Sugerencias no disponibles")
        suggestions = bridge.suggest_for_event(event_id)
        return [d.model_dump() for d in dtos.suggestions_to_dtos(suggestions)]
