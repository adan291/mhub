"""DTOs (modelos de respuesta) de la API_Web para la Lectura_Publica.

Estos modelos pydantic definen EXACTAMENTE qué se expone en las respuestas
públicas. La invariante central (Property 1 / Req 6.3, 10.5, 12.6) es que
NINGÚN DTO incluye el `user_id` ni tokens de sesión: los miembros de un rol se
exponen únicamente por su nombre mostrado, y las entradas del directorio y las
sugerencias del puente exponen proveedor/categoría/nombre/enlace pero nunca el
`user_id` propietario.

Los DTOs se construyen a partir de los objetos de dominio (eventos del
`EventManager`/`EventStore`, entradas del `RegistryStore`, sugerencias del
`BridgeService`), que son la única fuente de verdad (Req 5.2, 13.1, 13.2). Las
funciones `*_to_dto` actúan como capa de proyección que descarta los datos
sensibles al cruzar la frontera de la API.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class RoleDTO(BaseModel):
    """Un rol de un Evento: cupo, ocupación y nombres (SIN `user_id`)."""

    role: str
    emoji: Optional[str] = None
    capacity: int
    occupied: int
    free: int
    members: List[str]  # nombres mostrados, nunca user_id


class EventDTO(BaseModel):
    """Un Evento proyectado para la web: título, estado y composición/ocupación."""

    id: str
    title: str
    status: str
    created_at: Optional[str] = None
    roles: List[RoleDTO]


class EntryDTO(BaseModel):
    """Una entrada del directorio: categoría, nombre, enlace y proveedor(es).

    NUNCA incluye el `user_id` propietario (Req 6.2, 6.3).
    """

    category: str
    name: str
    link: str
    providers: List[str]


class SuggestionDTO(BaseModel):
    """Una Persona candidata sugerida para un rol (SIN `user_id`)."""

    provider: str
    category: str
    name: str
    link: str


class RoleSuggestionsDTO(BaseModel):
    """Candidatos sugeridos para un rol concreto de un Evento."""

    role: str
    candidates: List[SuggestionDTO]


def event_to_dto(event: dict) -> EventDTO:
    """Proyecta un evento de dominio a `EventDTO`, descartando los `user_id`.

    `event` es el dict que devuelven `EventManager`/`EventStore` con claves
    `message_id`, `title`, `status`, `created_at`, `composition`
    (`{rol: {emoji, plazas}}`) y `state` (`{rol: [{id, name}]}`). De cada
    miembro se toma SOLO el nombre.
    """
    composition = event.get("composition") or {}
    state = event.get("state") or {}

    roles: List[RoleDTO] = []
    for role_key, cfg in composition.items():
        members = state.get(role_key) or []
        capacity = int(cfg.get("plazas", 0)) if isinstance(cfg, dict) else 0
        occupied = len(members)
        roles.append(
            RoleDTO(
                role=role_key,
                emoji=cfg.get("emoji") if isinstance(cfg, dict) else None,
                capacity=capacity,
                occupied=occupied,
                free=max(capacity - occupied, 0),
                members=[m.get("name", "") for m in members],
            )
        )

    return EventDTO(
        id=str(event.get("message_id")),
        title=event.get("title") or "",
        status=event.get("status") or "activo",
        created_at=event.get("created_at"),
        roles=roles,
    )


def list_all_to_entry_dtos(rows) -> List[EntryDTO]:
    """Proyecta el resultado de `RegistryStore.list_all()` a `EntryDTO`.

    `rows` es una lista de tuplas `(category, name, link, [providers])`; el
    `user_id` ya no está presente en esa proyección, pero igualmente solo se
    copian los campos públicos.
    """
    return [
        EntryDTO(category=r[0], name=r[1], link=r[2], providers=list(r[3]))
        for r in rows
    ]


def search_to_entry_dtos(category: str, rows) -> List[EntryDTO]:
    """Proyecta `RegistryStore.search()` a `EntryDTO`.

    `search` devuelve tuplas `(name, link, [providers])` sin categoría, por lo
    que esta se toma del parámetro de búsqueda.
    """
    return [
        EntryDTO(category=category, name=r[0], link=r[1], providers=list(r[2]))
        for r in rows
    ]


def suggestions_to_dtos(suggestions: dict) -> List[RoleSuggestionsDTO]:
    """Proyecta `BridgeService.suggest_for_event()` a DTOs sin `user_id`.

    `suggestions` es `dict[str, list[Persona]]`; de cada `Persona` se toman
    proveedor/categoría/nombre/enlace, descartando su `user_id` (Req 6.3).
    """
    result: List[RoleSuggestionsDTO] = []
    for role_key, personas in suggestions.items():
        candidates = [
            SuggestionDTO(
                provider=p.provider,
                category=p.category,
                name=p.name,
                link=p.link,
            )
            for p in personas
        ]
        result.append(RoleSuggestionsDTO(role=role_key, candidates=candidates))
    return result
