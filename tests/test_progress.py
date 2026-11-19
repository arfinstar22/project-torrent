"""Tes tampilan progress bar.

Jalankan: .venv/bin/python tests/test_progress.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bot


class FakeMessage:
    def __init__(self):
        self.texts = []

    async def edit_text(self, text):
        self.texts.append(text)


def test_human_time():
    assert bot._human_time(5) == "5 dtk"
    assert bot._human_time(65) == "1 mnt 5 dtk"
    assert bot._human_time(3725) == "1 j 2 mnt"


def test_render_without_total():
    message = FakeMessage()
    progress = bot._Progress(message, "⬇️ Mengunduh torrent")
    bot._Progress.INTERVAL = 0
    asyncio.run(progress.update(0, 0, "mencari metadata..."))
    text = message.texts[-1]
    assert "mencari metadata..." in text
    assert "█" not in text


def test_render_with_total():
    message = FakeMessage()
    progress = bot._Progress(message, "⬇️ Mengunduh")
    bot._Progress.INTERVAL = 0
    asyncio.run(progress.update(50 * 1024 * 1024, 100 * 1024 * 1024))

    text = message.texts[-1]
    assert "⬇️ Mengunduh" in text
    assert "█" * 10 + "░" * 10 in text
    assert "50%" in text
    assert "50.0 MB / 100.0 MB" in text


def test_throttle():
    message = FakeMessage()
    bot._Progress.INTERVAL = 60
    progress = bot._Progress(message, "⬇️ Mengunduh")
    asyncio.run(progress.update(1, 10))
    asyncio.run(progress.update(9, 10))
    assert len(message.texts) == 1, message.texts
    bot._Progress.INTERVAL = 3.0


def main():
    test_human_time()
    print("human_time OK")
    test_render_without_total()
    print("render tanpa total OK")
    test_render_with_total()
    print("render dengan total OK")
    test_throttle()
    print("throttle OK")
    print("ALL PROGRESS TESTS PASSED")


if __name__ == "__main__":
    main()
