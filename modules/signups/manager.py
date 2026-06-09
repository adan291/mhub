import json


class CompositionError(ValueError):
    """Error tipado de validación de una Composicion de evento.

    `code` permite a las capas superiores (cog Discord, API web) reaccionar de
    forma uniforme: el cog responde de forma efímera y la API devuelve 422.
    Códigos posibles:
      - "empty"         → la composición no tiene ningún rol (Req 2.4)
      - "invalid_plazas"→ un rol tiene plazas no enteras o < 1 (Req 2.3)
      - "duplicate_key" → dos roles comparten la misma clave (Req 2.5)
    """

    def __init__(self, code, message, role=None):
        super().__init__(message)
        self.code = code
        self.role = role

    def __str__(self):
        return self.args[0] if self.args else self.code


class EventManager:
    """Lógica de negocio de los eventos de inscripción, agnóstica de Discord.

    A partir de mhub-platform, cada evento se gestiona con su propia
    Composicion (roles + plazas + emoji) persistida junto a él, sin depender de
    una plantilla global. `template_key` se conserva solo como metadato opcional
    del origen del evento.

    El estado de ocupación de un evento es:
        { role_key: [ {"id": int, "name": str}, ... ] }
    """

    JOINED = "joined"
    LEFT = "left"
    MOVED = "moved"
    FULL = "full"
    NOT_FOUND = "not_found"

    # Estados de Ciclo_De_Vida (Req 11)
    ACTIVE = "activo"
    COMPLETED = "completado"
    ARCHIVED = "archivado"

    def __init__(self, store, templates, bus=None):
        self.store = store
        self.templates = templates
        # EventBus opcional (pub/sub en memoria). Cuando está presente, el
        # manager publica un cambio tras cada mutación de estado (Req 10.1,
        # 10.2). Es opcional para que la capa de dominio siga siendo usable y
        # testeable sin un loop de asyncio.
        self.bus = bus

    def _publish(self, change_type, event_id, status=None):
        """Publica un cambio en el EventBus si hay uno configurado.

        No-op seguro cuando no hay bus: la capa de dominio no depende de él.
        """
        if self.bus is None:
            return
        change = {"type": change_type, "event_id": event_id}
        if status is not None:
            change["status"] = status
        self.bus.publish(change)

    # ------------------------------------------------------------------ #
    # Plantillas (compatibilidad con el flujo actual basado en plantillas)
    # ------------------------------------------------------------------ #
    def get_template(self, template_key):
        return self.templates.get(template_key)

    def new_state(self, template_key):
        template = self.templates[template_key]
        return {role_key: [] for role_key in template["roles"].keys()}

    def composition_from_template(self, template_key):
        """Devuelve una copia de la Composicion de una plantilla (Req 2.6).

        Se devuelve una copia profunda para que mutaciones posteriores del
        evento no alteren la plantilla compartida en memoria.
        """
        template = self.templates.get(template_key)
        if not template:
            return {}
        return json.loads(json.dumps(template.get("roles", {})))

    # ------------------------------------------------------------------ #
    # Validación de composición (Req 2.3, 2.4, 2.5 / Property 9)
    # ------------------------------------------------------------------ #
    def validate_composition(self, composition):
        """Valida una Composicion y devuelve una versión normalizada.

        Acepta un dict ``{clave: {"emoji": str|None, "plazas": int}}`` o una
        secuencia de pares ``(clave, cfg)``. La forma de pares permite detectar
        claves duplicadas (un dict no puede contenerlas).

        No modifica la entrada: construye y devuelve un nuevo dict normalizado
        (Property 9). Lanza :class:`CompositionError` con el código y el rol
        afectado cuando la composición es inválida.
        """
        if isinstance(composition, dict):
            pairs = list(composition.items())
        else:
            pairs = [(key, cfg) for key, cfg in composition]

        if not pairs:
            raise CompositionError(
                "empty", "La composición debe incluir al menos un rol.")

        normalized = {}
        seen = set()
        for key, cfg in pairs:
            if key in seen:
                raise CompositionError(
                    "duplicate_key",
                    f"Clave de rol duplicada: '{key}'.",
                    role=key)
            seen.add(key)

            plazas = cfg.get("plazas") if isinstance(cfg, dict) else None
            # bool es subclase de int: hay que excluirlo explícitamente.
            if not isinstance(plazas, int) or isinstance(plazas, bool) or plazas < 1:
                raise CompositionError(
                    "invalid_plazas",
                    f"El rol '{key}' debe tener un número entero de plazas ≥ 1.",
                    role=key)

            emoji = cfg.get("emoji") if isinstance(cfg, dict) else None
            normalized[key] = {"emoji": emoji, "plazas": plazas}

        return normalized

    # ------------------------------------------------------------------ #
    # Creación (Req 2.1, 2.2, 2.6, 11.1)
    # ------------------------------------------------------------------ #
    def create(self, message_id, channel_id, guild_id, title, composition, *,
               template_key=None):
        """Crea un evento con una Composicion ad-hoc o copiada de una plantilla.

        La Composicion se valida y se persiste junto al evento, de modo que el
        evento se gestiona con su propia composición con independencia de
        cambios posteriores en plantillas (Req 2.2 / Property 7). El evento se
        crea siempre en estado "activo" (Req 11.1).

        Devuelve la composición normalizada. Lanza :class:`CompositionError` si
        la composición es inválida (Req 2.3–2.5).
        """
        composition = self.validate_composition(composition)
        state = {role_key: [] for role_key in composition}
        self.store.create(
            message_id, channel_id, guild_id, template_key, title, state,
            composition=composition, status=self.ACTIVE)
        self._publish("event_created", message_id, self.ACTIVE)
        return composition

    def get(self, message_id):
        return self.store.get(message_id)

    # ------------------------------------------------------------------ #
    # Inscripción (toggle) + transición de Ciclo_De_Vida (Req 11.2)
    # ------------------------------------------------------------------ #
    def _find_user_role(self, state, user_id):
        for role_key, members in state.items():
            if any(m["id"] == user_id for m in members):
                return role_key
        return None

    def toggle(self, message_id, role_key, user_id, user_name):
        """Apunta, mueve o saca al usuario del rol indicado.

        Usa la Composicion guardada en el propio evento (no la plantilla
        global), de modo que funciona igual para eventos ad-hoc y de plantilla.
        Tras la mutación recalcula el Ciclo_De_Vida: si todos los roles quedan
        llenos pasa a "completado" (Req 11.2); si deja de estar lleno vuelve a
        "activo".
        """
        event = self.store.get(message_id)
        if not event:
            return self.NOT_FOUND, None

        composition = event.get("composition") or {}
        if role_key not in composition:
            return self.NOT_FOUND, None

        state = event["state"]
        max_slots = composition[role_key]["plazas"]
        current_role = self._find_user_role(state, user_id)

        if current_role == role_key:
            state[role_key] = [m for m in state[role_key] if m["id"] != user_id]
            self.store.update_state(message_id, state)
            updated = self._sync_lifecycle(self._with_state(event, state))
            self._publish("event_changed", message_id, updated.get("status"))
            return self.LEFT, updated

        if len(state.get(role_key, [])) >= max_slots:
            return self.FULL, event

        result = self.JOINED
        if current_role is not None:
            state[current_role] = [m for m in state[current_role] if m["id"] != user_id]
            result = self.MOVED

        state.setdefault(role_key, [])
        state[role_key].append({"id": user_id, "name": user_name})
        self.store.update_state(message_id, state)
        updated = self._sync_lifecycle(self._with_state(event, state))
        self._publish("event_changed", message_id, updated.get("status"))
        return result, updated

    # ------------------------------------------------------------------ #
    # Ciclo de vida: completado / archivado / listados (Req 11)
    # ------------------------------------------------------------------ #
    def is_complete(self, event):
        """True si todos los roles de la composición del evento están llenos."""
        composition = event.get("composition")
        if not composition:
            return False
        state = event.get("state", {})
        for role_key, role_cfg in composition.items():
            if len(state.get(role_key, [])) < role_cfg["plazas"]:
                return False
        return True

    def _sync_lifecycle(self, event):
        """Ajusta el estado entre "activo" y "completado" según la ocupación.

        Nunca altera un evento "archivado": el archivado es una acción
        explícita del organizador (Req 11.3).
        """
        status = event.get("status", self.ACTIVE)
        if status == self.ARCHIVED:
            return event

        complete = self.is_complete(event)
        if complete and status != self.COMPLETED:
            self.store.update_status(event["message_id"], self.COMPLETED)
            event["status"] = self.COMPLETED
        elif not complete and status == self.COMPLETED:
            self.store.update_status(event["message_id"], self.ACTIVE)
            event["status"] = self.ACTIVE
        return event

    def archive(self, event_id):
        """Marca un evento como "archivado" (Req 11.3).

        Devuelve el evento actualizado, o ``None`` si no existe.
        """
        event = self.store.get(event_id)
        if not event:
            return None
        self.store.update_status(event_id, self.ARCHIVED)
        event["status"] = self.ARCHIVED
        self._publish("event_archived", event_id, self.ARCHIVED)
        return event

    def list_active(self, guild_id=None):
        """Eventos en estado "activo" (Req 11.4 / 6.1), opcionalmente por guild."""
        return self._list_by_status(self.ACTIVE, guild_id)

    def list_archived(self, guild_id=None):
        """Eventos en estado "archivado" (historial, Req 11.4)."""
        return self._list_by_status(self.ARCHIVED, guild_id)

    def _list_by_status(self, status, guild_id):
        events = self.store.all()
        return [
            event for event in events
            if event.get("status", self.ACTIVE) == status
            and (guild_id is None or event.get("guild_id") == guild_id)
        ]

    @staticmethod
    def _with_state(event, state):
        event = dict(event)
        event["state"] = state
        return event
