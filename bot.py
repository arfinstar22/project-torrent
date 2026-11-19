"""Bot Telegram: unduh link Google Drive publik dan torrent, lalu kirim filenya.

Cara pakai: kirim link Drive publik atau link magnet / file .torrent ke bot.
"""
import asyncio
import html
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import downloaders

MENU_TEXT = (
    "🤖 <b>Bot Unduh &amp; Unggah Super</b>\n\n"
    "Kirim ke chat ini:\n"
    "• <b>Google Drive</b> (file / folder publik)\n"
    "• <b>TeraBox, MediaFire, Pixeldrain, GoFile</b>\n"
    "• <b>Link Langsung HTTP/HTTPS</b> (video, zip, file apapun)\n"
    "• <b>YouTube, TikTok (no watermark), Reels, Twitter/X, FB</b>\n"
    "• <b>Torrent</b> (link magnet / file <code>.torrent</code>)\n\n"
    "⚡ <b>Kecepatan:</b> Multi-koneksi aria2c (16 stream paralel).\n"
    "🎬 <b>Cover:</b> Poster landscape resmi (TMDB) + watermark otomatis.\n"
    "✂️ <b>Ukuran:</b> Video &gt; 2 GB otomatis dipotong rapi per part."
)

HELP_TEXT = (
    "📥 <b>Cara pakai</b>\n\n"
    "1. Kirim link apapun ke bot ini:\n"
    "   • Google Drive publik (file atau folder)\n"
    "   • TeraBox, MediaFire, Pixeldrain, GoFile\n"
    "   • Link langsung unduhan (URL file .mp4, .mkv, .zip, dll.)\n"
    "   • Link video sosial media (YouTube, TikTok, Instagram, dll.)\n"
    "   • Link magnet atau file <code>.torrent</code>\n"
    "2. Bot otomatis mengunduh dengan multi-koneksi kecepatan tinggi.\n"
    "3. Film/video otomatis diberi cover landscape resmi TMDB ber-watermark.\n"
    "4. Video &gt; 2 GB otomatis dipecah menjadi beberapa bagian (Part 1, Part 2).\n"
    "5. File diterima di chat ini, unduhan sementara langsung dihapus dari disk."
)

LIMITS_TEXT = (
    "📦 <b>Batas &amp; format</b>\n\n"
    "• Ukuran unggah: maksimal 2 GB per file (batas MTProto).\n"
    "• Thumbnail: JPEG maksimal 320 px, dengan watermark.\n"
    "• Drive: hanya link publik; folder tanpa progress per file.\n"
    "• Torrent: butuh peer/tracker, port default 6881.\n"
    "• Media: mp4/mkv/webm/mov/avi, mp3/flac/m4a/wav, jpg/png/webp, gif.\n"
    "• Format lain tetap dikirim sebagai dokumen.\n"
    "• Satu tugas per chat; kiriman berikutnya masuk antrian FIFO."
)

_queues: dict = {}
_queue_workers: dict = {}
_running: dict = {}
_pending_watermark: set = set()


def _is_link(text: str) -> bool:
    lowered = text.lower()
    return (
        lowered.startswith(("http://", "https://", "magnet:"))
        or "drive.google.com" in lowered
    )


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📥 Cara pakai", callback_data="menu:help"),
                InlineKeyboardButton("🖌 Watermark", callback_data="menu:wm"),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data="menu:status"),
                InlineKeyboardButton("📦 Batas & format", callback_data="menu:limits"),
            ],
        ]
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Menu", callback_data="menu:main")]]
    )


def _watermark_panel() -> str:
    current = config.watermark() or "(nonaktif)"
    return (
        "🖌 <b>Watermark thumbnail</b>\n\n"
        f"Sekarang: <code>{html.escape(current)}</code>\n\n"
        "Watermark hanya di cover video/GIF, bukan di dalam video.\n"
        "Tekan <b>Ganti</b>, lalu kirim teks baru (1-40 karakter)."
    )


def _watermark_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Ganti", callback_data="wm:edit"),
                InlineKeyboardButton("♻️ Reset", callback_data="wm:reset"),
            ],
            [InlineKeyboardButton("⬅️ Menu", callback_data="menu:main")],
        ]
    )


