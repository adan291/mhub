"""Tests del EventBus interno (pub/sub en memoria) y de su integración con
EventManager.

Cubren:
  - Entrega de un cambio a múltiples suscriptores (cada cola recibe el cambio).
  - Suscripción/cancelación: tras `unsubscribe` una cola deja de recibir.
  - Publicación segura desde OTRO hilo: el cambio llega a una cola cuyo loop
    propietario corre en el hilo principal (vía `loop.call_soon_threadsafe`).
  - No-op seguro: `publish` sin suscriptores o sin loop no falla.
  - EventManager publica un cambio tras cada mutación de estado (create,
    toggle join/leave, archive) y no publica cuando no hay mutación (FULL).

Requisitos: 10.1, 10.2
"""
import asyncio
import threading

import pytest

from tests.domain_loader import EventBus, EventManager, EventStore


# --------------------------------------------------------------------------- #
# EventBus puro
# --------------------------------------------------------------------------- #
class TestEventBusDelivery:
    def test_delivery_to_multiple_subscribers(self):
        """Un cambio publicado llega a TODOS los suscriptores."""
        async def scenario():
            bus = EventBus(loop=asyncio.get_running_loop())
            q1 = bus.subscribe()
            q2 = bus.subscribe()
            q3 = bus.subscribe()

            change = {"type": "event_changed", "event_id": 42}
            bus.publish(change)

            # `call_soon_threadsafe` programa la entrega; basta ceder el loop.
            got1 = await asyncio.wait_for(q1.get(), timeout=1)
            got2 = await asyncio.wait_for(q2.get(), timeout=1)
            got3 = await asyncio.wait_for(q3.get(), timeout=1)
            return got1, got2, got3

        got1, got2, got3 = asyncio.run(scenario())
        expected = {"type": "event_changed", "event_id": 42}
        assert got1 == expected
        assert got2 == expected
        assert got3 == expected

    def test_unsubscribe_stops_delivery(self):
        async def scenario():
            bus = EventBus(loop=asyncio.get_running_loop())
            q1 = bus.subscribe()
            q2 = bus.subscribe()
            bus.unsubscribe(q1)

            bus.publish({"type": "event_changed", "event_id": 1})
            got2 = await asyncio.wait_for(q2.get(), timeout=1)
            # q1 ya no debe recibir nada.
            assert q1.empty()
            return got2

        got2 = asyncio.run(scenario())
        assert got2 == {"type": "event_changed", "event_id": 1}

    def test_unsubscribe_is_idempotent(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        # Cancelar dos veces no debe fallar.
        bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    def test_publish_without_subscribers_is_noop(self):
        # Sin suscriptores ni loop: no debe lanzar.
        bus = EventBus()
        bus.publish({"type": "event_changed", "event_id": 1})

    def test_publish_without_loop_delivers_directly(self):
        """Sin loop configurado, la entrega es directa (mismo hilo)."""
        async def scenario():
            bus = EventBus()  # sin loop
            q = bus.subscribe()
            bus.publish({"type": "event_created", "event_id": 7})
            return await asyncio.wait_for(q.get(), timeout=1)

        got = asyncio.run(scenario())
        assert got == {"type": "event_created", "event_id": 7}

    def test_publish_from_another_thread(self):
        """`publish` invocado desde OTRO hilo entrega el cambio a una cola cuyo
        loop propietario corre en el hilo principal (Req 10.2)."""
        async def scenario():
            loop = asyncio.get_running_loop()
            bus = EventBus(loop=loop)
            q = bus.subscribe()

            def worker():
                # Se ejecuta en un hilo distinto al del loop.
                bus.publish({"type": "event_changed", "event_id": 99})

            t = threading.Thread(target=worker)
            t.start()
            t.join()

            # La entrega se programó en este loop; debe llegar a la cola.
            return await asyncio.wait_for(q.get(), timeout=2)

        got = asyncio.run(scenario())
        assert got == {"type": "event_changed", "event_id": 99}

    def test_publish_from_another_thread_multiple_subscribers(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            bus = EventBus(loop=loop)
            queues = [bus.subscribe() for _ in range(4)]

            def worker():
                bus.publish({"type": "event_archived", "event_id": 5})

            t = threading.Thread(target=worker)
            t.start()
            t.join()

            return [await asyncio.wait_for(q.get(), timeout=2) for q in queues]

        results = asyncio.run(scenario())
        assert all(r == {"type": "event_archived", "event_id": 5} for r in results)


# --------------------------------------------------------------------------- #
# Integración EventManager → EventBus
# --------------------------------------------------------------------------- #
class _RecordingBus:
    """Bus de prueba que registra publicaciones de forma síncrona, para
    verificar QUÉ publica EventManager sin depender de un loop."""

    def __init__(self):
        self.published = []

    def publish(self, change):
        self.published.append(change)


@pytest.fixture
def bus():
    return _RecordingBus()


@pytest.fixture
def manager(tmp_path, bus):
    store = EventStore(str(tmp_path / "events.sqlite"), templates={})
    return EventManager(store, {}, bus=bus)


def _adhoc():
    return {
        "backend": {"emoji": None, "plazas": 2},
        "frontend": {"emoji": None, "plazas": 1},
    }


class TestEventManagerPublishes:
    def test_create_publishes(self, manager, bus):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        assert bus.published == [
            {"type": "event_created", "event_id": 1, "status": EventManager.ACTIVE}
        ]

    def test_toggle_join_publishes_event_changed(self, manager, bus):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        bus.published.clear()
        manager.toggle(1, "backend", 7, "Ana")
        assert bus.published == [
            {"type": "event_changed", "event_id": 1, "status": EventManager.ACTIVE}
        ]

    def test_toggle_leave_publishes(self, manager, bus):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        manager.toggle(1, "backend", 7, "Ana")
        bus.published.clear()
        manager.toggle(1, "backend", 7, "Ana")  # leave
        assert bus.published == [
            {"type": "event_changed", "event_id": 1, "status": EventManager.ACTIVE}
        ]

    def test_toggle_completing_publishes_completed_status(self, manager, bus):
        comp = {"solo": {"emoji": None, "plazas": 1}}
        manager.create(1, 10, 100, "Uno", comp)
        bus.published.clear()
        manager.toggle(1, "solo", 7, "Ana")  # llena el evento → completado
        assert bus.published == [
            {"type": "event_changed", "event_id": 1,
             "status": EventManager.COMPLETED}
        ]

    def test_full_role_does_not_publish(self, manager, bus):
        manager.create(1, 10, 100, "Sprint", _adhoc())  # frontend: 1 plaza
        manager.toggle(1, "frontend", 7, "Ana")
        bus.published.clear()
        manager.toggle(1, "frontend", 8, "Beto")  # FULL: sin mutación
        assert bus.published == []

    def test_unknown_event_does_not_publish(self, manager, bus):
        manager.toggle(999, "backend", 7, "Ana")
        assert bus.published == []

    def test_archive_publishes(self, manager, bus):
        manager.create(1, 10, 100, "Sprint", _adhoc())
        bus.published.clear()
        manager.archive(1)
        assert bus.published == [
            {"type": "event_archived", "event_id": 1,
             "status": EventManager.ARCHIVED}
        ]

    def test_manager_without_bus_is_noop(self, tmp_path):
        # EventManager sin bus no debe fallar al mutar (compatibilidad dominio).
        store = EventStore(str(tmp_path / "e.sqlite"), templates={})
        m = EventManager(store, {})  # sin bus
        m.create(1, 10, 100, "Sprint", _adhoc())
        result, _ = m.toggle(1, "backend", 7, "Ana")
        assert result == EventManager.JOINED
