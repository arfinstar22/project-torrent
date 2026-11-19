"""Konfigurasi bot, dibaca dari environment atau file .env."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Kredensial dari https://my.telegram.org (API ID + API Hash) dan @BotFather.
API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Semua unduhan ditampung di sini, lalu dihapus setelah terkirim.
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", BASE_DIR / "downloads"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "2000") or 2000)

# The Movie Database (TMDB) untuk poster landscape (backdrop)
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

# Watermark default pojok kiri bawah thumbnail. Bisa diganti dari bot;
# nilai dari bot disimpan di settings.json dan menang atas nilai .env.
WATERMARK_TEXT = os.getenv("WATERMARK_TEXT", "@film_indonesia1")
SETTINGS_FILE = BASE_DIR / "settings.json"

# BitTorrent
TORRENT_PORT = int(os.getenv("TORRENT_PORT", "6881") or 6881)
TORRENT_METADATA_TIMEOUT = int(os.getenv("TORRENT_METADATA_TIMEOUT", "300") or 300)

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def watermark() -> str:
    """Watermark aktif: pilihan dari bot, atau default dari .env."""
    value = _settings().get("watermark")
    return value if isinstance(value, str) else WATERMARK_TEXT


def set_watermark(text: str) -> None:
    data = _settings()
    data["watermark"] = text
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))