def _status_panel(chat_id: int) -> str:
    running = bool(_running.get(chat_id))
    waiting = _queues[chat_id].qsize() if chat_id in _queues else 0
    if running:
        state = "⏳ Sedang mengunduh/mengunggah"
    else:
        state = "✅ Tidak ada tugas"
    queue_line = f"\n🕒 Menunggu: {waiting} tugas" if waiting else ""
    return (
        "📊 <b>Status chat ini</b>\n\n"
        f"{state}{queue_line}\n"
        f"🖌 Watermark: <code>{html.escape(config.watermark() or '(nonaktif)')}</code>\n\n"
        "Tugas baru masuk antrian dan diproses berurutan."
    )


def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Segarkan", callback_data="menu:status")],
            [InlineKeyboardButton("⬅️ Menu", callback_data="menu:main")],
        ]
    )


def _queue_for(chat_id: int) -> asyncio.Queue:
    if chat_id not in _queues:
        _queues[chat_id] = asyncio.Queue()
        _queue_workers[chat_id] = asyncio.create_task(_queue_worker(chat_id))
    return _queues[chat_id]


async def _enqueue(chat_id: int, message: Message, label: str, job) -> None:
    """Masukkan tugas ke antrian chat; kirim satu pesan status per tugas."""
    queue = _queue_for(chat_id)
    ahead = queue.qsize() + (1 if _running.get(chat_id) else 0)
    if ahead:
        status = await message.reply(f"🕒 Antrian #{ahead + 1}: {label}")
    else:
        status = await message.reply(f"⏳ Menyiapkan: {label}")
    await queue.put((status, job))


async def _queue_worker(chat_id: int) -> None:
    queue = _queues[chat_id]
    while True:
        status, job = await queue.get()
        _running[chat_id] = True
        try:
            await job(status)
        except Exception as exc:
            await _safe_edit(status, f"❌ Gagal: {html.escape(str(exc))}")
        finally:
            _running[chat_id] = False
            queue.task_done()


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _human_time(seconds: float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total} dtk"
    if total < 3600:
        return f"{total // 60} mnt {total % 60} dtk"
    return f"{total // 3600} j {(total % 3600) // 60} mnt"


MEDIA_TYPES = {
    ".mp4": "video", ".m4v": "video", ".mkv": "video", ".webm": "video",
    ".mov": "video", ".avi": "video", ".mpg": "video", ".mpeg": "video",
    ".ts": "video", ".flv": "video", ".wmv": "video", ".3gp": "video",
    ".mp3": "audio", ".flac": "audio", ".m4a": "audio", ".aac": "audio",
    ".ogg": "audio", ".oga": "audio", ".opus": "audio", ".wav": "audio",
    ".wma": "audio",
    ".jpg": "photo", ".jpeg": "photo", ".png": "photo", ".webp": "photo",
    ".gif": "animation",
}


def _probe(path: Path):
    """Durasi/dimensi/judul lewat ffprobe. None kalau gagal."""
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        streams = json.loads(result.stdout).get("streams", [])
    except Exception:
        return None

    meta = {}
    for stream in streams:
        kind = stream.get("codec_type")
        if kind == "video" and stream.get("width"):
            meta["width"] = int(stream["width"])
            meta["height"] = int(stream["height"])
        if kind in ("video", "audio") and stream.get("duration"):
            meta["duration"] = int(float(stream["duration"]))
        if kind == "audio":
            tags = stream.get("tags") or {}
            meta.setdefault("title", tags.get("title"))
            meta.setdefault("performer", tags.get("artist"))
    return meta or None


THUMB_MAX_SIDE = 320
THUMB_MAX_BYTES = 200 * 1024
WATERMARK_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


def _watermark_font(size: int):
    for name in WATERMARK_FONTS:
        if Path(name).exists():
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size)
    except Exception:
        return ImageFont.load_default()


def _watermark(path: Path) -> None:
    """Badge @handle di pojok kiri bawah thumbnail: rounded, semi-transparan."""
    text = config.watermark().strip()
    if not text:
        return
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
    except Exception:
        return

    width, height = image.size
    font = _watermark_font(max(11, round(width * 0.045)))
    probe = ImageDraw.Draw(image)
    box = probe.textbbox((0, 0), text, font=font)
    text_w, text_h = box[2] - box[0], box[3] - box[1]

    pad_x = round(font.size * 0.65)
    pad_y = round(font.size * 0.34)
    left = max(4, round(width * 0.025))
    bottom = height - max(4, round(height * 0.045))
    right = left + text_w + pad_x * 2
    top = bottom - text_h - pad_y * 2

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rounded_rectangle(
        [left, top, right, bottom], radius=(bottom - top) // 2, fill=(0, 0, 0, 150)
    )
    draw.text(
        (left + pad_x - box[0], top + pad_y - box[1]),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )

    result = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    for quality in (92, 85, 78, 70):
        result.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
        if path.stat().st_size <= THUMB_MAX_BYTES:
            return


