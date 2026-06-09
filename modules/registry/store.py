import os
import sqlite3
import unicodedata


def normalize_text(text):
    """Normaliza el texto eliminando tildes y pasando a minúsculas."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    ).lower()


class RegistryStore:
    """Registro genérico de entradas: cada entrada tiene una categoría,
    un nombre, un enlace y un proveedor (con su user_id de Discord)."""

    # Expuesto como método estático para que otros módulos (p. ej. el puente
    # skills↔grupos) normalicen claves con la MISMA lógica que el directorio,
    # sin reimplementarla ni importar el paquete (que arrastra `discord`).
    normalize_text = staticmethod(normalize_text)

    def __init__(self, db_path):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._create_table()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _create_table(self):
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS entries (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    category      TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    link          TEXT NOT NULL,
                    norm_category TEXT NOT NULL,
                    norm_name     TEXT NOT NULL,
                    provider      TEXT NOT NULL,
                    user_id       INTEGER NOT NULL
                )
            ''')
            conn.execute(
                'CREATE INDEX IF NOT EXISTS idx_entries_cat_name '
                'ON entries (norm_category, norm_name)'
            )
            conn.commit()

    def add(self, provider, category, name, link, user_id):
        """Añade una entrada. Devuelve True si se añadió, False si ya existía."""
        norm_category = normalize_text(category)
        norm_name = normalize_text(name)
        with self._connect() as conn:
            exists = conn.execute(
                'SELECT 1 FROM entries WHERE norm_category = ? AND norm_name = ? '
                'AND link = ? AND user_id = ?',
                (norm_category, norm_name, link, user_id)
            ).fetchone()
            if exists:
                return False
            conn.execute(
                'INSERT INTO entries (category, name, link, norm_category, norm_name, provider, user_id) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (category, name, link, norm_category, norm_name, provider, user_id)
            )
            conn.commit()
            return True

    def search(self, category, name):
        """Busca entradas por categoría y nombre (coincidencia parcial)."""
        norm_category = normalize_text(category)
        norm_name = normalize_text(name)
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT name, link, GROUP_CONCAT(provider, ", ") '
                'FROM entries WHERE norm_category = ? AND norm_name LIKE ? '
                'GROUP BY name, link',
                (norm_category, f'%{norm_name}%')
            ).fetchall()
        return [(r[0], r[1], r[2].split(", ")) for r in rows]

    def list_all(self):
        """Lista todas las entradas agrupadas."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT category, name, link, GROUP_CONCAT(provider, ", ") '
                'FROM entries GROUP BY category, name, link ORDER BY category, name'
            ).fetchall()
        return [(r[0], r[1], r[2], r[3].split(", ")) for r in rows]

    def all_entries(self):
        """Devuelve TODAS las entradas individuales con su `user_id` y claves
        normalizadas, sin agrupar.

        A diferencia de `search`/`list_all` (orientados a presentación, que
        agrupan y descartan el `user_id`), este método expone las filas crudas
        para consumidores que necesitan identificar a la Persona dueña de cada
        entrada y casar por clave normalizada (p. ej. el módulo puente). Método
        de solo lectura; no altera el comportamiento de los existentes."""
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT id, category, name, link, norm_category, norm_name, '
                'provider, user_id FROM entries ORDER BY id'
            ).fetchall()
        return [
            {
                "id": r[0],
                "category": r[1],
                "name": r[2],
                "link": r[3],
                "norm_category": r[4],
                "norm_name": r[5],
                "provider": r[6],
                "user_id": r[7],
            }
            for r in rows
        ]

    def delete(self, provider, category, name, link, user_id):
        """Elimina una entrada propia. Devuelve 'deleted', 'not_found' o 'no_permission'."""
        norm_category = normalize_text(category)
        norm_name = normalize_text(name)
        with self._connect() as conn:
            row = conn.execute(
                'SELECT user_id FROM entries WHERE provider = ? AND norm_category = ? '
                'AND norm_name = ? AND link = ?',
                (provider, norm_category, norm_name, link)
            ).fetchone()
            if row is None:
                return 'not_found'
            if row[0] != user_id:
                return 'no_permission'
            conn.execute(
                'DELETE FROM entries WHERE provider = ? AND norm_category = ? '
                'AND norm_name = ? AND link = ? AND user_id = ?',
                (provider, norm_category, norm_name, link, user_id)
            )
            conn.commit()
            return 'deleted'

    def delete_by_id(self, entry_id, user_id):
        """Elimina una entrada por su `id` SOLO si pertenece a `user_id`.

        Pensado para la API_Web, donde el cliente identifica la entrada por su
        id de fila y la propiedad se comprueba contra el `user_id` de la sesión
        (Req 8.4, 8.5). Devuelve:
          - 'not_found'     si no existe ninguna fila con ese id,
          - 'no_permission' si la fila pertenece a otra Identidad (NO se borra),
          - 'deleted'       si se eliminó correctamente.

        Método ADITIVO: no altera el comportamiento de `delete()` (que opera por
        clave provider/categoría/nombre/enlace). La comparación de propiedad se
        hace por texto para tolerar que el `user_id` llegue como str (sesión web)
        frente al INTEGER almacenado."""
        with self._connect() as conn:
            row = conn.execute(
                'SELECT user_id FROM entries WHERE id = ?', (entry_id,)
            ).fetchone()
            if row is None:
                return 'not_found'
            if str(row[0]) != str(user_id):
                return 'no_permission'
            conn.execute('DELETE FROM entries WHERE id = ?', (entry_id,))
            conn.commit()
            return 'deleted'
