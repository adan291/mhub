# mhub — Plataforma de Discord + Web

mhub es el **único bot activo** de la comunidad. Consolida en un solo proceso
las funciones de dos bots ahora **retirados**, reimplementados como módulos
sobre un núcleo común:

- **Mplux → módulo `signups`**: inscripción a eventos con roles y cupos
  (slash commands + botones persistentes).
- **mprof → módulo `registry`**: directorio genérico por categorías.

> Mplux y mprof quedan **retirados**: no deben ejecutarse en paralelo. Toda su
> funcionalidad vive ahora como módulos de mhub, sobre una única fuente de
> verdad, para evitar comandos duplicados.

Además de Discord, mhub expone una **cara Web** (API HTTP) servida en el mismo
proceso que el bot, de modo que ambas caras comparten exactamente los mismos
managers y stores.

## Arquitectura

```
mhub/
  main.py                  # punto de entrada: arranca HubBot (bot + API en un proceso)
  core/
    config.py              # token, intents, canal, guild, rutas de datos
    bot.py                 # HubBot: carga módulos, restaura estado, sincroniza comandos
    event_bus.py           # EventBus interno (pub/sub) para el canal de tiempo real
    guild_config_store.py  # GuildConfigStore: configuración por servidor (multi-comunidad)
  modules/
    __init__.py            # ENABLED_MODULES = [signups, registry, api, bridge]
    signups/               # eventos con roles y cupos (ex-Mplux)
    registry/              # directorio por categorías (ex-mprof)
    bridge/                # Módulo_Puente: relaciona roles de eventos con skills del directorio
    api/                   # API_Web (FastAPI): lectura pública, OAuth2, escritura, SSE, seguridad
  config/
    templates.json         # plantillas de evento por defecto (módulo signups)
    registry.json          # categorías por defecto (módulo registry)
```

### Núcleo compartido

- **HubBot** (`core/bot.py`) carga cada módulo de `ENABLED_MODULES` llamando a
  su `setup(bot)`, ejecuta los `restore(bot)` (vistas persistentes) y sincroniza
  los slash commands. Si la sincronización falla, el bot sigue operando con los
  módulos cargados.
- **EventBus** (`core/event_bus.py`): bus pub/sub en memoria. Una única
  instancia compartida por la que `EventManager` (Discord) y la escritura web
  publican los cambios de evento, y de la que el canal de tiempo real (SSE) de
  la API se nutre. Entrega segura entre hilos (`call_soon_threadsafe`).
- **GuildConfigStore** (`core/guild_config_store.py`): configuración por
  servidor (plantillas y categorías), persistida en SQLite, con *fallback* a la
  configuración por defecto de `config/templates.json` y `config/registry.json`
  cuando un servidor no tiene configuración propia.

Cada módulo persiste su estado en su propia base de datos SQLite dentro de
`DATA_DIR` (`events.sqlite`, `registry.sqlite`, configuración por servidor), de
modo que son independientes entre sí.

### Un solo proceso (bot + API)

`main.py` arranca **HubBot y la API_Web en el mismo proceso** sobre el mismo
volumen. El módulo `api` arranca Uvicorn en un hilo *daemon* durante su
`setup(bot)`, escuchando en `0.0.0.0:$PORT` y conservando la ruta de salud `/`
que Fly.io espera. Por eso ya **no** se levanta el antiguo servidor Flask de
`core/webserver.py`. La API corre en su propio *event loop* (hilo de Uvicorn),
separado del loop de `discord.py`.

## Módulos

### `signups` (ex-Mplux)

Inscripción a eventos con roles y cupos. Las vistas de botones son persistentes
y se restauran tras un reinicio mediante `restore(bot)` (reconstruidas desde la
composición guardada del evento, así que funcionan también con plantillas
propias de un servidor que no estén en `templates.json`).

### `registry` (ex-mprof)

Directorio por categorías: alta, búsqueda, listado y borrado de entradas, más
backup para administradores. Cada entrada queda asociada al `user_id` de quien
la registra.

### `bridge` (Módulo_Puente)