def _thumb_time(meta) -> float:
    """Titik ambil cover: lewati intro, tapi jangan terlalu dalam.

    Pecahan 25% durasi, dibatasi 15 menit, supaya film panjang tidak
    mengambil frame dari tengah cerita. Video pendek pakai setengah durasi.
    """
    duration = (meta or {}).get("duration") or 0
    if duration <= 0:
        return 60.0
    if duration < 120:
        return duration / 2
    return min(duration * 0.25, 15 * 60)


def _clean_movie_title(filename: str) -> tuple[str, str | None]:
    stem = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", filename)
    stem = re.sub(r"(\.part\d+|\bpart\s*\d+\b)", "", stem, flags=re.IGNORECASE)
    name = re.sub(r"[._]", " ", stem)
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", name)
    year = year_match.group(1) if year_match else None

    clean_regex = re.compile(
        r"\b(s\d+e\d+|s\d+|season\s*\d+|episode\s*\d+|2160p|1080p|720p|480p|360p|4k|uhd|bluray|blu-ray|web-?dl|web-?rip|hdrip|dvdrip|hdtc|cam|telesync|remux|x264|x265|h\.?264|h\.?265|hevc|avc|10bit|aac\d*|ac3|ddp\d*(\.\d*)?|dts|atmos|yts(\.mx)?|pahe|rarbg|psa)\b",
        re.IGNORECASE,
    )
    if year_match:
        name = name[:year_match.start()]
    else:
        m = clean_regex.search(name)
        if m:
            name = name[:m.start()]

    name = re.sub(r"[\[\(\{].*?[\]\)\}]", "", name)
    name = clean_regex.sub("", name)
    name = re.sub(r"[^a-zA-Z0-9\s'-]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -'")
    return name, year


def _get_backdrop_url(title: str, year: str | None = None) -> str | None:
    if not title:
        return None
    # 1. Coba TMDB dengan API key resmi
    tmdb_key = getattr(config, "TMDB_API_KEY", "") or os.getenv("TMDB_API_KEY", "")
    if tmdb_key:
        for endpoint in ("movie", "multi"):
            try:
                params = f"api_key={tmdb_key}&query={urllib.parse.quote(title)}"
                if year and endpoint == "movie":
                    params += f"&year={year}"
                req = urllib.request.Request(
                    f"https://api.themoviedb.org/3/search/{endpoint}?{params}",
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                    results = data.get("results", [])
                    for item in results:
                        if item.get("backdrop_path"):
                            return f"https://image.tmdb.org/t/p/w1280{item['backdrop_path']}"
            except Exception:
                pass

    # 2. Coba Cinemeta (fallback gratis, tanpa API key)
    for cat in ("movie", "series"):
        query = urllib.parse.quote(f"{title} {year}" if year else title)
        url = f"https://v3-cinemeta.strem.io/catalog/{cat}/top/search={query}.json"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                metas = data.get("metas", [])
                if metas and metas[0].get("background"):
                    return metas[0]["background"]
        except Exception:
            pass
    return None


def _movie_backdrop_thumb(path: Path) -> Path | None:
    """Ambil poster landscape (backdrop) film dari database.
    Jika ketemu, resize ke format landscape Telegram (maks 320 px) + watermark.
    """
    title, year = _clean_movie_title(path.name)
    if not title or len(title) < 2:
        return None

    backdrop_url = _get_backdrop_url(title, year)
    if not backdrop_url:
        return None

    target = path.with_suffix(".thumb.jpg")
    try:
        req = urllib.request.Request(backdrop_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read()

        with Image.open(io.BytesIO(content)) as img:
            img = img.convert("RGB")
            orig_w, orig_h = img.size
            if orig_w <= 0 or orig_h <= 0:
                return None
            target_w = THUMB_MAX_SIDE
            target_h = max(1, round(THUMB_MAX_SIDE * (orig_h / orig_w)))
            resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
            resized.save(target, "JPEG", quality=90, optimize=True)

        _watermark(target)
        if target.exists() and 0 < target.stat().st_size <= THUMB_MAX_BYTES:
            return target
    except Exception:
        target.unlink(missing_ok=True)
    return None


def _thumb(path: Path, meta):
    """Ambil satu frame video sebagai cover JPEG. None kalau gagal.

    Telegram hanya menerima JPEG <=200 KB, sisi terpanjang <=320 px, jadi
    resolusi mengikuti batas itu dan tidak pernah di-upscale dari video kecil.
    """
    backdrop = _movie_backdrop_thumb(path)
    if backdrop:
        return backdrop

    if shutil.which("ffmpeg") is None:
        return None

    source = path.with_suffix(".thumb.png")
    target = path.with_suffix(".thumb.jpg")
    at = _thumb_time(meta)

    # Sesuaikan skala dengan resolusi asli video; jangan upscale.
    width = (meta or {}).get("width") or 0
    height = (meta or {}).get("height") or 0
    if width and height:
        factor = min(1.0, THUMB_MAX_SIDE / max(width, height))
        scale = f"scale={max(1, round(width * factor))}:{max(1, round(height * factor))}:flags=lanczos"
    else:
        scale = f"scale={THUMB_MAX_SIDE}:{THUMB_MAX_SIDE}:force_original_aspect_ratio=decrease:flags=lanczos"

    try:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-ss", f"{at:.2f}",
                "-i", str(path),
                # thumbnail memilih frame paling representatif dari 100 frame,
                # supaya cover tidak jatuh di frame hitam.
                "-vf", f"thumbnail=100,{scale}",
                "-frames:v", "1",
                str(source),
            ],
            capture_output=True,
            timeout=60,
        )
        if not source.exists() or source.stat().st_size == 0:
            return None
        _watermark(source)
        with Image.open(source) as image:
            image.convert("RGB").save(
                target, "JPEG", quality=92, optimize=True, progressive=True
            )
    except Exception:
        return None
    finally:
        source.unlink(missing_ok=True)

    if not target.exists() or not 0 < target.stat().st_size <= THUMB_MAX_BYTES:
        target.unlink(missing_ok=True)
        return None
    return target


