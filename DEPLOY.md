# Despliegue y configuración de mhub

mhub es un único proceso que sirve a la vez el **bot de Discord** y la **API web**
(FastAPI en un hilo). Se despliega en Fly.io con un volumen en `/data`. El
**frontend** es una app estática aparte (Vercel/Netlify/Cloudflare Pages) que
consume la API.

> Nota de seguridad: los endpoints `GET` de la API son **públicos por diseño**
> (no exponen `user_id`). Toda escritura exige sesión (login con Discord).

## Variables de entorno

| Variable | ¿Obligatoria? | Dónde / valor | Para qué |
|---|---|---|---|
| `DISCORD_TOKEN_HUB` | Sí | Secret | Token del bot de Discord |
| `DISCORD_CLIENT_ID` | Sí (web) | Secret | OAuth2: ID de la aplicación |
| `DISCORD_CLIENT_SECRET` | Sí (web) | Secret | OAuth2: secreto de la aplicación |
| `OAUTH_REDIRECT_URI` | Sí (web) | `https://<api>/auth/callback` | Callback OAuth2 |
| `SESSION_SECRET` | Sí (web) | Secret aleatorio fijo | Firma de la cookie de sesión |
| `FRONTEND_ORIGIN` | Sí (web) | `https://<tu-web>` | Origen permitido por CORS y destino tras login |
| `GUILD_ID` | No | ID de tu servidor | Sincroniza slash commands al instante (si no, global, hasta 1h) |
| `CHANNEL_ID` | No | ID de canal | Restringe los comandos a un canal |
| `RATE_LIMIT_PER_MINUTE` | No | entero | Límite por IP/identidad. Sin definir = sin límite. `0` = rechaza todo |
| `COOKIE_SECURE` | No | `1` (def.) / `0` | `0` solo para pruebas en local sin HTTPS |
| `DATA_DIR` | No | `/data` en Fly.io | Directorio de las SQLite |
| `PORT` | No | `8080` en Fly.io | Puerto de la API/health |

Las marcadas como **Secret** deben ir con `fly secrets set`, no en `fly.toml`.

## 1. Crear la aplicación de Discord

1. En https://discord.com/developers/applications → New Application.
2. **Bot** → crea el bot y copia su **token** → `DISCORD_TOKEN_HUB`.
3. **OAuth2** → copia **Client ID** y **Client Secret**.
4. **OAuth2 → Redirects**: añade `https://<tu-api>/auth/callback`
   (el mismo valor que pondrás en `OAUTH_REDIRECT_URI`).
5. Invita el bot con scopes **`bot`** y **`applications.commands`** (URL
   generada en OAuth2 → URL Generator).

## 2. Desplegar el bot + API en Fly.io

```bash
# desde la carpeta mhub/
fly launch --no-deploy        # si la app no existe aún (usa el fly.toml incluido)
fly volumes create app_data_hub --size 1   # si no existe el volumen

# Secretos (no se versionan)
fly secrets set DISCORD_TOKEN_HUB=...   DISCORD_CLIENT_ID=...   DISCORD_CLIENT_SECRET=...   SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"   OAUTH_REDIRECT_URI=https://<tu-api>.fly.dev/auth/callback   FRONTEND_ORIGIN=https://<tu-web>

fly deploy
```

`PORT=8080` y `DATA_DIR=/data` ya están en `fly.toml`. Opcionalmente añade
`GUILD_ID` como secret/env para sincronización instantánea de comandos.

## 3. Desplegar el frontend

1. Genera la web con el prompt de `docs/ai-studio-prompt.md`.
2. Despliega el estático (Vercel/Netlify/Pages) con la variable
   `VITE_API_BASE = https://<tu-api>.fly.dev`.
3. Toma la URL pública de la web y ponla en `FRONTEND_ORIGIN` del bot
   (`fly secrets set FRONTEND_ORIGIN=...` y `fly deploy`).

> Hay una dependencia cruzada: necesitas la URL de la API para `VITE_API_BASE` y
> `OAUTH_REDIRECT_URI`, y la URL de la web para `FRONTEND_ORIGIN`. Despliega
> primero la API, luego la web, y por último actualiza `FRONTEND_ORIGIN`.

## Ejecutar en local (sin Docker)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# crea un archivo .env con al menos:
#   DISCORD_TOKEN_HUB=...
#   GUILD_ID=...            (recomendado para ver los comandos al instante)
#   COOKIE_SECURE=0         (en local sin HTTPS)
#   FRONTEND_ORIGIN=http://localhost:5173
#   DISCORD_CLIENT_ID=... DISCORD_CLIENT_SECRET=... OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback
python main.py
```

La API quedará en `http://localhost:8000` (health en `/` y `/api/health`).

## Tests

```bash
python -m pytest -q
```

La capa de dominio y la API se testean sin necesidad de un token ni de red (el
acceso a Discord se mockea).
