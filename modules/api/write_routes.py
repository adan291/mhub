"""Endpoints de Escritura_Autenticada de la API_Web (Req 8).

Estos endpoints SÍ requieren una Sesion_Web válida (Req 7.3/12.2): cada uno
depende de `get_current_identity`, que devuelve la `Identity` firmada en la
cookie o un 401. La Identidad de toda escritura procede SIEMPRE de la sesión y
NUNCA del cuerpo de la petición (Req 7.4, 8.1): los modelos de cuerpo solo
declaran los campos de negocio y cualquier `user_id` enviado por el cliente se
ignora.

Reutilizan la MISMA fuente de verdad y lógica de negocio que el bot, sin
reimplementarla (Req 5.2, 5.5, 13.1, 13.2):
  - Inscripción/movimiento/salida vía `EventManager.toggle` (misma lógica de
    cupos y movimiento de rol que en Discord, Req 8.1, 8.2, 8.6).
  - Alta de entradas vía `RegistryStore.add`, asociadas al `user_id` de la
    sesión (Req 8.3).
  - Borrado de entradas solo del propietario vía `RegistryStore.delete_by_id`
    (403 si es ajena, sin eliminar; Req 8.4, 8.5).

Endpoints:
  - POST   /api/events/{event_id}/join     body {role}   (Req 8.1, 8.6)
  - POST   /api/events/{event_id}/leave                  (Req 8.2)
  - POST   /api/registry                   body {entry}  (Req 8.3)
  - DELETE /api/registry/{entry_id}                      (Req 8.4, 8.5)

`register_write_routes(app)` se invoca desde `build_app(bot)` tras registrar las
rutas de autenticación y lectura. Los imports de `auth`/`dtos` son robustos
frente a la carga por ruta de archivo (los tests cargan `app.py` con importlib
sin contexto de paquete, para evitar `discord`).
"""
from fastapi import Depends, HTTPException, Request, Response, status
from pydantic import BaseModel


def _load_sibling(filename, modname, package_attr):
    """Importa un submódulo hermano (`auth`/`dtos`) de forma robusta.

    Dentro del paquete `modules.api` funciona el import relativo. Cuando
    `app.py`/`write_routes.py` se cargan por ruta (tests con importlib, sin
    contexto de paquete), se carga el fichero por ruta junto a este módulo.
    """
    try:  # ruta normal: dentro del paquete modules.api
        from importlib import import_module
        module = import_module(f".{package_attr}", __package__)
        return module
    except (ImportError, TypeError):
        import importlib.util
        import os
        import sys

        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), filename)
        spec = importlib.util.spec_from_file_location(modname, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)
        return module


def _load_auth():
    return _load_sibling("auth.py", "mhub_api_auth", "auth")


def _load_dtos():
    return _load_sibling("dtos.py", "mhub_api_dtos", "dtos")


def _load_sync():
    return _load_sibling("sync.py", "mhub_api_sync", "sync")


class JoinBody(BaseModel):
    """Cuerpo de la inscripción a un rol.

    Solo declara `role`: la Identidad procede de la sesión, nunca del cuerpo
    (Req 7.4, 8.1). Cualquier campo extra (p. ej. un `user_id` enviado por el
    cliente) se ignora por defecto.
    """

    role: str


class RegistryBody(BaseModel):
    """Cuerpo del alta de una entrada del directorio.

    No incluye `user_id`: la entrada se asocia al `user_id` de la sesión
    (Req 8.3). Un `user_id` enviado en el cuerpo se ignora.
    """

    provider: str
    category: str
    name: str
    link: str


def _resync_event(app, event_id):
    """Pide a HubBot reeditar el mensaje del Evento en Discord (Req 9.1).

    Reúne el bot inyectado en `app.state.bot` y delega en `sync.resync_event`,
    que programa la reedición en el loop del bot sin bloquear. Si no hay un bot
    con loop real (p. ej. en tests con un objeto ligero), es un no-op: el cambio
    queda persistido igualmente (Req 9.3).
    """
    bot = getattr(app.state, "bot", None)
    if bot is None or getattr(bot, "loop", None) is None:
        return None
    sync = _load_sync()
    return sync.resync_event(bot, event_id)


def _publish_event_change(app, event_id, status_value=None):
    """Publica un cambio de Evento en el EventBus si está presente (Req 10.2)."""
    bus = getattr(app.state, "event_bus", None)
    if bus is None:
        return
    change = {"type": "event_changed", "event_id": event_id}
    if status_value is not None:
        change["status"] = status_value
    bus.publish(change)