async def _safe_edit(message: Message, text: str) -> None:
    try:
        await message.edit_text(text)
    except Exception:
        pass


class _Progress:
    """Pesan status berisi progress bar, kecepatan, dan ETA.

    update() dipanggil berkala dan membatasi edit Telegram tiap INTERVAL detik.
    """

    BAR_WIDTH = 20
    INTERVAL = 3.0

    def __init__(self, message: Message, label: str):
        self.message = message
        self.label = label
        self.last_edit = 0.0
        self.last_value = 0
        self.last_tick = time.monotonic()
        self.last_text = None

    def set_label(self, label: str) -> None:
        if label == self.label:
            return
        self.label = label
        # Fase baru: mulai hitung ulang dari nol supaya kecepatan tidak negatif.
        self.last_edit = 0.0
        self.last_value = 0
        self.last_tick = time.monotonic()

    async def update(self, current: int, total: int, detail: str = "") -> None:
        now = time.monotonic()
        if now - self.last_edit < self.INTERVAL:
            return
        elapsed = now - self.last_tick
        speed = (current - self.last_value) / elapsed if elapsed > 0 else 0
        self.last_edit = now
        self.last_value = current
        self.last_tick = now

        text = self._render(current, total, speed, detail)
        if text == self.last_text:
            return
        self.last_text = text
        await _safe_edit(self.message, text)

    async def finish(self, text: str) -> None:
        if text == self.last_text:
            return
        self.last_text = text
        await _safe_edit(self.message, text)

    def _render(self, current: int, total: int, speed: float, detail: str) -> str:
        if not total:
            return f"{self.label}\n{detail or 'memproses...'}"

        percent = min(100, int(current * 100 / total))
        filled = int(self.BAR_WIDTH * min(current, total) / total)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        eta = (total - current) / speed if speed > 0 else 0
        return (
            f"{self.label}\n"
            f"<code>[{bar}]</code> {percent}%\n"
            f"{_human(current)} / {_human(total)}\n"
            f"⚡ {_human(int(speed))}/s • ⏱ {_human_time(eta)}"
        )