`BridgeService` relaciona los roles de un Evento con las Personas del directorio
cuya skill (categoría/nombre normalizado) encaja con cada rol, excluyendo a
quienes ya están inscritos. No reimplementa lógica de negocio: deriva todo de
`RegistryStore` y `EventStore`. Debe registrarse después de `signups` y
`registry`.

### `api` (API_Web)

API HTTP con FastAPI servida por Uvicorn en el proceso del bot. Construida por
`build_app(bot)`, que inyecta en `app.state` las referencias compartidas con el
bot (`signup_manager`, `registry_store`, `guild_config_store`, `bridge_service`,
`event_bus`, `loop`). Es deliberadamente independiente de `discord` para poder
testearse de forma aislada.

## API Web — Endpoints

### Salud

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Salud para el health-check de Fly.io. |
| `GET` | `/api/health` | Salud de la API_Web. |

### Lectura pública (sin sesión)

No requieren autenticación y nunca exponen `user_id`.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/events` | Eventos activos con composición y ocupación/libres por rol. |
| `GET` | `/api/events/archived` | Historial de eventos archivados. |
| `GET` | `/api/events/{event_id}` | Detalle de un evento (por `message_id`). |
| `GET` | `/api/registry` | Directorio completo. |
| `GET` | `/api/registry/search?categoria=&q=` | Búsqueda por categoría y nombre. |
| `GET` | `/api/bridge/events/{event_id}/suggestions` | Sugerencias de personas por rol. |

### Autenticación (Discord OAuth2)

Flujo *Authorization Code* con `scope=identify` y sesión por cookie firmada
(`itsdangerous`). La identidad de toda escritura procede **siempre** de la
sesión, nunca del cuerpo de la petición.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/auth/login` | Redirige a Discord para iniciar OAuth2 (cookie `state` anti-CSRF). |
| `GET` | `/auth/callback?code=&state=` | Valida `state`, intercambia `code`, crea la sesión y redirige a `FRONTEND_ORIGIN`. |
| `POST` | `/auth/logout` | Cierra la sesión (borra la cookie). |
| `GET` | `/api/me` | Identidad de la sesión actual (401 si no hay sesión). |

### Escritura autenticada (requiere sesión)

Reutilizan los mismos managers/stores que Discord. Devuelven 401 sin sesión.

| Método | Ruta | Cuerpo | Descripción |
|---|---|---|---|
| `POST` | `/api/events/{event_id}/join` | `{ "role": "..." }` | Apunta/mueve al usuario al rol (409 si está completo). |
| `POST` | `/api/events/{event_id}/leave` | — | Retira al usuario del evento (idempotente). |
| `POST` | `/api/registry` | `{ "provider", "category", "name", "link" }` | Crea una entrada asociada al `user_id` de la sesión. |
| `DELETE` | `/api/registry/{entry_id}` | — | Borra una entrada propia (403 si es ajena, 404 si no existe). |

### Tiempo real (SSE)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/stream` | Flujo `text/event-stream`: emite `event_changed` ante cada cambio de evento (de Discord o de la web). Solo proyecta el DTO público (sin `user_id`). Al reconectar, el cliente recupera el estado con `GET /api/events`. |

### Seguridad transversal

- **Rate limiting** en memoria por IP/identidad mediante ventana deslizante
  (`RATE_LIMIT_PER_MINUTE`). Sin la variable → sin límite; `0` → rechaza todo
  con `429`; `N>0` → hasta `N` peticiones por minuto.
- **CORS** restringido a `FRONTEND_ORIGIN`, con credenciales (cookies). Sin
  origen configurado no se permite ningún origen cross-site.

## Variables de entorno

