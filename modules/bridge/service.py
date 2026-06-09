"""Lógica del Modulo_Puente: relaciona los roles de un Evento con las Skills
(Personas) del directorio.

`BridgeService` es agnóstico de Discord (no importa el paquete `discord`), de
modo que puede testearse en local sin el bot. Solo lee a través de
`RegistryStore` y `EventStore`; no reimplementa su lógica de negocio (Req 4.6).

Estrategia de matching (Req 4.2): correspondencia por **clave normalizada**. La
clave de un rol (p. ej. "backend") se normaliza (sin tildes, minúsculas) y se
considera que una entrada del directorio le corresponde cuando su
`norm_category` o su `norm_name` coincide con esa clave. Se usa la misma
normalización que `RegistryStore` para garantizar consistencia.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    """Una entrada del directorio asociada a un `user_id` de Discord.

    Representa a una Persona candidata a ocupar un rol: incluye su `user_id`
    (necesario para excluir inscritos e identificarla), su proveedor (nombre
    mostrado), la categoría/nombre de la Skill y el enlace asociado.
    """
    user_id: int
    provider: str
    category: str
    name: str
    link: str


class BridgeService:
    """Puente entre el directorio de Skills (RegistryStore) y los Eventos
    (EventStore). No persiste estado propio: deriva todo de ambos stores."""

    def __init__(self, registry_store, event_store):
        self._registry = registry_store
        self._events = event_store

    def _normalize(self, text):
        # Reutiliza la normalización del directorio para que las claves casen
        # exactamente igual que al registrar/buscar entradas.
        return self._registry.normalize_text(text)

    @staticmethod
    def _enrolled_user_ids(state):
        """Conjunto de `user_id` ya inscritos en CUALQUIER rol del evento."""
        enrolled = set()
        for members in (state or {}).values():
            for member in members:
                enrolled.add(member.get("id"))
        return enrolled

    def suggest_for_event(self, event_id):
        """Para cada rol del Evento, devuelve las Personas del directorio cuya
        Skill se corresponde con el rol, EXCLUYENDO a las ya inscritas en el
        Evento (Req 4.5).

        Devuelve `dict[str, list[Persona]]` con una clave por cada rol de la
        composición. Un rol sin candidatos aparece con una lista vacía
        (Req 4.4). Si el evento no existe, devuelve `{}`.
        """
        event = self._events.get(event_id)
        if event is None:
            return {}

        composition = event.get("composition") or {}
        enrolled = self._enrolled_user_ids(event.get("state"))
        entries = self._registry.all_entries()

        suggestions = {}
        for role_key in composition:
            norm_role = self._normalize(role_key)
            candidates = []
            seen = set()
            for entry in entries:
                if entry["user_id"] in enrolled:
                    continue
                if entry["norm_category"] != norm_role and entry["norm_name"] != norm_role:
                    continue
                # Una misma Persona puede tener varias entradas que casen con el
                # rol; se sugiere una sola vez por rol.
                if entry["user_id"] in seen:
                    continue
                seen.add(entry["user_id"])
                candidates.append(Persona(
                    user_id=entry["user_id"],
                    provider=entry["provider"],
                    category=entry["category"],
                    name=entry["name"],
                    link=entry["link"],
                ))
            suggestions[role_key] = candidates
        return suggestions

    def events_for_skill(self, guild_id, skill):
        """Devuelve los Eventos del Servidor `guild_id` cuyos roles se
        corresponden con `skill` (por clave normalizada) (Req 4.3)."""
        norm_skill = self._normalize(skill)
        result = []
        for event in self._events.all():
            if event.get("guild_id") != guild_id:
                continue
            composition = event.get("composition") or {}
            if any(self._normalize(role_key) == norm_skill for role_key in composition):
                result.append(event)
        return result