async def _send_media(
    client: Client, message: Message, path: Path, kind: str, meta, thumb, base: dict
) -> None:
    if kind == "video":
        await client.send_video(
            video=str(path),
            file_name=path.name,
            supports_streaming=True,
            duration=meta and meta.get("duration"),
            width=meta and meta.get("width"),
            height=meta and meta.get("height"),
            thumb=str(thumb) if thumb else None,
            **base,
        )
    elif kind == "audio":
        await client.send_audio(
            audio=str(path),
            file_name=path.name,
            duration=meta and meta.get("duration"),
            performer=meta and meta.get("performer"),
            title=meta and meta.get("title"),
            **base,
        )
    elif kind == "photo":
        await client.send_photo(photo=str(path), **base)
    elif kind == "animation":
        await client.send_animation(
            animation=str(path),
            file_name=path.name,
            duration=meta and meta.get("duration"),
            width=meta and meta.get("width"),
            height=meta and meta.get("height"),
            thumb=str(thumb) if thumb else None,
            **base,
        )
    else:
        await client.send_document(
            document=str(path), file_name=path.name, force_document=True, **base
        )


def _split_video_sync(path: Path, max_bytes: int) -> list[Path]:
    """Bagi video menjadi beberapa part jika melebihi max_bytes via ffmpeg -c copy."""
    if shutil.which("ffmpeg") is None:
        return []

    size = path.stat().st_size
    chunk_limit = min(max_bytes, 1950 * 1024 * 1024)
    num_parts = math.ceil(size / chunk_limit)
    if num_parts <= 1:
        return [path]

    meta = _probe(path)
    duration = (meta or {}).get("duration", 0)
    if duration <= 0:
        return []

    segment_time = int(duration / num_parts) + 1
    split_dir = path.parent / f"_split_{path.stem}"
    split_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = str(split_dir / f"{path.stem}.part%02d{path.suffix}")

    try:
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-i", str(path),
                "-c", "copy",
                "-map", "0",
                "-segment_time", str(segment_time),
                "-f", "segment",
                "-reset_timestamps", "1",
                out_pattern,
            ],
            capture_output=True,
            timeout=600,
            check=True,
        )
        parts = sorted(split_dir.glob(f"{path.stem}.part*{path.suffix}"))
        if parts:
            return parts
    except Exception:
        shutil.rmtree(split_dir, ignore_errors=True)
    return []


async def _send_single(
    client: Client, message: Message, path: Path, progress: _Progress, label: str = ""
) -> None:
    size = path.stat().st_size
    disp_name = f"{label} {path.name}".strip() if label else path.name
    caption = f"<code>{html.escape(disp_name)}</code>\n{_human(size)}"
    kind = MEDIA_TYPES.get(path.suffix.lower(), "document")
    meta = _probe(path) if kind in ("video", "audio", "animation") else None
    thumb = _thumb(path, meta) if kind in ("video", "animation") else None
    base = {"chat_id": message.chat.id, "caption": caption, "progress": progress.update}

    try:
        await _send_media(client, message, path, kind, meta, thumb, base)
    except Exception:
        if kind == "document":
            raise
        if thumb and kind in ("video", "animation"):
            # Cover ditolak Telegram: ulangi tanpa cover, jangan turun ke dokumen.
            try:
                await _send_media(client, message, path, kind, meta, None, base)
                return
            except Exception:
                pass
        # Codec/container ditolak Telegram: kirim ulang sebagai dokumen.
        await client.send_document(
            chat_id=message.chat.id,
            document=str(path),
            caption=caption,
            file_name=path.name,
            force_document=True,
            progress=progress.update,
        )
    finally:
        if thumb:
            thumb.unlink(missing_ok=True)


async def _send_file(client: Client, message: Message, path: Path, progress: _Progress) -> None:
    size = path.stat().st_size
    max_bytes = config.MAX_UPLOAD_MB * 1024 * 1024
    kind = MEDIA_TYPES.get(path.suffix.lower(), "document")

    if size > max_bytes:
        if kind == "video":
            await progress.finish(f"✂️ File ({_human(size)}) > 2 GB. Memotong video...")
            loop = asyncio.get_running_loop()
            parts = await loop.run_in_executor(None, _split_video_sync, path, max_bytes)
            if parts and len(parts) > 1:
                try:
                    for idx, part in enumerate(parts, 1):
                        progress.set_label(f"📤 Mengunggah Bagian {idx}/{len(parts)}")
                        await _send_single(
                            client, message, part, progress, label=f"[Part {idx}/{len(parts)}]"
                        )
                    return
                finally:
                    split_dir = parts[0].parent
                    shutil.rmtree(split_dir, ignore_errors=True)
        await message.reply(f"❌ {html.escape(path.name)} terlalu besar ({_human(size)}).")
        return

    await _send_single(client, message, path, progress)


