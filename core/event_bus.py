"""EventBus interno: pub/sub en memoria para el núcleo de dominio.

Desacopla a los productores de cambios de estado (el bot de Discord vía
`EventManager`, y la API web) de los consumidores (el canal de tiempo real
SSE de la API). Cada suscriptor recibe una `asyncio.Queue` propia donde se
encolan los cambios publicados.

Reto clave de la plataforma (ver design.md → "dos loops/hilos coordinados"):
`publish` puede invocarse desde un hilo distinto al del loop donde viven las
colas (p. ej. desde el hilo de Uvicorn de la API). Como `asyncio.Queue` no es
segura entre hilos, la entrega real se programa en el loop propietario mediante
`loop.call_soon_threadsafe`.

Diseño deliberadamente tolerante: si no hay loop configurado o no hay
suscriptores, `publish` es un no-op seguro. Esto permite que la capa de dominio
(`EventManager`) publique tras cada mutación sin requerir un loop de asyncio en
contextos donde no lo hay (p. ej. los tests unitarios del dominio).
"""
import asyncio


class EventBus:
    """Bus de publicación/suscripción en memoria basado en `asyncio.Queue`.

    Uso típico:
        bus = EventBus(loop=bot.loop)      # el loop propietario de las colas
        queue = bus.subscribe()            # un suscriptor (p. ej. una conexión SSE)
        ...
        bus.publish({"type": "event_changed", "event_id": 123})
        change = await queue.get()         # el suscriptor recibe el cambio
        ...
        bus.unsubscribe(queue)
    """

    def __init__(self, loop=None):
        # Conjunto de colas de los suscriptores activos.
        self._subscribers = set()
        # Loop propietario de las colas. `publish` programa la entrega en él
        # de forma segura entre hilos.
        self._loop = loop

    def set_loop(self, loop):
        """Fija el loop propietario de las colas.

        Útil cuando el bus se crea antes de que el loop del bot exista (p. ej.
        en el arranque) y se quiere asociar más tarde.
        """
        self._loop = loop

    def subscribe(self):
        """Registra un nuevo suscriptor y devuelve su `asyncio.Queue`."""
        queue = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue):
        """Elimina un suscriptor. Es idempotente: no falla si ya no existe."""
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self):
        return len(self._subscribers)

    def publish(self, change):
        """Publica un cambio a todos los suscriptores. No bloqueante.

        Seguro entre hilos: si hay un loop configurado y en ejecución, la
        entrega se programa en ese loop con `call_soon_threadsafe`. Si no hay
        loop o no hay suscriptores, es un no-op seguro.
        """
        subscribers = list(self._subscribers)
        if not subscribers:
            return

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._deliver, subscribers, change)
        else:
            # Sin loop en ejecución: entrega directa, en el mismo hilo.
            self._deliver(subscribers, change)

    @staticmethod
    def _deliver(subscribers, change):
        """Encola el cambio en cada cola de suscriptor.

        Se ejecuta siempre en el loop propietario de las colas, por lo que
        `put_nowait` es seguro aquí. Los suscriptores lentos cuya cola esté
        llena simplemente pierden ese cambio (recuperan el estado vigente al
        reconsultar; ver Req 10.4); el resto no se ve afectado.
        """
        for queue in subscribers:
            try:
                queue.put_nowait(change)
            except asyncio.QueueFull:
                pass
