# Prompt para Google AI Studio — Frontend de mhub

> Copia el bloque de abajo en Google AI Studio (modo Build). Describe la API real
> ya implementada en mhub, para que la web encaje a la primera.

---

Crea una aplicación web (SPA) en **React + TypeScript + Vite + Tailwind CSS** que
sea el panel de una comunidad de Discord. Consume una API REST ya existente. Debe
ser limpia, responsive y con tema claro/oscuro.

## Configuración de la API
- La URL base de la API se lee de la variable de entorno `VITE_API_BASE`
  (ej.: `https://mhub.fly.dev`). No la hardcodees.
- **TODAS** las peticiones `fetch` deben incluir `credentials: 'include'` (la
  sesión es una cookie HttpOnly; sin esto, la autenticación no funciona).

## Autenticación (Discord OAuth2)
- Botón "Iniciar sesión con Discord": redirige el navegador (no fetch) a
  `${VITE_API_BASE}/auth/login`. La API gestiona el OAuth y vuelve a la web.
- Al cargar la app, llama a `GET ${VITE_API_BASE}/api/me`:
  - Si responde 200 → usuario autenticado. Forma: `{ user_id, username, avatar }`.
  - Si responde 401 → no autenticado (muestra el botón de login).
- Cerrar sesión: `POST ${VITE_API_BASE}/auth/logout` (responde 204).
- Las acciones de escritura solo se muestran si el usuario está autenticado.

## Endpoints de LECTURA (públicos, no requieren login)
- `GET /api/events` → lista de eventos activos. Cada evento:
  ```ts
  type Role = { role: string; emoji: string | null; capacity: number;
                occupied: number; free: number; members: string[] };
  type Event = { id: string; title: string; status: string;
                 created_at: string | null; roles: Role[] };
  ```
- `GET /api/events/archived` → mismos `Event[]`, pero el historial archivado.
- `GET /api/events/{id}` → un `Event`.
- `GET /api/registry` → directorio:
  ```ts
  type Entry = { category: string; name: string; link: string; providers: string[] };
  ```
- `GET /api/registry/search?categoria=<cat>&q=<texto>` → `Entry[]`.
- `GET /api/bridge/events/{id}/suggestions` → sugerencias de personas por rol:
  ```ts
  type Suggestion = { provider: string; category: string; name: string; link: string };
  type RoleSuggestions = { role: string; candidates: Suggestion[] };
  ```
  (devuelve `RoleSuggestions[]`).

## Endpoints de ESCRITURA (requieren sesión)
- `POST /api/events/{id}/join` con cuerpo `{ "role": "<clave_del_rol>" }`
  → devuelve el `Event` actualizado. Si el rol está lleno responde **409**
  (muestra "rol completo").
- `POST /api/events/{id}/leave` → devuelve el `Event` actualizado (salir del evento).
- `POST /api/registry` con cuerpo
  `{ "provider": "...", "category": "...", "name": "...", "link": "..." }`
  → crea una entrada (201). Devuelve `{ id, provider, category, name, link }`.
- `DELETE /api/registry/{entry_id}` → borra una entrada propia (204).
  Responde **403** si no es tuya y **404** si no existe.

## Tiempo real (SSE)
- Suscríbete a `GET ${VITE_API_BASE}/api/stream` con `EventSource`.
- Emite eventos con nombre `event_changed` cuyo `data` es un `Event` (JSON).
- Al recibirlo, actualiza ese evento en la UI sin recargar la página.
- Si la conexión se cae, reconéctate y vuelve a pedir `GET /api/events`.

## Pantallas / funcionalidad
1. **Eventos (home)**: tarjetas de eventos activos. Cada tarjeta muestra el
   título, el estado y, por cada rol, su emoji, ocupación `occupied/capacity` y
   la lista de miembros (nombres). Botones por rol para **Apuntarse** y un botón
   **Salir** (solo si hay sesión). Refresco en vivo vía SSE.
2. **Detalle de evento**: vista ampliada con los roles y, si hay sesión, un panel
   de **Sugerencias** (llamando a `/api/bridge/events/{id}/suggestions`) que
   muestra, por rol, las personas candidatas del directorio.
3. **Historial**: lista de eventos archivados (`/api/events/archived`).
4. **Directorio**: tabla/listado de entradas con buscador (categoría + texto vía
   `/api/registry/search`). Si hay sesión: formulario para **añadir** entrada y
   botón para **borrar** las propias.
5. **Barra superior**: estado de sesión (avatar/username o botón de login) y
   navegación entre Eventos, Historial y Directorio.

## Detalles de UX
- Maneja estados de carga y error de cada petición.
- Ante 401 en una acción de escritura, invita a iniciar sesión.
- Ante 409 al apuntarse, muestra "ese rol ya está completo".
- No muestres ni esperes ningún `user_id` en las respuestas (la API no lo expone).
- Código organizado: un cliente de API tipado en `src/api.ts`, tipos en
  `src/types.ts`, y componentes por pantalla.