async def _deliver(
    client: Client, message: Message, paths: list, progress: _Progress, root: Path
) -> None:
    if len(paths) == 1:
        targets = paths
    else:
        # Banyak file: jadikan satu arsip supaya chat tidak dibanjiri pesan.
        await progress.finish(f"📦 Membuat arsip dari {len(paths)} file...")
        # Arsip dibuat di luar root, kalau tidak zip akan memuat dirinya sendiri.
        archive = root.parent / root.name
        shutil.make_archive(str(archive), "zip", root_dir=str(root), base_dir=".")
        targets = [Path(f"{archive}.zip")]

    for index, path in enumerate(targets, 1):
        progress.set_label(f"📤 Mengunggah {index}/{len(targets)}")
        await _send_file(client, message, path, progress)


async def _handle_gdrive(client: Client, message: Message, url: str) -> None:
    async def job(status):
        progress = _Progress(status, "⬇️ Mengunduh dari Google Drive")
        workdir = Path(tempfile.mkdtemp(prefix="gdrive_", dir=config.DOWNLOAD_DIR))
        try:
            kind = downloaders.parse_gdrive_url(url)
            state = {
                "current": 0,
                "total": 0,
                "detail": "mengunduh folder..." if kind and kind[0] == "folder"
                else "menghubungi Drive...",
            }

            def on_progress(current, total):
                state["current"] = current or 0
                state["total"] = total or 0

            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(
                None, downloaders.download_gdrive, url, workdir, on_progress
            )
            while not task.done():
                await asyncio.sleep(1)
                await progress.update(state["current"], state["total"], state["detail"])

            paths = task.result()
            progress.set_label(f"📤 Mengunggah {len(paths)} file")
            await _deliver(client, message, paths, progress, workdir)
            await progress.finish(f"✅ Selesai: {len(paths)} file terkirim")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    await _enqueue(message.chat.id, message, "Google Drive", job)


async def _handle_torrent(client: Client, message: Message, source: str) -> None:
    async def job(status):
        progress = _Progress(status, "⬇️ Mengunduh torrent")
        workdir = Path(tempfile.mkdtemp(prefix="torrent_", dir=config.DOWNLOAD_DIR))
        try:
            state = {"done": 0, "total": 0, "detail": "mencari metadata..."}

            def on_progress(done, total, detail):
                state.update(done=done, total=total, detail=detail)

            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(
                None, downloaders.download_torrent, source, workdir, on_progress
            )
            while not task.done():
                await asyncio.sleep(1)
                await progress.update(state["done"], state["total"], state["detail"])

            paths = task.result()
            progress.set_label(f"📤 Mengunggah {len(paths)} file")
            await _deliver(client, message, paths, progress, workdir)
            await progress.finish(f"✅ Selesai: {len(paths)} file terkirim")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    await _enqueue(message.chat.id, message, "torrent", job)


async def _handle_direct_download(
    client: Client, message: Message, name: str, download_fn
) -> None:
    async def job(status):
        progress = _Progress(status, f"⬇️ Mengunduh dari {name}")
        prefix = f"{name.lower().replace(' ', '_')}_"
        workdir = Path(tempfile.mkdtemp(prefix=prefix, dir=config.DOWNLOAD_DIR))
        try:
            state = {"current": 0, "total": 0, "detail": "menghubungi server..."}

            def on_progress(current, total, detail="mengunduh..."):
                state.update(current=current or 0, total=total or 0, detail=detail)

            loop = asyncio.get_running_loop()
            task = loop.run_in_executor(None, download_fn, workdir, on_progress)
            while not task.done():
                await asyncio.sleep(1)
                await progress.update(state["current"], state["total"], state["detail"])

            result = task.result()
            paths = result if isinstance(result, list) else [result]
            progress.set_label(f"📤 Mengunggah {len(paths)} file")
            await _deliver(client, message, paths, progress, workdir)
            await progress.finish(f"✅ Selesai: {len(paths)} file terkirim")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    await _enqueue(message.chat.id, message, name, job)


app = Client(
    "projectTorrent",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    parse_mode=enums.ParseMode.HTML,
)