| Variable | Obligatoria | Por defecto | Descripción |
|---|---|---|---|
| `DISCORD_TOKEN_HUB` | Sí | — | Token del bot de Discord. |
| `GUILD_ID` | No | — | Servidor para sincronizar slash commands al instante (desarrollo). Sin él, sincronización global. |
| `CHANNEL_ID` | No | — | Restringe los comandos a un canal. |
| `DATA_DIR` | No | `./data` | Directorio de datos persistente. En Fly.io es `/data` (volumen). |
| `HOST` | No | `0.0.0.0` | Host de escucha de la API_Web. |
| `PORT` | No | `8000` | Puerto de la API_Web (en Fly.io `8080`). |
| `DISCORD_CLIENT_ID` | Sí (web) | — | Client ID de la aplicación OAuth2 de Discord. |
| `DISCORD_CLIENT_SECRET` | Sí (web) | — | Client Secret de la aplicación OAuth2 de Discord. |
| `OAUTH_REDIRECT_URI` | Sí (web) | — | URI de callback registrada (p. ej. `https://<app>.fly.dev/auth/callback`). |
| `SESSION_SECRET` | Recomendada | efímero | Secreto para firmar la cookie de sesión. Sin él se genera uno efímero (las sesiones no sobreviven a reinicios). |
| `FRONTEND_ORIGIN` | Sí (web) | `""` | Origen del frontend para CORS y redirección tras el login. |
| `COOKIE_SECURE` | No | `1` | Marca la cookie de sesión como `Secure` (HTTPS). `0`/`false` la desactiva (desarrollo local). |
| `RATE_LIMIT_PER_MINUTE` | No | — | Límite de peticiones por minuto e IP/identidad. Sin definir = sin límite; `0` = rechaza todo. |

> Notas: las variables marcadas "(web)" solo son necesarias para que funcione la
> cara web (OAuth2 y escritura autenticada). El bot de Discord arranca sin ellas;
> la lectura pública también, pero la autenticación/escritura no funcionará.

## Comandos de Discord

Los slash commands y las vistas persistentes de botones **existentes siguen
intactos** tras la introducción de la plataforma web: se aplican a través de los
mismos managers y stores que usa la API_Web, y los eventos creados con
plantillas anteriores se siguen gestionando sin migración manual.

Módulo `signups`:

- `/evento plantilla:<...> [titulo:<...>]` — crea un evento desde una plantilla
  (con botones por rol).
- `/evento_custom roles:<rol:plazas, ...> titulo:<...>` — crea un evento con
  composición numérica **ad-hoc**, sin editar archivos.
- `/evento_archivar evento:<message_id>` — archiva un evento (requiere permiso
  para gestionar mensajes o ser administrador).

Módulo `registry`:

- `/registro registrar | buscar | listar | borrar | backup` — gestiona el
  directorio por categorías.

Módulo `bridge`:

- `/equipo_sugerencias evento:<message_id>` — sugiere personas del directorio
  para cada rol de un evento (los roles sin candidatos se muestran
  explícitamente).

## Despliegue (Fly.io)

Un **único proceso** (`python main.py`) sirve el bot y la API_Web sobre el mismo
volumen (`/data`), donde viven todas las bases SQLite. El módulo `api` arranca
Uvicorn en el `PORT` configurado y conserva la ruta de salud `/`.

Variables no sensibles en `fly.toml` (`[env]`): `PORT`, `DATA_DIR`,
`FRONTEND_ORIGIN`, `COOKIE_SECURE`, `RATE_LIMIT_PER_MINUTE`.

Secretos (no en claro), vía `fly secrets set`:

```
fly secrets set DISCORD_TOKEN_HUB=...
fly secrets set DISCORD_CLIENT_ID=...
fly secrets set DISCORD_CLIENT_SECRET=...
fly secrets set OAUTH_REDIRECT_URI=https://<app>.fly.dev/auth/callback
fly secrets set SESSION_SECRET=...
```

## Pruebas

```
pytest
```

La suite cubre el dominio de signups, el parser de composición, el EventBus, el
GuildConfigStore, el servicio del puente y toda la API_Web (app, auth, lectura,
escritura, seguridad, SSE y sincronización), incluyendo pruebas basadas en
propiedades (Hypothesis).

## Cómo añadir un módulo nuevo

1. Crea un paquete en `modules/mi_modulo/` con una función:

   ```python
   async def setup(bot):
       await bot.add_cog(MiCog(bot))
   ```

2. (Opcional) añade `async def restore(bot)` si necesitas restaurar estado
   persistente al arrancar (p. ej. vistas con botones).

3. Regístralo en `modules/__init__.py`:

   ```python
   from . import signups, registry, api, bridge, mi_modulo
   ENABLED_MODULES = [signups, registry, api, bridge, mi_modulo]
   ```

No hay que tocar `core/`. El bot carga, restaura y sincroniza todos los módulos
de la lista automáticamente.
