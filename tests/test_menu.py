"""Tes menu, penyimpanan watermark, dan deteksi link.

Jalankan: .venv/bin/python tests/test_menu.py
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot
import config


def test_default_watermark():
    original = config.SETTINGS_FILE
    config.SETTINGS_FILE = Path(tempfile.mkdtemp()) / "settings.json"
    try:
        assert config.WATERMARK_TEXT == "@film_indonesia1", config.WATERMARK_TEXT
        assert config.watermark() == "@film_indonesia1", config.watermark()

        config.set_watermark("@baru_tes")
        assert config.watermark() == "@baru_tes"
        assert "watermark" in config.SETTINGS_FILE.read_text()

        config.set_watermark(config.WATERMARK_TEXT)
        assert config.watermark() == "@film_indonesia1"
    finally:
        config.SETTINGS_FILE = original
    print("watermark tersimpan & reset -> OK")


def test_is_link():
    assert bot._is_link("https://drive.google.com/file/d/abc/view")
    assert bot._is_link("magnet:?xt=urn:btih:abc")
    assert bot._is_link("http://contoh.test/file.zip")
    assert not bot._is_link("@film_indonesia1")
    assert not bot._is_link("film indonesia")
    print("deteksi link -> OK")


class FakeStatus:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text):
        self.texts.append(text)


class FakeMessage:
    def __init__(self, chat_id=77):
        self.chat = type("Chat", (), {"id": chat_id})()
        self.replies = []

    async def reply(self, text):
        self.replies.append(text)
        return FakeStatus()


def test_queue_fifo():
    async def scenario():
        order = []
        chat_id = 987654

        async def make_job(name):
            async def job(status):
                await asyncio.sleep(0.05)
                order.append(name)

            return job

        first = FakeMessage(chat_id)
        await bot._enqueue(chat_id, first, "satu", await make_job("satu"))
        second = FakeMessage(chat_id)
        await bot._enqueue(chat_id, second, "dua", await make_job("dua"))
        await bot._queues[chat_id].join()
        bot._queue_workers.pop(chat_id).cancel()
        return first, second, order

    first, second, order = asyncio.run(scenario())
    assert order == ["satu", "dua"], order
    assert "Antrian #2" in second.replies[0], second.replies
    assert "Menyiapkan" in first.replies[0], first.replies
    print("antrian FIFO ->", order)


def test_panels():
    assert "Watermark thumbnail" in bot._watermark_panel()
    assert "Status chat ini" in bot._status_panel(123)
    assert bot._menu_keyboard().inline_keyboard
    print("panel menu -> OK")


def main():
    test_default_watermark()
    test_is_link()
    test_queue_fifo()
    test_panels()
    print("ALL MENU TESTS PASSED")


if __name__ == "__main__":
    main()
