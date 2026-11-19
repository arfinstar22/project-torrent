"""Tes pemilihan tipe media saat unggah.

Jalankan: .venv/bin/python tests/test_media.py
"""
import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
from PIL import Image

FAKE_MEDIA = {
    "video.mp4": b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 100,
    "audio.mp3": b"ID3\x03\x00\x00\x00" + b"\x00" * 100,
    "photo.jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 100,
    "anim.gif": b"GIF89a" + b"\x00" * 100,
    "pack.zip": b"PK\x03\x04" + b"\x00" * 100,
}


class FakeClient:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def _call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.fail == name:
            raise RuntimeError("ditolak Telegram")

    async def send_video(self, **kw):
        await self._call("video", **kw)

    async def send_audio(self, **kw):
        await self._call("audio", **kw)

    async def send_photo(self, **kw):
        await self._call("photo", **kw)

    async def send_animation(self, **kw):
        await self._call("animation", **kw)

    async def send_document(self, **kw):
        await self._call("document", **kw)


class FakeChat:
    id = 123


class FakeMessage:
    chat = FakeChat()

    async def reply(self, text):
        pass


def run(name, fail=None):
    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(FAKE_MEDIA[name])
    client = FakeClient(fail=fail)
    message = FakeMessage()
    asyncio.run(bot._send_file(client, message, path, bot._Progress(message, "📤 Mengunggah")))
    return client


def make_real_video() -> Path:
    path = Path(tempfile.mkdtemp()) / "real.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", "testsrc=duration=3:size=640x360:rate=15",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_thumb():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("thumb -> dilewati (ffmpeg tidak ada)")
        return

    path = make_real_video()
    thumb = bot._thumb(path, bot._probe(path))
    assert thumb is not None and thumb.exists(), "thumb tidak dibuat"
    assert thumb.stat().st_size <= bot.THUMB_MAX_BYTES, thumb.stat().st_size
    assert thumb.suffix == ".jpg"

    client = FakeClient()
    message = FakeMessage()
    asyncio.run(
        bot._send_file(client, message, path, bot._Progress(message, "📤 Mengunggah"))
    )
    sent = client.calls[0][1]
    thumb_path = Path(sent["thumb"] or "")
    assert thumb_path.suffix == ".jpg", sent.get("thumb")
    assert not thumb_path.exists(), "thumb tidak dibersihkan"
    print("thumb ->", thumb_path.name, "dipakai lalu dihapus")

    fake = Path(tempfile.mkdtemp()) / "fake.mp4"
    fake.write_bytes(FAKE_MEDIA["video.mp4"])
    assert bot._thumb(fake, None) is None
    print("thumb file rusak -> None")


def test_thumb_time():
    cases = [
        ({"duration": 7200}, 900),
        ({"duration": 1800}, 450),
        ({"duration": 600}, 150),
        ({"duration": 90}, 45),
        ({"duration": 10}, 5),
        ({}, 60),
    ]
    got = [bot._thumb_time(meta) for meta, _ in cases]
    want = [value for _, value in cases]
    assert got == want, got
    print("thumb time ->", " ".join(f"{g:g}" for g in got), "(durasi 7200/1800/600/90/10/tanpa)")


def test_thumb_lewati_intro():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("lewati intro -> dilewati (ffmpeg tidak ada)")
        return

    path = Path(tempfile.mkdtemp()) / "film.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", "testsrc=duration=120:size=640x360:rate=15",
            "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(t,25)'",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    thumb = bot._thumb(path, bot._probe(path))
    result = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(thumb), "-vf",
         "signalstats,metadata=print:file=-", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    out = result.stdout + result.stderr
    yavg = float(out.split("lavfi.signalstats.YAVG=")[1].split()[0])
    assert yavg > 80, yavg
    print("lewati intro -> YAVG", yavg, "(intro hitam 25 detik)")
    thumb.unlink()


