"""Tes ringan untuk parser link Drive dan round-trip torrent lokal.

Jalankan: .venv/bin/python tests/test_downloaders.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import libtorrent as lt

import downloaders

SIZE = 2 * 1024 * 1024
SEED_PORT = 6891


def make_torrent():
    src = tempfile.mkdtemp(prefix="seed_")
    name = "hello.bin"
    with open(os.path.join(src, name), "wb") as handle:
        handle.write(os.urandom(SIZE))
    files = lt.file_storage()
    lt.add_files(files, os.path.join(src, name))
    creator = lt.create_torrent(files)
    lt.set_piece_hashes(creator, src)
    torrent_path = os.path.join(src, "hello.torrent")
    with open(torrent_path, "wb") as handle:
        handle.write(lt.bencode(creator.generate()))
    return src, torrent_path, name


def start_seeder(src, torrent_path):
    seeder = lt.session({"listen_interfaces": f"0.0.0.0:{SEED_PORT}"})
    seeder.start_dht()
    params = lt.add_torrent_params()
    params.ti = lt.torrent_info(torrent_path)
    params.save_path = src
    params.flags |= lt.torrent_flags.seed_mode
    seeder.add_torrent(params)
    time.sleep(1)
    return seeder, seeder.listen_port()


def test_parse_gdrive_url():
    cases = {
        "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456/view?usp=sharing":
            ("file", "1AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz123456":
            ("file", "1AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        "https://drive.google.com/uc?id=1AbCdEfGhIjKlMnOpQrStUvWxYz123456&export=download":
            ("file", "1AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz123456?usp=sharing":
            ("folder", "1AbCdEfGhIjKlMnOpQrStUvWxYz123456"),
        "https://example.com/nope": None,
    }
    for url, expected in cases.items():
        assert downloaders.parse_gdrive_url(url) == expected, url


def test_torrent_file_roundtrip():
    src, torrent_path, name = make_torrent()
    _, port = start_seeder(src, torrent_path)

    real_session = downloaders.lt.session

    def session_factory(*args, **kwargs):
        session = real_session(*args, **kwargs)
        real_add = session.add_torrent

        def add(params, *inner_args, **inner_kwargs):
            if isinstance(params, dict):
                params["peers"] = [("127.0.0.1", port)]
            return real_add(params, *inner_args, **inner_kwargs)

        session.add_torrent = add
        return session

    downloaders.lt.session = session_factory
    try:
        dest = tempfile.mkdtemp(prefix="dl_")
        paths = downloaders.download_torrent(torrent_path, dest)
    finally:
        downloaders.lt.session = real_session

    assert [path.name for path in paths] == [name]
    assert paths[0].stat().st_size == SIZE


def test_magnet_roundtrip():
    src, torrent_path, name = make_torrent()
    _, port = start_seeder(src, torrent_path)
    info = lt.torrent_info(torrent_path)
    magnet = f"magnet:?xt=urn:btih:{info.info_hash().to_bytes().hex()}&dn={name}"

    real_add_magnet = downloaders.lt.add_magnet_uri

    def add_magnet_with_peer(session, uri, params):
        params["peers"] = [("127.0.0.1", port)]
        return real_add_magnet(session, uri, params)

    downloaders.lt.add_magnet_uri = add_magnet_with_peer
    try:
        dest = tempfile.mkdtemp(prefix="dl_")
        paths = downloaders.download_torrent(magnet, dest)
    finally:
        downloaders.lt.add_magnet_uri = real_add_magnet

    assert [path.name for path in paths] == [name]
    assert paths[0].stat().st_size == SIZE


def main():
    test_parse_gdrive_url()
    print("parse_gdrive_url OK")
    test_torrent_file_roundtrip()
    print("torrent file roundtrip OK")
    test_magnet_roundtrip()
    print("magnet roundtrip OK")
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
