# Mplux — Revisión, mejoras y propuesta de abstracción

> Bot de Discord para buscar grupo de míticas (M+) en World of Warcraft.

---

## 1. Qué hace hoy

Con el comando `!mplux <mazmorra+nivel> <rol>` el bot crea un *embed* con la
composición fija de una mítica de WoW (1 tank, 3 dps, 1 heal). Los usuarios se
apuntan reaccionando con emojis (🛡️ tank, ⚔️ dps, 💖 heal), y el bot va
rellenando los huecos y editando el mensaje. También borra mensajes antiguos del
canal de forma periódica.

### Flujo actual

1. `main.py` carga token e intents y arranca `MyBot` con un `channel_id` fijo.
2. `bot.py` registra el cog `MplusCommand` y escucha reacciones.
3. `team_manager.py` guarda los equipos en un diccionario en memoria.
4. `embed_manager.py` construye y actualiza el embed.
5. `message_manager.py` borra mensajes tras un retraso y limpia el canal cada 5 min.

### Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Arranque, carga de entorno, webserver de salud |
| `bot.py` | Cliente de Discord, eventos, registro de cogs |
| `comands/mplux_command.py` | Comando `!mplux` y lógica de reacciones |
| `team_manager.py` | Estado de los equipos (en memoria) |
| `embed_manager.py` | Construcción y actualización de embeds |
| `message_manager.py` | Borrado y limpieza de mensajes |
| `localization.py` | Textos en ES/EN (**no se usa**) |
| `config.py` | Token e intents |
| `webserver.py` | Health check para Fly.io |

---

## 2. Problemas detectados

### 🔴 Críticos

- **`on_reaction_add` duplicado.** Está definido en `bot.py` **y** en el cog
  `mplux_command.py`. Ambos se ejecutan en cada reacción, con lógicas distintas
  (uno comprueba si el rol está lleno *antes* de añadir, el otro *después*).
  Resultado: doble conteo y comportamiento inconsistente. **Es un bug real.**
- **Estado solo en memoria.** Los equipos viven en `TeamManager.teams` (un
  `dict`). Si el bot se reinicia —y en Fly.io se reinicia en cada deploy— se
  pierden todos los grupos activos.

### 🟠 Importantes

- **Composición y canal hardcodeados.** `{tank:1, dps:3, heal:1}` y el
  `channel_id` (`1279108276016906292`) están escritos en el código.
- **Reacciones en vez de botones.** Las reacciones de emoji son *legacy*,
  dependen de permisos para quitarlas y son frágiles. Los botones (Views) son el
  estándar actual.
- **`localization.py` sin usar.** Hay infraestructura de idiomas que nunca se
  invoca; los textos están en español dentro del código.

### 🟡 Menores

- `create_initial_embed` recibe `roles_necesarios` ya decrementado, lo que hace
  la lógica de los campos confusa y frágil.
- La limpieza periódica de mensajes es agresiva (borra todo lo viejo del canal).
- Comandos por prefijo (`!mplux`) en lugar de slash commands (`/`).

---

## 3. Mejoras recomendadas (sin reescribir)

Cambios de bajo riesgo que se pueden aplicar ya:

1. **Eliminar el `on_reaction_add` de `bot.py`** y dejar solo el del cog (o al
   revés), unificando la lógica de "añadir y comprobar lleno".
2. **Persistir los equipos** en SQLite o JSON para sobrevivir a reinicios.
3. **Mover `channel_id` y la composición a configuración** (variables de entorno
   o un archivo `config.yaml`).
4. **Conectar `localization.py`** o eliminarlo si no se va a usar.
5. **Suavizar la limpieza de mensajes**: borrar solo los mensajes propios del bot
   o los de grupos ya completados.

---

## 4. Propuesta de abstracción

> **Mplux no es realmente "de WoW": es un bot de inscripción a eventos con roles y cupos.**

El patrón es: *"define unos roles con un número de plazas, la gente se apunta, se
llena"*. Eso sirve para muchísimo más que míticas:

- Raids y otros juegos (LoL/Valorant: 5 plazas por posición).
- Partidas de rol / D&D (1 DM + 4 jugadores).
- Deportes (portero, defensas, delanteros…).
- Quedadas y eventos con cupo (RSVP).
- Turnos de trabajo o voluntariado.

### Idea central: plantillas configurables

En vez de `{tank:1, dps:3, heal:1}` hardcodeado, definir **plantillas de evento**
por servidor:

```yaml
# config/plantillas.yaml
mitica:
  titulo: "Grupo para míticas"
  roles:
    tank: { emoji: "🛡️", plazas: 1 }
    dps:  { emoji: "⚔️", plazas: 3 }
    heal: { emoji: "💖", plazas: 1 }

raid:
  titulo: "Raid"
  roles:
    tank: { emoji: "🛡️", plazas: 2 }
    dps:  { emoji: "⚔️", plazas: 14 }
    heal: { emoji: "💖", plazas: 4 }

dnd:
  titulo: "Partida de rol"
  roles:
    dm:       { emoji: "📖", plazas: 1 }
    jugador:  { emoji: "🎲", plazas: 4 }
```

Uso genérico:

```
/evento crear plantilla:mitica titulo:"Cúpula +15"
/evento crear plantilla:dnd  titulo:"Maldición de Strahd"
```

### Beneficios

- Un mismo bot vale para cualquier comunidad, no solo WoW.
- Las plantillas se editan sin tocar código.
- Migrando a slash commands + botones gana fiabilidad y autocompletado.

---

## 5. Hoja de ruta sugerida

| Fase | Objetivo | Riesgo |
|---|---|---|
| 1 | Arreglar bug del `on_reaction_add` y persistir estado | Bajo |
| 2 | Mover canal y composición a configuración | Bajo |
| 3 | Migrar a slash commands + botones (Views) | Medio |
| 4 | Plantillas de evento configurables por servidor | Medio |
| 5 | Núcleo compartido con el otro bot (ver doc de mprof) | Medio |
