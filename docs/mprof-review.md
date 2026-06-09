# mprof — Revisión, mejoras y propuesta de abstracción

> Bot de Discord para registrar y buscar artesanos (profesiones) de World of Warcraft.

---

## 1. Qué hace hoy

Permite a una comunidad llevar un registro de quién puede fabricar qué objeto.
Comandos disponibles (por prefijo `!mprof`):

- `registrar <personaje> <categoria> <enlace>` — registra un artesano.
- `buscar <categoria> <objeto>` — busca quién fabrica un objeto.
- `list` — lista todos los registros.
- `borrar <personaje> <categoria> <enlace>` — elimina un registro propio.
- `backup` — envía el archivo SQLite al canal.

Los datos se guardan en SQLite (`data/crafters_db.sqlite`). Las categorías son las
8 profesiones de WoW y los enlaces deben ser de `wowhead.com`.

### Estructura de archivos

| Archivo | Responsabilidad |
|---|---|
| `main.py` | Arranque, carga de entorno, webserver de salud |
| `bot.py` | Cliente de Discord, enrutado de comandos por `on_message` |
| `commands.py` | Lógica de registrar/buscar/listar/borrar |
| `database.py` | Acceso a SQLite y normalización de texto |
| `profesiones.py` | Lista de categorías válidas (WoW) |
| `utils.py` | **Duplicado muerto** de categorías + `normalize_text` |
| `config.py` | Token e intents |
| `webserver.py` | Health check para Fly.io |

---

## 2. Problemas detectados

### 🔴 Críticos

- **Persistencia efímera en Fly.io.** El SQLite se guarda en `./data` dentro del
  contenedor. En Fly.io ese disco es efímero: **cada redeploy borra todos los
  registros**. La existencia del comando `backup` sugiere que ya os habéis topado
  con esto. Falta un volumen persistente (o una base externa).

### 🟠 Importantes

- **Código duplicado e inconsistente.** `VALID_CATEGORIES` y `normalize_text`
  están en `profesiones.py` **y** en `utils.py`, con una discrepancia
  (`sastreria` vs `sastrería`). `utils.py` parece muerto y debería eliminarse.
- **Atado a WoW.** La validación exige enlaces de `https://www.wowhead.com/` y las
  categorías son profesiones de WoW. Es lo más específico del dominio.
- **Parseo manual de texto.** `message.content.split(' ', n)` es frágil ante
  espacios o comillas. Los slash commands resuelven esto con parámetros tipados.

### 🟡 Menores

- En `get_crafters` se reutiliza el nombre de variable `item_name`/
  `normalized_item_name` dentro del bucle, lo que dificulta leer el código.
- La tabla no tiene índices; con muchos registros las búsquedas se degradan.
- El comando `backup` envía la base entera al canal (posible fuga de `user_id`).

---

## 3. Mejoras recomendadas (sin reescribir)

1. **Persistencia real**: montar un volumen de Fly.io para `./data`, o migrar a
   Postgres. Es lo más urgente.
2. **Eliminar `utils.py`** y dejar una única fuente para categorías y
   `normalize_text`.
3. **Añadir índices** en `(normalized_category, normalized_item_name)`.
4. **Parametrizar la validación de enlaces**: en vez de exigir `wowhead.com`,
   permitir cualquier URL o un patrón configurable.
5. **Restringir `backup`** a administradores y enviarlo por DM, no al canal.

---

## 4. Propuesta de abstracción

> **mprof no es realmente "de WoW": es un directorio/registro con categorías y proveedores.**

El patrón es: *"registra entradas bajo categorías, con un enlace y un dueño;
busca, lista, borra"*. Eso sirve para:

- Directorio de habilidades/servicios de una comunidad.
- Catálogo de objetos de cualquier juego.
- Lista de recursos o enlaces por tema.
- Mini-marketplace de la comunidad.

### Idea central: categorías y validación configurables

Quitar las profesiones de WoW y el dominio de Wowhead, y hacerlos configurables
por servidor:

```yaml
# config/registro.yaml
nombre_entrada: "objeto"        # "objeto", "servicio", "recurso"...
nombre_proveedor: "artesano"    # "artesano", "experto", "vendedor"...
validacion_enlace: ".*"         # cualquier URL; o "https://www.wowhead.com/.*"
categorias:
  - alquimia
  - herreria
  - sastreria
  - ingenieria
```

Así el mismo bot sirve para "quién sabe de X" en una comunidad técnica, o "quién
vende Y" en un marketplace, solo cambiando el YAML.

### Modelo de datos genérico

```
entrada(
  categoria, nombre, enlace,
  norm_nombre, norm_categoria,
  proveedor, user_id
)
```

Es exactamente el esquema actual, pero con nombres neutrales (`entrada`/
`proveedor` en lugar de `crafter`/`item`).

---

## 5. Hoja de ruta sugerida

| Fase | Objetivo | Riesgo |
|---|---|---|
| 1 | Volumen persistente / Postgres (no perder datos) | Bajo |
| 2 | Eliminar `utils.py` y unificar categorías | Bajo |
| 3 | Migrar a slash commands con parámetros tipados | Medio |
| 4 | Categorías y validación de enlaces configurables | Medio |
| 5 | Núcleo compartido con el otro bot (ver doc de Mplux) | Medio |

---

## 6. Visión común con Mplux

Ambos bots comparten mucho código (`config.py`, `webserver.py`, `Dockerfile`,
`fly.toml`, manejo de token e intents). La propuesta de mayor alcance es un único
proyecto con núcleo compartido y los dos comportamientos como módulos:

```
discord-toolkit/
  core/
    config.py        # token, intents, config por servidor
    webserver.py     # health check (compartido)
    storage.py       # persistencia
  modules/
    signups/         # ex-Mplux: plantillas de roles + botones
    registry/        # ex-mprof: categorías + entradas
  config/
    guilds.yaml      # plantillas y categorías por servidor
  main.py
```