async def _reupload_media(client: Client, message: Message, raw_name: str) -> None:
    """Dokumen media (video dikirim sebagai file) diunggah ulang sebagai media."""
    async def job(status):
        progress = _Progress(status, "⬇️ Mengunduh file")
        workdir = Path(tempfile.mkdtemp(prefix="media_", dir=config.DOWNLOAD_DIR))
        try:
            safe_name = Path(raw_name).name or f"{message.id}{Path(raw_name).suffix}"
            path = Path(
                await message.download(
                    file_name=str(workdir / safe_name), progress=progress.update
                )
            )
            progress.set_label("📤 Mengunggah sebagai media")
            await _send_file(client, message, path, progress)
            await progress.finish("✅ Terkirim sebagai media")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    await _enqueue(message.chat.id, message, "unggah ulang media", job)


@app.on_message(filters.command(["start", "menu", "help"]))
async def _start(client: Client, message: Message) -> None:
    await message.reply_text(
        MENU_TEXT, reply_markup=_menu_keyboard(), disable_web_page_preview=True
    )


@app.on_message(filters.command(["status"]))
async def _status(client: Client, message: Message) -> None:
    await message.reply_text(
        _status_panel(message.chat.id),
        reply_markup=_status_keyboard(),
        disable_web_page_preview=True,
    )


@app.on_message(filters.command(["watermark"]))
async def _watermark_cmd(client: Client, message: Message) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        await message.reply_text(
            _watermark_panel(),
            reply_markup=_watermark_keyboard(),
            disable_web_page_preview=True,
        )
        return

    value = parts[1].strip()
    if value.lower() in {"reset", "default", "hapus"}:
        config.set_watermark(config.WATERMARK_TEXT)
        await message.reply_text(
            f"♻️ Watermark kembali ke <code>{html.escape(config.WATERMARK_TEXT)}</code>."
        )
    elif not 1 <= len(value) <= 40:
        await message.reply_text("❌ Watermark harus 1-40 karakter.")
    else:
        config.set_watermark(value)
        await message.reply_text(
            f"✅ Watermark diganti: <code>{html.escape(value)}</code>\n"
            "Berlaku untuk thumbnail berikutnya."
        )


@app.on_message(filters.command(["batal", "cancel"]))
async def _cancel(client: Client, message: Message) -> None:
    was_waiting = message.chat.id in _pending_watermark
    _pending_watermark.discard(message.chat.id)
    await message.reply_text(
        "Dibatalkan." if was_waiting else "Tidak ada yang perlu dibatalkan."
    )


@app.on_callback_query(filters.regex(r"^(menu|wm):"))
async def _on_callback(client: Client, query: CallbackQuery) -> None:
    chat_id = query.message.chat.id
    panels = {
        "menu:main": (MENU_TEXT, _menu_keyboard()),
        "menu:help": (HELP_TEXT, _back_keyboard()),
        "menu:limits": (LIMITS_TEXT, _back_keyboard()),
        "menu:wm": (_watermark_panel(), _watermark_keyboard()),
        "menu:status": (_status_panel(chat_id), _status_keyboard()),
    }

    try:
        if query.data in panels:
            text, keyboard = panels[query.data]
            await query.message.edit_text(
                text, reply_markup=keyboard, disable_web_page_preview=True
            )
        elif query.data == "wm:edit":
            _pending_watermark.add(chat_id)
            await query.message.reply_text(
                "✏️ Kirim teks watermark baru (1-40 karakter), "
                "misalnya <code>@film_indonesia1</code>.\n"
                "Kirim /batal untuk membatalkan."
            )
        elif query.data == "wm:reset":
            config.set_watermark(config.WATERMARK_TEXT)
            _pending_watermark.discard(chat_id)
            await query.message.edit_text(
                _watermark_panel(),
                reply_markup=_watermark_keyboard(),
                disable_web_page_preview=True,
            )
    except MessageNotModified:
        pass
    finally:
        await query.answer()


@app.on_message(filters.document)
async def _on_document(client: Client, message: Message) -> None:
    raw_name = message.document.file_name or ""
    name = raw_name.lower()

    if name.endswith(".torrent"):
        meta_dir = config.DOWNLOAD_DIR / "meta"
        path = Path(await message.download(file_name=str(meta_dir / f"{message.id}.torrent")))
        await _handle_torrent(client, message, str(path))
        path.unlink(missing_ok=True)
        return

    if Path(name).suffix not in MEDIA_TYPES:
        await message.reply(
            "Kirim link Google Drive publik, link magnet, file .torrent, "
            "atau file media (video/audio/gambar/GIF).\n"
            "File media dikirim ulang sebagai media ber-watermark.\n"
            "Ketik /menu untuk daftar fitur."
        )
        return

    size = message.document.file_size or 0
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        await message.reply(f"❌ {html.escape(raw_name)} terlalu besar ({_human(size)}).")
        return

    await _reupload_media(client, message, raw_name)


