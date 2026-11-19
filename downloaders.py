"""Pengunduh: Google Drive publik (gdown) dan torrent (libtorrent).

Semua fungsi di modul ini blocking dan memakai CPU/IO, jadi panggil lewat
asyncio loop.run_in_executor dari bot.py.
"""
import re
import time
from pathlib import Path

import gdown
import libtorrent as lt

from config import TORRENT_METADATA_TIMEOUT, TORRENT_PORT

_GDRIVE_FOLDER = re.compile(r"/folders/([a-zA-Z0-9_-]{10,})")
_GDRIVE_FILE = re.compile(r"/d/([a-zA-Z0-9_-]{10,})")
_GDRIVE_ID = re.compile(r"[?&]id=([a-zA-Z0-9_-]{10,})")

_DHT_ROUTERS = (
    ("router.bittorrent.com", 6881),
    ("router.utorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("dht.libtorrent.org", 25401),
)

# DNS di jaringan ini kadang gagal sekejap, jadi unduhan Drive diulang.
GDRIVE_RETRIES = 3


def _retry(fn, attempts=GDRIVE_RETRIES, delay=3):
    last = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))
    raise last


def parse_gdrive_url(url: str):
    """Ubah link Drive menjadi (jenis, id), jenis: 'file' atau 'folder'."""
    if match := _GDRIVE_FOLDER.search(url):
        return "folder", match.group(1)
    if match := _GDRIVE_FILE.search(url):
        return "file", match.group(1)
    if match := _GDRIVE_ID.search(url):
        return "file", match.group(1)
    return None


def download_gdrive(url: str, dest_dir: Path, progress_cb=None) -> list:
    """Unduh file atau folder Drive publik ke dest_dir.

    progress_cb(current, total) dipanggil berkala oleh gdown.
    """
    parsed = parse_gdrive_url(url)
    if not parsed:
        raise ValueError("Link Google Drive tidak dikenali")
    kind, gid = parsed
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if kind == "folder":
        results = _retry(
            lambda: gdown.download_folder(
                f"https://drive.google.com/drive/folders/{gid}",
                output=str(dest_dir),
                quiet=True,
                use_cookies=False,
                retries=GDRIVE_RETRIES,
            )
        )
        if not results:
            raise RuntimeError("Folder gagal diunduh atau kosong")
        return [Path(path) for path in results]

    path = _retry(
        lambda: gdown.download(
            f"https://drive.google.com/uc?id={gid}",
            output=str(dest_dir),
            quiet=True,
            use_cookies=False,
            progress=progress_cb,
            retries=GDRIVE_RETRIES,
        )
    )
    if not path:
        raise RuntimeError("File gagal diunduh (link publik?)")
    return [Path(path)]


def download_torrent(source: str, dest_dir: Path, progress_cb=None) -> list:
    """Unduh magnet atau file .torrent ke dest_dir.

    source: URI magnet: atau path file .torrent.
    progress_cb(done, total, detail) dipanggil berkala; detail misalnya
    'metadata', 'downloading', 'finished'.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    session = lt.session({"listen_interfaces": f"0.0.0.0:{TORRENT_PORT}"})
    session.start_dht()
    session.start_lsd()
    session.start_upnp()
    session.start_natpmp()
    for host, port in _DHT_ROUTERS:
        session.add_dht_router(host, port)

    # libtorrent mengharuskan dict untuk add_magnet_uri, jadi pakai dict di
    # kedua jalur supaya setelan torrent tetap sama.
    params = {
        "save_path": str(dest_dir),
        "storage_mode": lt.storage_mode_t.storage_mode_sparse,
        "flags": int(lt.torrent_flags.auto_managed),
    }
    if source.startswith("magnet:"):
        handle = lt.add_magnet_uri(session, source, params)
    else:
        params["ti"] = lt.torrent_info(source)
        handle = session.add_torrent(params)

    _wait_metadata(handle, progress_cb)
    _wait_download(handle, progress_cb)

    files = handle.get_torrent_info().files()
    paths = [dest_dir / entry.path for entry in files if entry.size > 0 and not entry.pad_file]
    if not paths:
        raise RuntimeError("Torrent tidak memuat file")
    return paths


def _report(progress_cb, done, total, detail):
    if progress_cb:
        progress_cb(done, total, detail)


def _wait_metadata(handle, progress_cb) -> None:
    started = time.monotonic()
    while not handle.has_metadata():
        if time.monotonic() - started > TORRENT_METADATA_TIMEOUT:
            raise RuntimeError("Timeout menunggu metadata torrent")
        _report(progress_cb, 0, 0, "metadata")
        time.sleep(1)
    handle.resume()


def _wait_download(handle, progress_cb) -> None:
    while True:
        status = handle.status()
        if status.errc.value() != 0:
            raise RuntimeError(f"Torrent error: {status.errc.message()}")
        if status.is_finished and status.total_done > 0:
            _report(progress_cb, status.total_wanted, status.total_wanted, "finished")
            return
        _report(progress_cb, status.total_wanted_done, status.total_wanted, "downloading")
        time.sleep(2)
