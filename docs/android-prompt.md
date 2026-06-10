# Prompt para generar la app Android de mhub

> Pega el bloque de abajo en tu generador (AI Studio / Gemini / etc.). Describe la
> API real de mhub. La lectura es pública; la escritura usa la cookie de sesión de
> Discord, que en Android se obtiene vía WebView + CookieManager.

---

Crea una app **Android nativa en Kotlin con Jetpack Compose** (Material 3, tema
claro/oscuro) que sea el cliente de una comunidad de Discord, consumiendo una API
REST ya existente.

## Stack y arquitectura
- Kotlin + Jetpack Compose + Material 3.
- Arquitectura MVVM: `ViewModel` + `StateFlow`, repositorios, capa de red aislada.
- Red: **Retrofit + OkHttp**, con **kotlinx.serialization** (o Moshi) para JSON.
- Coroutines/Flow para asincronía.
- SSE (tiempo real): usa **okhttp-sse** (`EventSource`).
- Navegación con Navigation-Compose.
- La URL base de la API es configurable vía `BuildConfig.API_BASE`
  (p. ej. `https://mhub.fly.dev`). No la hardcodees en pantallas.

## Sesión y cookies (IMPORTANTE)
- La API autentica con una **cookie de sesión** (`mhub_session`, HttpOnly).
- Configura OkHttp con un **CookieJar persistente** (guarda cookies en
  `SharedPreferences`/DataStore) para que las llamadas autenticadas envíen la
  cookie.
- Las peticiones de **lectura no requieren sesión**.

## Autenticación con Discord (vía WebView)
1. Botón "Iniciar sesión con Discord" abre una pantalla con un `WebView` que carga
   `${API_BASE}/auth/login`. El WebView debe aceptar cookies de terceros
   (`CookieManager.getInstance().setAcceptThirdPartyCookies`).
2. La API hace el flujo OAuth2 y al terminar redirige (a su `FRONTEND_ORIGIN`). En
   `WebViewClient.onPageStarted`/`onPageFinished`, comprueba si ya existe la cookie
   de sesión: `CookieManager.getInstance().getCookie(API_BASE)` (devuelve también
   las HttpOnly). Cuando contenga `mhub_session=...`, extrae esa cookie, guárdala
   en el CookieJar de OkHttp y cierra el WebView (login completado).
3. Tras el login, llama a `GET ${API_BASE}/api/me` para obtener el usuario:
   `{ user_id, username, avatar }`. Si responde 401, sigue sin autenticar.
4. Cerrar sesión: `POST ${API_BASE}/auth/logout` (204) y limpia el CookieJar y el
   `CookieManager`.
5. Muestra las acciones de escritura solo si hay sesión.

## Modelos (JSON de la API)
```kotlin
data class Role(val role: String, val emoji: String?, val capacity: Int,
                val occupied: Int, val free: Int, val members: List<String>)
data class Event(val id: String, val title: String, val status: String,
                 val created_at: String?, val roles: List<Role>)
data class Entry(val category: String, val name: String, val link: String,
                 val providers: List<String>)
data class Suggestion(val provider: String, val category: String,
                      val name: String, val link: String)
data class RoleSuggestions(val role: String, val candidates: List<Suggestion>)
data class Me(val user_id: String, val username: String?, val avatar: String?)
```
La API NUNCA devuelve `user_id` de otros usuarios (no lo esperes en eventos ni
directorio).

## Endpoints de LECTURA (públicos)
- `GET /api/events` → `List<Event>` (eventos activos).
- `GET /api/events/archived` → `List<Event>` (historial).
- `GET /api/events/{id}` → `Event`.
- `GET /api/registry` → `List<Entry>`.
- `GET /api/registry/search?categoria=<cat>&q=<texto>` → `List<Entry>`.
- `GET /api/bridge/events/{id}/suggestions` → `List<RoleSuggestions>`.

## Endpoints de ESCRITURA (requieren sesión)
- `POST /api/events/{id}/join` body `{ "role": "<clave>" }` → `Event` actualizado.
  Si el rol está lleno → **409** (muestra "rol completo").
- `POST /api/events/{id}/leave` → `Event` actualizado.
- `POST /api/registry` body `{ provider, category, name, link }` → 201, devuelve
  `{ id, provider, category, name, link }`.
- `DELETE /api/registry/{entry_id}` → 204. **403** si no es tuya, **404** si no
  existe.

## Tiempo real (SSE)
- En la lista/detalle de eventos, abre un `EventSource` (okhttp-sse) sobre
  `GET ${API_BASE}/api/stream`.
- Procesa los eventos con nombre `event_changed`; su `data` es un `Event` (JSON).
  Actualiza ese evento en el estado de la UI sin recargar.
- Gestiona reconexión: al reconectar, recarga `GET /api/events`.

## Pantallas
1. **Eventos** (inicio): lista de tarjetas de eventos activos. Cada tarjeta: título,
   estado y, por rol, emoji + `occupied/capacity` + miembros. Botones por rol para
   **Apuntarse** y botón **Salir** (solo con sesión). Actualización en vivo por SSE.
2. **Detalle de evento**: roles ampliados y, con sesión, panel de **Sugerencias**
   (`/api/bridge/events/{id}/suggestions`) con candidatos por rol.
3. **Historial**: eventos archivados (`/api/events/archived`).
4. **Directorio**: lista con buscador (categoría + texto). Con sesión: formulario
   para **añadir** entrada y borrar las propias.
5. **Perfil/sesión**: estado de login (avatar/username o botón de login/logout).

## UX y robustez
- Estados de carga, vacío y error por pantalla; pull-to-refresh.
- Ante 401 en escritura, invita a iniciar sesión.
- Ante 409 al apuntarse, muestra "ese rol ya está completo".
- Permite cambiar la URL de la API en ajustes (para pruebas) además de
  `BuildConfig.API_BASE`.
- minSdk 26+; usa solo dependencias estables y ampliamente usadas.
```
