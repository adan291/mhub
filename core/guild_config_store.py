import os
import json
import sqlite3


class GuildConfigStore:
    """Persistencia de la Configuracion_Por_Servidor: plantillas y categorías
    propias de cada Servidor de Discord, identificadas por `guild_id` (Req 3).

    Cada Servidor puede definir sus propias Plantillas (`guild_templates`) y
    categorías (`guild_categories`). El aislamiento entre servidores es natural
    por el `guild_id` incluido en la clave primaria de ambas tablas (Req 3.5).

    Resolución con fallback (Req 3.3): si un Servidor tiene filas propias, se
    usan; si no tiene ninguna, se devuelve la configuración por defecto incluida
    en la Plataforma (`templates.json` / `registry.json`). No se requiere un paso
    de configuración inicial: la primera modificación de un Servidor crea sus
    filas (Req 3.4).

    Como el resto de la Plataforma, todo acceso a la base de datos pasa por este
    store; ningún otro componente toca SQLite directamente (Req 14.2).
    """

    def __init__(self, db_path, default_templates=None, default_categories=None):
        self.db_path = db_path
        # Configuración por defecto de la Plataforma usada como fallback cuando
        # un Servidor no tiene configuración propia.
        #   default_templates: {template_key: {"titulo": str, "roles": {...}}}
        #   default_categories: [str, ...]
        self.default_templates = default_templates or {}
        self.default_categories = list(default_categories or [])
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._create_tables()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _create_tables(self):
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_templates (
                    guild_id     INTEGER NOT NULL,
                    template_key TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    roles_json   TEXT NOT NULL,
                    PRIMARY KEY (guild_id, template_key)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS guild_categories (
                    guild_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    PRIMARY KEY (guild_id, category)
                )
            ''')
            conn.commit()

    # ------------------------------------------------------------------
    # Plantillas — lectura/escritura por guild_id
    # ------------------------------------------------------------------

    def get_guild_templates(self, guild_id):
        """Devuelve únicamente las Plantillas propias del Servidor (sin
        fallback). {template_key: {"titulo": str, "roles": {...}}}. Vacío si el
        Servidor no tiene configuración propia."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT template_key, title, roles_json FROM guild_templates '
                'WHERE guild_id = ?',
                (guild_id,)
            ).fetchall()
        return {
            row[0]: {"titulo": row[1], "roles": json.loads(row[2])}
            for row in rows
        }

    def set_template(self, guild_id, template_key, title, roles):
        """Crea o actualiza una Plantilla del Servidor (Req 3.4). La primera
        modificación crea las filas del Servidor sin paso de configuración
        previo."""
        with self._connect() as conn:
            conn.execute(
                '''INSERT OR REPLACE INTO guild_templates
                   (guild_id, template_key, title, roles_json)
                   VALUES (?, ?, ?, ?)''',
                (guild_id, template_key, title, json.dumps(roles))
            )
            conn.commit()

    def delete_template(self, guild_id, template_key):
        """Elimina una Plantilla del Servidor. Devuelve True si existía."""
        with self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM guild_templates WHERE guild_id = ? AND template_key = ?',
                (guild_id, template_key)
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Categorías — lectura/escritura por guild_id
    # ------------------------------------------------------------------

    def get_guild_categories(self, guild_id):
        """Devuelve únicamente las categorías propias del Servidor (sin
        fallback). Lista vacía si el Servidor no tiene configuración propia."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT category FROM guild_categories WHERE guild_id = ? '
                'ORDER BY category',
                (guild_id,)
            ).fetchall()
        return [row[0] for row in rows]

    def add_category(self, guild_id, category):
        """Añade una categoría al Servidor (Req 3.4). Devuelve True si se añadió,
        False si ya existía."""
        with self._connect() as conn:
            exists = conn.execute(
                'SELECT 1 FROM guild_categories WHERE guild_id = ? AND category = ?',
                (guild_id, category)
            ).fetchone()
            if exists:
                return False
            conn.execute(
                'INSERT INTO guild_categories (guild_id, category) VALUES (?, ?)',
                (guild_id, category)
            )
            conn.commit()
            return True

    def remove_category(self, guild_id, category):
        """Elimina una categoría del Servidor. Devuelve True si existía."""
        with self._connect() as conn:
            cur = conn.execute(
                'DELETE FROM guild_categories WHERE guild_id = ? AND category = ?',
                (guild_id, category)
            )
            conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Resolución con fallback a la configuración por defecto (Req 3.2, 3.3)
    # ------------------------------------------------------------------

    def resolve_templates(self, guild_id):
        """Resuelve las Plantillas aplicables a un Servidor: usa las propias si
        las tiene; en caso contrario, la configuración por defecto de la
        Plataforma (Req 3.3). Devuelve una copia para no exponer ni mutar el
        estado compartido por defecto."""
        own = self.get_guild_templates(guild_id)
        if own:
            return own
        return json.loads(json.dumps(self.default_templates))

    def resolve_template(self, guild_id, template_key):
        """Resuelve una Plantilla concreta de un Servidor con fallback. Devuelve
        None si no existe ni en la configuración del Servidor ni por defecto."""
        return self.resolve_templates(guild_id).get(template_key)

    def resolve_categories(self, guild_id):
        """Resuelve las categorías aplicables a un Servidor: usa las propias si
        las tiene; en caso contrario, las categorías por defecto de la
        Plataforma (Req 3.3)."""
        own = self.get_guild_categories(guild_id)
        if own:
            return own
        return list(self.default_categories)


def get_or_create_guild_config_store(bot, *, default_templates=None,
                                     default_categories=None):
    """Devuelve el GuildConfigStore compartido del bot, creándolo si aún no
    existe (Req 3).

    Ambos módulos (`signups` y `registry`) comparten un único store por
    `guild_config.sqlite` accesible como ``bot.guild_config_store``. Cada módulo
    aporta sus propios valores por defecto (plantillas desde `templates.json`,
    categorías desde `registry.json`); como el orden de carga de módulos no está
    garantizado, este helper rellena los valores por defecto que falten cuando
    el store ya fue creado por el otro módulo.

    El import de ``core.config`` es perezoso para que este módulo siga siendo
    importable sin el paquete `discord` (la capa de dominio se testea sin él).
    """
    from core import config

    store = getattr(bot, "guild_config_store", None)
    if store is None:
        store = GuildConfigStore(
            config.Config.get_db_path("guild_config.sqlite"),
            default_templates=default_templates,
            default_categories=default_categories,
        )
        bot.guild_config_store = store
    else:
        if default_templates and not store.default_templates:
            store.default_templates = default_templates
        if default_categories and not store.default_categories:
            store.default_categories = list(default_categories)
    return store