def test_watermark():
    original = bot.config.SETTINGS_FILE
    bot.config.SETTINGS_FILE = Path(tempfile.mkdtemp()) / "settings.json"
    try:
        assert bot.config.watermark() == "@film_indonesia1"

        path = Path(tempfile.mkdtemp()) / "plain.jpg"
        Image.new("RGB", (320, 180), (128, 128, 128)).save(path, "JPEG", quality=95)
        bot._watermark(path)

        with Image.open(path) as image:
            assert image.size == (320, 180), image.size
            rgb = image.convert("RGB")
            dark = sum(
                1
                for x in range(4, 170)
                for y in range(130, 180)
                if sum(rgb.getpixel((x, y))) < 300
            )
            light = sum(
                1
                for x in range(4, 170)
                for y in range(130, 180)
                if min(rgb.getpixel((x, y))) > 200
            )
        assert dark > 200, dark
        assert light > 50, light
        print("watermark -> badge", dark, "px gelap, teks", light, "px putih")
    finally:
        bot.config.SETTINGS_FILE = original


def test_thumb_watermark():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("thumb watermark -> dilewati (ffmpeg tidak ada)")
        return

    path = Path(tempfile.mkdtemp()) / "gray.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", "color=c=gray:size=640x360:duration=3:rate=15",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )
    thumb = bot._thumb(path, bot._probe(path))
    with Image.open(thumb) as image:
        rgb = image.convert("RGB")
        dark = sum(
            1
            for x in range(4, 170)
            for y in range(130, 180)
            if sum(rgb.getpixel((x, y))) < 300
        )
    assert dark > 200, dark
    print("thumb watermark -> badge ada di pojok kiri bawah", dark, "px")
    thumb.unlink()


class RejectThumb(FakeClient):
    async def _call(self, name, **kwargs):
        if kwargs.get("thumb"):
            raise RuntimeError("THUMB_INVALID")
        await super()._call(name, **kwargs)


def test_thumb_rejected():
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("thumb ditolak -> dilewati (ffmpeg tidak ada)")
        return

    path = make_real_video()
    client = RejectThumb()
    message = FakeMessage()
    asyncio.run(
        bot._send_file(client, message, path, bot._Progress(message, "📤 Mengunggah"))
    )
    calls = [(name, bool(kwargs.get("thumb"))) for name, kwargs in client.calls]
    # Panggilan pertama (dengan cover) ditolak sebelum sempat tercatat;
    # yang tercatat adalah percobaan ulang tanpa cover, dan bukan dokumen.
    assert calls == [("video", False)], calls
    print("thumb ditolak -> kirim ulang tanpa cover, tetap video")


class FakeDocument:
    def __init__(self, name, size):
        self.file_name = name
        self.file_size = size


class FakeChat:
    id = 42


class FakeDocMessage:
    id = 7
    chat = FakeChat()

    def __init__(self, name, size=1000, payload=b""):
        self.document = FakeDocument(name, size)
        self.payload = payload
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)
        return FakeStatus()

    async def download(self, file_name=None, progress=None):
        path = Path(file_name)
        path.write_bytes(self.payload)
        return str(path)


class FakeStatus:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text):
        self.texts.append(text)


def test_document_media():
    sent = []
    original = bot._send_file

    async def fake_send(client, message, path, progress):
        sent.append(Path(path).name)

    async def scenario():
        media = FakeDocMessage("film.mkv", payload=FAKE_MEDIA["video.mp4"])
        await bot._on_document(object(), media)
        await bot._queues[media.chat.id].join()

        archive = FakeDocMessage("arsip.zip", payload=b"PK\x03\x04")
        await bot._on_document(object(), archive)

        bot._queue_workers.pop(media.chat.id).cancel()
        return media, archive

    bot._send_file = fake_send
    try:
        media, archive = asyncio.run(scenario())
    finally:
        bot._send_file = original

    assert sent == ["film.mkv"], sent
    assert archive.replies and "File media" in archive.replies[0], archive.replies
    print("dokumen media -> dikirim ulang sebagai media; .zip -> ditolak")


def main():
    expected = {
        "video.mp4": "video",
        "audio.mp3": "audio",
        "photo.jpg": "photo",
        "anim.gif": "animation",
        "pack.zip": "document",
    }
    for name, want in expected.items():
        client = run(name)
        got = client.calls[0][0]
        assert got == want, f"{name}: {got} != {want}"
        print(name, "->", got)

    client = run("video.mp4", fail="video")
    assert [call[0] for call in client.calls] == ["video", "document"], client.calls
    print("fallback video ->", [call[0] for call in client.calls])

    test_thumb()
    test_thumb_time()
    test_thumb_lewati_intro()
    test_watermark()
    test_thumb_watermark()
    test_thumb_rejected()
    test_document_media()

    print("ALL MEDIA TESTS PASSED")


if __name__ == "__main__":
    main()