def register_write_routes(app):
    """Monta los endpoints de Escritura_Autenticada en la app."""
    auth = _load_auth()
    dtos = _load_dtos()
    get_current_identity = auth.get_current_identity
    Identity = auth.Identity

    @app.post("/api/events/{event_id}/join")
    async def join_event(
        event_id: int,
        body: JoinBody,
        request: Request,
        identity: "Identity" = Depends(get_current_identity),
    ):
        """Apunta/mueve al usuario de la sesión al rol indicado (Req 8.1, 8.6).

        La identidad procede SIEMPRE de la sesión (Req 7.4). Aplica la misma
        lógica de cupos/movimiento que Discord vía `EventManager.toggle`.
        """
        manager = request.app.state.signup_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")

        result, event = manager.toggle(
            event_id, body.role, identity.user_id,
            identity.username or identity.user_id,
        )

        if result == manager.NOT_FOUND:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")
        if result == manager.FULL:
            # Rol completo: se rechaza la inscripción (Req 8.6).
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="El rol está completo")

        # JOINED / MOVED / LEFT → estado actualizado del Evento.
        _publish_event_change(request.app, event_id, event.get("status"))
        _resync_event(request.app, event_id)
        return dtos.event_to_dto(event).model_dump()

    @app.post("/api/events/{event_id}/leave")
    async def leave_event(
        event_id: int,
        request: Request,
        identity: "Identity" = Depends(get_current_identity),
    ):
        """Retira a la Identidad de la sesión del Evento (Req 8.2).

        Localiza el rol actual del usuario en el Estado_Ocupacion y lo desapunta
        vía `toggle` (sin duplicar lógica de dominio). Si el usuario no está
        inscrito, responde de forma idempotente con el estado vigente.
        """
        manager = request.app.state.signup_manager
        if manager is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")

        event = manager.get(event_id)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Evento no encontrado")

        state = event.get("state") or {}
        current_role = None
        for role_key, members in state.items():
            if any(str(m.get("id")) == str(identity.user_id) for m in members):
                current_role = role_key
                break

        if current_role is None:
            # No estaba inscrito: idempotente, devolvemos el estado actual.
            return dtos.event_to_dto(event).model_dump()

        _result, updated = manager.toggle(
            event_id, current_role, identity.user_id,
            identity.username or identity.user_id,
        )
        _publish_event_change(request.app, event_id, updated.get("status"))
        _resync_event(request.app, event_id)
        return dtos.event_to_dto(updated).model_dump()

    @app.post("/api/registry", status_code=status.HTTP_201_CREATED)
    async def create_registry_entry(
        body: RegistryBody,
        response: Response,
        request: Request,
        identity: "Identity" = Depends(get_current_identity),
    ):
        """Registra una entrada asociada al `user_id` de la sesión (Req 8.3)."""
        store = request.app.state.registry_store
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Directorio no disponible")

        created = store.add(
            body.provider, body.category, body.name, body.link, identity.user_id)

        # Recuperamos el id de la entrada del usuario para que el cliente pueda
        # borrarla luego (DELETE /api/registry/{id}). No se expone el user_id.
        entry_id = None
        for row in store.all_entries():
            if (
                row["category"] == body.category
                and row["name"] == body.name
                and row["link"] == body.link
                and str(row["user_id"]) == str(identity.user_id)
            ):
                entry_id = row["id"]
                break

        if not created:
            # Ya existía una entrada idéntica del mismo usuario: idempotente.
            response.status_code = status.HTTP_200_OK

        return {
            "id": entry_id,
            "provider": body.provider,
            "category": body.category,
            "name": body.name,
            "link": body.link,
        }

    @app.delete("/api/registry/{entry_id}")
    async def delete_registry_entry(
        entry_id: int,
        request: Request,
        identity: "Identity" = Depends(get_current_identity),
    ):
        """Borra una entrada solo si pertenece a la sesión (Req 8.4, 8.5).

        403 si la entrada es de otra Identidad (sin eliminarla); 404 si no
        existe; 204 si se borra correctamente.
        """
        store = request.app.state.registry_store
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Directorio no disponible")

        outcome = store.delete_by_id(entry_id, identity.user_id)
        if outcome == "not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Entrada no encontrada")
        if outcome == "no_permission":
            # No es el propietario: se conserva la entrada (Req 8.5).
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No puedes borrar una entrada ajena")

        return Response(status_code=status.HTTP_204_NO_CONTENT)