@app.on_message(filters.video)
async def _on_video(client: Client, message: Message) -> None:
    raw_name = message.video.file_name or ""
    if not raw_name and message.caption:
        raw_name = message.caption.splitlines()[0].strip()
    if not raw_name:
        raw_name = f"video_{message.id}.mp4"
    if not Path(raw_name).suffix:
        raw_name += ".mp4"

    size = message.video.file_size or 0
    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
        await message.reply(f"❌ {html.escape(raw_name)} terlalu besar ({_human(size)}).")
        return

    await _reupload_media(client, message, raw_name)


@app.on_message(filters.text & ~filters.regex(r"^/"))
async def _on_text(client: Client, message: Message) -> None:
    text = message.text.strip()
    chat_id = message.chat.id

    # Menunggu teks watermark, kecuali user malah mengirim link.
    if chat_id in _pending_watermark and not _is_link(text):
        _pending_watermark.discard(chat_id)
        if not 1 <= len(text) <= 40:
            await message.reply_text(
                "❌ Watermark harus 1-40 karakter. Coba lagi lewat /watermark."
            )
            return
        config.set_watermark(text)
        await message.reply_text(
            f"✅ Watermark diganti: <code>{html.escape(text)}</code>\n"
            "Berlaku untuk thumbnail berikutnya."
        )
        return

    lower = text.lower()
    if "drive.google.com" in lower:
        await _handle_gdrive(client, message, text)
    elif text.startswith("magnet:"):
        await _handle_torrent(client, message, text)
    elif "mediafire.com" in lower:
        await _handle_direct_download(
            client,
            message,
            "MediaFire",
            lambda w, cb: downloaders.download_url(
                *downloaders.resolve_mediafire(text), dest_dir=w, progress_cb=cb
            ),
        )
    elif "pixeldrain.com" in lower:
        await _handle_direct_download(
            client,
            message,
            "Pixeldrain",
            lambda w, cb: downloaders.download_url(
                *downloaders.resolve_pixeldrain(text), dest_dir=w, progress_cb=cb
            ),
        )
    elif "gofile.io" in lower:
        await _handle_direct_download(
            client,
            message,
            "GoFile",
            lambda w, cb: [
                downloaders.download_url(
                    u, w, filename=n, headers=h, progress_cb=cb
                )
                for u, n, h in downloaders.resolve_gofile(text)
            ],
        )
    elif any(
        d in lower
        for d in (
            "terabox",
            "1024tera",
            "teraboxapp",
            "mirrobox",
            "4funbox",
            "nephobox",
        )
    ):
        await _handle_direct_download(
            client,
            message,
            "TeraBox",
            lambda w, cb: downloaders.download_url(
                *downloaders.resolve_terabox(text), dest_dir=w, progress_cb=cb
            ),
        )
    elif any(
        d in lower
        for d in (
            "youtube.com",
            "youtu.be",
            "tiktok.com",
            "instagram.com",
            "twitter.com",
            "x.com",
            "facebook.com",
            "fb.watch",
        )
    ):
        await _handle_direct_download(
            client,
            message,
            "Video",
            lambda w, cb: downloaders.download_ytdlp(text, w, progress_cb=cb),
        )
    elif text.startswith(("http://", "https://")):
        await _handle_direct_download(
            client,
            message,
            "Direct Link",
            lambda w, cb: downloaders.download_url(text, w, progress_cb=cb),
        )
    else:
        await message.reply_text(
            MENU_TEXT, reply_markup=_menu_keyboard(), disable_web_page_preview=True
        )


def _check_config() -> None:
    missing = [
        name
        for name, value in (
            ("API_ID", config.API_ID),
            ("API_HASH", config.API_HASH),
            ("BOT_TOKEN", config.BOT_TOKEN),
        )
        if not value
    ]
    if missing:
        raise SystemExit(f"Env belum diisi: {', '.join(missing)}. Lihat .env.example")


def _clean_stale_downloads() -> None:
    """Bersihkan file sisa unduhan lama jika bot sempat terhenti mendadak."""
    if config.DOWNLOAD_DIR.exists():
        for item in config.DOWNLOAD_DIR.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    _check_config()
    _clean_stale_downloads()
    app.run()
