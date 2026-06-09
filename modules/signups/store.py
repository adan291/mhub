import os
import json
import sqlite3


class EventStore:
    """Persistencia de eventos en SQLite. Cada evento guarda su estado
    (qué usuario ocupa cada rol) como JSON para sobrevivir a reinicios.

    A partir de mhub-platform, cada evento también persiste su Composicion
    (roles + plazas + emoji) y su estado de Ciclo_De_Vida (`status`). Para los
    eventos creados antes de esta feature, la composición se reconstruye de
    forma perezosa desde `templates.json[template_key]` al cargarlos.
    """

    def __init__(self, db_path, templates=None):
        self.db_path = db_path
        # Plantillas por defecto usadas para la migración perezosa de eventos
        # antiguos sin `composition_json`.
        self.templates = templates or {}
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._create_table()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _create_table(self):
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    message_id       INTEGER PRIMARY KEY,
                    channel_id       INTEGER NOT NULL,
                    guild_id         INTEGER,
                    template_key     TEXT,
                    title            TEXT NOT NULL,
                    state_json       TEXT NOT NULL,
                    composition_json TEXT,
                    status           TEXT DEFAULT 'activo',
                    created_at       TEXT DEFAULT (datetime('now'))
                )
            ''')
            # Migración de esquema para bases de datos creadas antes de esta
            # feature: añade las columnas nuevas si aún no existen.
            self._ensure_column(conn, "composition_json", "TEXT")
            self._ensure_column(conn, "status", "TEXT DEFAULT 'activo'")
            conn.commit()

    @staticmethod
    def _ensure_column(conn, name, ddl):
        existing = [row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()]
        if name not in existing:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} {ddl}")

    _SELECT = (
        'SELECT message_id, channel_id, guild_id, template_key, title, '
        'state_json, composition_json, status, created_at FROM events'
    )

    def create(self, message_id, channel_id, guild_id, template_key, title, state,
               composition=None, status='activo'):
        with self._connect() as conn:
            conn.execute(
                '''INSERT OR REPLACE INTO events
                   (message_id, channel_id, guild_id, template_key, title,
                    state_json, composition_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (message_id, channel_id, guild_id, template_key, title,
                 json.dumps(state),
                 json.dumps(composition) if composition is not None else None,
                 status)
            )
            conn.commit()

    def update_state(self, message_id, state):
        with self._connect() as conn:
            conn.execute(
                'UPDATE events SET state_json = ? WHERE message_id = ?',
                (json.dumps(state), message_id)
            )
            conn.commit()

    def update_status(self, message_id, status):
        """Persiste el estado de Ciclo_De_Vida de un evento (Req 11.5)."""
        with self._connect() as conn:
            conn.execute(
                'UPDATE events SET status = ? WHERE message_id = ?',
                (status, message_id)
            )
            conn.commit()

    def get(self, message_id):
        with self._connect() as conn:
            row = conn.execute(
                self._SELECT + ' WHERE message_id = ?',
                (message_id,)
            ).fetchone()
            if not row:
                return None
            result = self._row_to_dict(row)
            self._backfill_composition(conn, row, result)
            conn.commit()
        return result

    def all(self):
        with self._connect() as conn:
            rows = conn.execute(self._SELECT).fetchall()
            results = []
            for row in rows:
                result = self._row_to_dict(row)
                self._backfill_composition(conn, row, result)
                results.append(result)
            conn.commit()
        return results

    def delete(self, message_id):
        with self._connect() as conn:
            conn.execute('DELETE FROM events WHERE message_id = ?', (message_id,))
            conn.commit()

    def _backfill_composition(self, conn, row, result):
        """Migración perezosa: si la fila no tenía `composition_json` y se ha
        podido reconstruir desde la plantilla, persiste el resultado para no
        recalcularlo en cargas futuras."""
        if row[6] is None and result["composition"]:
            conn.execute(
                'UPDATE events SET composition_json = ? WHERE message_id = ?',
                (json.dumps(result["composition"]), row[0])
            )

    def _reconstruct_composition(self, template_key):
        """Reconstruye la composición de un evento antiguo desde la plantilla
        de la que se originó. Devuelve {} si la plantilla ya no existe."""
        template = self.templates.get(template_key)
        if not template:
            return {}
        # Copia profunda para no exponer ni mutar la plantilla compartida.
        return json.loads(json.dumps(template.get("roles", {})))

    def _row_to_dict(self, row):
        composition = (
            json.loads(row[6]) if row[6] is not None
            else self._reconstruct_composition(row[3])
        )
        return {
            "message_id": row[0],
            "channel_id": row[1],
            "guild_id": row[2],
            "template_key": row[3],
            "title": row[4],
            "state": json.loads(row[5]),
            "composition": composition,
            "status": row[7] if row[7] is not None else 'activo',
            "created_at": row[8],
        }
