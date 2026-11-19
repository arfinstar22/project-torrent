"""Pengunduh: Google Drive publik (gdown) dan torrent (libtorrent).

Semua fungsi di modul ini blocking dan memakai CPU/IO, jadi panggil lewat
asyncio loop.run_in_executor dari bot.py.
"""
import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

import gdown
import libtorrent as lt
import yt_dlp

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


# ==========================================
# MULTI-CONNECTION & DIRECT DOWNLOADERS (aria2c)
# ==========================================

def _download_with_aria2(
    url: str,
    dest_dir: Path,
    filename: str | None = None,
    headers: dict | None = None,
    progress_cb=None,
) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "aria2c",
        "--dir", str(dest_dir),
        "-x", "16",
        "-s", "16",
        "-j", "4",
        "-k", "1M",
        "-c",
        "--file-allocation=none",
        "--summary-interval=1",
        "--console-log-level=warn",
    ]
    if filename:
        cmd.extend(["--out", filename])
    if headers:
        for k, v in headers.items():
            cmd.extend(["--header", f"{k}: {v}"])
    cmd.append(url)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    prog_regex = re.compile(
        r"\[#\w+\s+([\d.]+)([KMGT]?i?B)/([\d.]+)([KMGT]?i?B)\((\d+)%\).*?ETA:?(\w+)?"
    )

    def _parse_size(val: float, unit: str) -> int:
        u = unit.lower()
        if "g" in u:
            return int(val * 1024 * 1024 * 1024)
        if "m" in u:
            return int(val * 1024 * 1024)
        if "k" in u:
            return int(val * 1024)
        return int(val)

    if proc.stdout:
        for line in proc.stdout:
            m = prog_regex.search(line)
            if m and progress_cb:
                cur_val, cur_u, tot_val, tot_u, pct, eta = m.groups()
                try:
                    current = _parse_size(float(cur_val), cur_u)
                    total = _parse_size(float(tot_val), tot_u)
                    detail = f"{pct}% | ETA: {eta}" if eta else f"{pct}%"
                    progress_cb(current, total, detail)
                except Exception:
                    pass

    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"aria2c gagal mengunduh (exit code {proc.returncode})")

    if filename and (dest_dir / filename).exists():
        return dest_dir / filename
    candidates = [p for p in dest_dir.iterdir() if p.is_file() and not p.name.endswith(".aria2")]
    if not candidates:
        raise RuntimeError("File hasil unduhan aria2c tidak ditemukan")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _download_stream(
    url: str,
    dest_dir: Path,
    filename: str | None = None,
    headers: dict | None = None,
    progress_cb=None,
) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        if not filename:
            cd = resp.headers.get("Content-Disposition", "")
            if "filename=" in cd:
                m = re.search(r'filename=["\']?([^"\';\r\n]+)', cd)
                if m:
                    filename = Path(m.group(1).strip()).name
            if not filename:
                filename = Path(urllib.parse.urlparse(url).path).name or "downloaded_file"

        target = dest_dir / filename
        total = int(resp.headers.get("Content-Length", 0))
        current = 0
        chunk_size = 512 * 1024

        with open(target, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                current += len(chunk)
                if progress_cb:
                    progress_cb(current, total, "mengunduh...")
    return target


def download_url(
    url: str,
    dest_dir: Path,
    filename: str | None = None,
    headers: dict | None = None,
    progress_cb=None,
) -> Path:
    """Unduh URL memakai aria2c (16 parallel connections). Fallback ke streaming jika aria2c absen."""
    if shutil.which("aria2c"):
        try:
            return _download_with_aria2(url, dest_dir, filename, headers, progress_cb)
        except Exception:
            pass
    return _download_stream(url, dest_dir, filename, headers, progress_cb)


# ==========================================
# FILE HOSTER RESOLVERS (MediaFire, GoFile, Pixeldrain, TeraBox)
# ==========================================

def resolve_mediafire(url: str) -> tuple[str, str | None]:
    """Ekstrak direct download link dari MediaFire."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    m = re.search(r'aria-label=["\']Download file["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'id=["\']downloadButton["\'][^>]*href=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'href=["\'](https?://download\d+\.mediafire\.com/[^"\']+)["\']', html)
    if not m:
        raise ValueError("Gagal mengekstrak direct link MediaFire (link mati atau privat)")

    direct_url = m.group(1)
    name_m = re.search(r'<div class="filename">([^<]+)</div>', html)
    filename = name_m.group(1).strip() if name_m else None
    return direct_url, filename


def resolve_pixeldrain(url: str) -> tuple[str, str | None]:
    """Ekstrak direct download link dari Pixeldrain."""
    m = re.search(r"pixeldrain\.com/u/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError("Link Pixeldrain tidak valid")
    fid = m.group(1)
    direct_url = f"https://pixeldrain.com/api/file/{fid}"
    filename = None
    try:
        req = urllib.request.Request(
            f"https://pixeldrain.com/api/file/{fid}/info",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
            filename = data.get("name")
    except Exception:
        pass
    return direct_url, filename


def resolve_gofile(url: str) -> list[tuple[str, str, dict]]:
    """Ekstrak direct link GoFile lewat guest token API."""
    m = re.search(r"gofile\.io/d/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError("Link GoFile tidak valid")
    cid = m.group(1)

    req_acc = urllib.request.Request(
        "https://api.gofile.io/accounts",
        data=b"",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req_acc, timeout=10) as resp:
        acc = json.loads(resp.read().decode())
    token = acc.get("data", {}).get("token")
    if not token:
        raise RuntimeError("Gagal mendapatkan token akun GoFile")

    req_cnt = urllib.request.Request(
        f"https://api.gofile.io/contents/{cid}?wt=4fd6sg89d7s6",
        headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req_cnt, timeout=10) as resp:
        cnt = json.loads(resp.read().decode())

    children = cnt.get("data", {}).get("children", {})
    files = []
    for _, item in children.items():
        if item.get("type") == "file" and item.get("link"):
            headers = {"Authorization": f"Bearer {token}"}
            files.append((item["link"], item.get("name"), headers))
    if not files:
        raise RuntimeError("Tidak ada file ditemukan di link GoFile")
    return files


def resolve_terabox(url: str) -> tuple[str, str | None]:
    """Ekstrak direct download link dari TeraBox share link."""
    m = re.search(r"/s/([a-zA-Z0-9_-]+)", url)
    if not m:
        raise ValueError("Link TeraBox tidak valid (harus memuat /s/...)")
    surl = m.group(1).lstrip("1")

    # 1. Coba public API gateways
    gateways = [
        f"https://terabox-dl.qtcloud.workers.dev/api/get-info?shorturl={surl}",
        f"https://teraboxvideodownloader.neon.run/api?url={urllib.parse.quote(url)}",
    ]
    for gw in gateways:
        try:
            req = urllib.request.Request(gw, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                if "download_link" in data:
                    dl = data["download_link"].get("url_1") or data["download_link"].get("url")
                    if dl:
                        return dl, data.get("file_name")
                if "download" in data and data["download"]:
                    return data["download"], data.get("filename")
                if "list" in data and data["list"]:
                    return data["list"][0].get("dlink"), data["list"][0].get("server_filename")
        except Exception:
            pass

    # 2. Coba endpoint langsung terabox
    try:
        api = f"https://www.terabox.app/share/list?app_id=250528&shorturl={surl}&root=1"
        req = urllib.request.Request(api, headers={"User-Agent": "Mozilla/5.0", "Referer": url})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            if "list" in data and data["list"]:
                return data["list"][0].get("dlink"), data["list"][0].get("server_filename")
    except Exception:
        pass

    raise RuntimeError("Gagal mengekstrak direct link TeraBox (link kedaluwarsa atau dibatasi)")


def download_ytdlp(url: str, dest_dir: Path, progress_cb=None) -> list[Path]:
    """Unduh video dari 1000+ situs (YouTube, TikTok, IG, FB, dll.) via yt-dlp."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / "%(title).100s.%(ext)s")

    def hook(d):
        if d.get("status") == "downloading" and progress_cb:
            current = d.get("downloaded_bytes", 0)
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed_b = d.get("speed") or 0
            speed = f"{speed_b / 1024 / 1024:.1f} MB/s" if speed_b else ""
            progress_cb(current, total, speed)

    ydl_opts = {
        "outtmpl": out_tmpl,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if shutil.which("aria2c"):
        ydl_opts["external_downloader"] = "aria2c"
        ydl_opts["external_downloader_args"] = ["-x", "16", "-s", "16", "-k", "1M"]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    files = [p for p in dest_dir.iterdir() if p.is_file() and not p.name.endswith(".part")]
    if not files:
        raise RuntimeError("Gagal mengunduh video via yt-dlp")
    return files
