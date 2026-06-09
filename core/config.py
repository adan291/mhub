import os
import json
from dotenv import load_dotenv
from discord import Intents

# Raíz del proyecto (carpeta padre de core/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")


class Config:
    @staticmethod
    def load_env():
        load_dotenv(".env")
        load_dotenv(".env.local")

    @staticmethod
    def get_token():
        return os.getenv('DISCORD_TOKEN_HUB')

    @staticmethod
    def get_intents():
        # Con slash commands y botones no necesitamos message_content.
        return Intents.default()

    @staticmethod
    def get_channel_id():
        """Canal opcional donde se permiten los comandos. None = cualquier canal."""
        value = os.getenv('CHANNEL_ID')
        return int(value) if value else None

    @staticmethod
    def get_guild_id():
        """Servidor para sincronizar slash commands al instante (desarrollo)."""
        value = os.getenv('GUILD_ID')
        return int(value) if value else None

    @staticmethod
    def get_data_dir():
        """Directorio persistente. En Fly.io está montado en /data."""
        return os.getenv('DATA_DIR', os.path.join(BASE_DIR, "data"))

    @staticmethod
    def get_db_path(filename):
        """Ruta a un archivo de base de datos dentro del directorio persistente.
        Cada módulo usa el suyo (events.sqlite, registry.sqlite, ...)."""
        return os.path.join(Config.get_data_dir(), filename)

    @staticmethod
    def load_json(filename):
        with open(os.path.join(CONFIG_DIR, filename), "r", encoding="utf-8") as f:
            return json.load(f)
