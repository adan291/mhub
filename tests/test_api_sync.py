"""Tests de la sincronización web → Discord (módulo `modules/api/sync.py`, task 10).

`sync.py` se carga por ruta de archivo con importlib para evitar el
`__init__.py` del paquete `modules` (que importa `discord`). El paquete
`discord` NO está instalado en el entorno de test: por eso los renderizadores
(`build_embed`/`EventView`) se inyectan vía `sync._get_renderers`, evitando el
import real de Discord, y el bot/canal/mensaje se sustituyen por dobles.

Cubre (Req 9.1, 9.2, 9.3):
  - `_resync` reconstruye el embed/vista y reedita el mensaje reflejando la
    ocupación vigente.
  - Caso de canal no localizado (`get_channel` → None): no lanza y conserva el
    cambio (no se intenta editar).
  - Caso de mensaje no encontrado (`fetch_message` lanza, simulando NotFound):
    no lanza y conserva el cambio.
  - `resync_event` programa la corrutina en el loop del bot vía
    `run_coroutine_threadsafe` y la edición se materializa.
  - `resync_event` es no-op seguro cuando el bot no tiene un loop de asyncio.
"""
import asyncio
import importlib.util
import os
import threading

import pytest


def _load_sync_module():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "modules", "api", "sync.py",
    )
    spec = importlib.util.spec_from_file_location("api_sync_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sync = _load_sync_module()


# --------------------------------------------------------------------------- #
# Dobles (sin Discord)
# --------------------------------------------------------------------------- #
class FakeEmbed:
    def __init__(self, event, template, complete):
        self.event = event
        self.template = template
        self.complete = complete


def _fake_build_embed(event, template, complete=False):
    return FakeEmbed(event, template, complete)


class FakeButton:
    def __init__(self):
        self.disabled = False


class FakeEventView:
    """Doble de `EventView` que registra cómo se construyó."""

    instances = []

    def __init__(self, manager, template_key, template=None):
        self.manager = manager
        self.template_key = template_key
        self.template = template
        self.children = [FakeButton(), FakeButton()]
        FakeEventView.instances.append(self)


class FakeMessage:
    def __init__(self):
        self.edited_embed = None
        self.edited_view = None
        self.edit_calls = 0

    async def edit(self, embed=None, view=None):
        self.edit_calls += 1
        self.edited_embed = embed
        self.edited_view = view


class FakeNotFound(Exception):
    """Simula `discord.NotFound` al obtener un mensaje borrado."""


class FakeChannel:
    def __init__(self, message=None, raise_on_fetch=None):
        self._message = message
        self._raise = raise_on_fetch
        self.fetched_ids = []

    async def fetch_message(self, message_id):
        self.fetched_ids.append(message_id)
        if self._raise is not None:
            raise self._raise
        return self._message


class FakeManager:
    def __init__(self, event, complete=False):
        self._event = event
        self._complete = complete

    def get(self, event_id):
        if self._event is None:
            return None
        if event_id == self._event.get("message_id"):
            return self._event
        return None

    def is_complete(self, event):
        return self._complete


class FakeBot:
    def __init__(self, manager, channel, loop=None):
        self.signup_manager = manager
        self._channel = channel
        self.loop = loop
        self.requested_channel_ids = []

    def get_channel(self, channel_id):
        self.requested_channel_ids.append(channel_id)
        return self._channel


def _make_event():
    return {
        "message_id": 2001,
        "channel_id": 5001,
        "guild_id": 7001,
        "title": "Equipo Web",
        "template_key": None,
        "status": "activo",
        "composition": {
            "backend": {"emoji": None, "plazas": 2},
            "lead": {"emoji": None, "plazas": 1},
        },
        "state": {
            "backend": [{"id": 42, "name": "Alicia"}],
            "lead": [],
        },
    }


@pytest.fixture(autouse=True)
def _inject_renderers(monkeypatch):
    """Inyecta dobles de los renderizadores para no importar `discord`."""
    FakeEventView.instances = []
    monkeypatch.setattr(
        sync, "_get_renderers", lambda: (_fake_build_embed, FakeEventView))


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# _resync: camino feliz (Req 9.2)
# --------------------------------------------------------------------------- #
def test_resync_edits_message_reflecting_occupancy():
    event = _make_event()
    message = FakeMessage()
    channel = FakeChannel(message=message)
    bot = FakeBot(FakeManager(event, complete=False), channel)

    _run(sync._resync(bot, 2001))

    assert message.edit_calls == 1
    # El embed se reconstruye desde la Composicion del propio evento.
    assert isinstance(message.edited_embed, FakeEmbed)
    assert message.edited_embed.event is event
    assert message.edited_embed.template == {"roles": event["composition"]}
    assert message.edited_embed.complete is False
    # Se localizó el canal y el mensaje correctos.
    assert bot.requested_channel_ids == [5001]
    assert channel.fetched_ids == [2001]
    # La vista se reconstruye desde la composición.
    assert isinstance(message.edited_view, FakeEventView)


def test_resync_disables_buttons_when_complete():
    event = _make_event()
    message = FakeMessage()
    bot = FakeBot(FakeManager(event, complete=True), FakeChannel(message=message))

    _run(sync._resync(bot, 2001))

    assert message.edit_calls == 1
    assert message.edited_embed.complete is True
    assert all(child.disabled for child in message.edited_view.children)


# --------------------------------------------------------------------------- #
# _resync: fallos de localización → se conserva el cambio (Req 9.3)
# --------------------------------------------------------------------------- #
def test_resync_channel_not_found_is_graceful():
    event = _make_event()
    bot = FakeBot(FakeManager(event), channel=None)  # get_channel → None

    # No debe lanzar y no debe intentar editar nada.
    _run(sync._resync(bot, 2001))

    assert bot.requested_channel_ids == [5001]


def test_resync_message_not_found_is_graceful():
    event = _make_event()
    channel = FakeChannel(raise_on_fetch=FakeNotFound("404"))
    bot = FakeBot(FakeManager(event), channel)

    # fetch_message lanza (como discord.NotFound): no debe propagar.
    _run(sync._resync(bot, 2001))

    assert channel.fetched_ids == [2001]


def test_resync_unknown_event_is_graceful():
    bot = FakeBot(FakeManager(None), FakeChannel(message=FakeMessage()))
    # El manager no encuentra el evento: no lanza y no intenta nada en Discord.
    _run(sync._resync(bot, 999999))
    assert bot.requested_channel_ids == []


def test_resync_edit_failure_is_swallowed():
    event = _make_event()

    class ExplodingMessage(FakeMessage):
        async def edit(self, embed=None, view=None):
            raise RuntimeError("Discord caído")

    bot = FakeBot(FakeManager(event), FakeChannel(message=ExplodingMessage()))
    # Un fallo en message.edit no debe propagar (Req 9.3).
    _run(sync._resync(bot, 2001))


# --------------------------------------------------------------------------- #
# resync_event: programación en el loop del bot (Req 9.1)
# --------------------------------------------------------------------------- #
def test_resync_event_schedules_on_bot_loop():
    event = _make_event()
    message = FakeMessage()
    bot = FakeBot(FakeManager(event), FakeChannel(message=message))

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    bot.loop = loop
    try:
        fut = sync.resync_event(bot, 2001)
        assert fut is not None
        # No bloqueamos indefinidamente: esperamos a que la edición se complete.
        fut.result(timeout=2)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        loop.close()

    assert message.edit_calls == 1
    assert isinstance(message.edited_view, FakeEventView)


def test_resync_event_noop_without_real_loop():
    """Con un `loop` que no es de asyncio, `resync_event` es no-op seguro."""
    event = _make_event()
    bot = FakeBot(FakeManager(event), FakeChannel(message=FakeMessage()),
                  loop=object())
    assert sync.resync_event(bot, 2001) is None


def test_resync_event_noop_without_bot_or_loop():
    assert sync.resync_event(None, 2001) is None
    bot = FakeBot(FakeManager(_make_event()), FakeChannel(), loop=None)
    assert sync.resync_event(bot, 2001) is None
