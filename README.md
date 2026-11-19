# projectTorrent

Bot Telegram yang mengunduh file dari **link Google Drive publik** atau **torrent**
(magnet / file `.torrent`), lalu mengirim file-nya ke chat.

Python + [Pyrogram](https://docs.pyrogram.org/) (MTProto, jadi unggah sampai 2 GB),
[gdown](https://github.com/wkentaro/gdown) untuk Drive, dan
[libtorrent](https://libtorrent.org/) untuk torrent.

## Fitur

- Link Google Drive publik, file atau folder.
- Link `magnet:?xt=...` dan file `.torrent`.
- Pesan status berisi progress bar, persen, ukuran, **kecepatan unduh/unggah**,
  dan ETA; di-edit berkala dan bedakan fase unduh, arsip, dan unggah.
- Video, audio, gambar, dan GIF dikirim sebagai **media** (bukan dokumen);
  format lain tetap dokumen. Codec yang ditolak Telegram otomatis dikirim ulang
  sebagai dokumen.
- File media yang dikirim sebagai **dokumen** (mis. video `.mkv` di-forward
  sebagai file) diunduh bot lalu diunggah ulang sebagai media, lengkap dengan
  cover ber-watermark.
- Video dan GIF mendapat **cover JPEG** dari frame paling representatif di 25%
  durasi (maksimal menit ke-15, jadi tidak kena intro), di-resize dari resolusi
  asli ke maks 320 px (tanpa upscale), dengan **watermark** di pojok kiri bawah
  (default `@film_indonesia1`).
  Watermark bisa diganti dari bot lewat menu 🖌 atau `/watermark`, disimpan di
  `settings.json`; isi kosong untuk mematikan.
- Menu tombol `/start`: cara pakai, watermark, status, dan batas format.
- Antrian FIFO per chat: kiriman berikutnya menunggu dengan label
  `🕒 Antrian #n`, bukan ditolak.
- Torrent banyak file dijadikan satu arsip `.zip`.
- File sementara dihapus dari disk setelah terkirim.

## Setup

```bash
cd ~/projectTorrent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Isi `.env`:

| Variabel | Dari mana |
|---|---|
| `API_ID`, `API_HASH` | https://my.telegram.org → API development tools |
| `BOT_TOKEN` | @BotFather → `/newbot` |

Jalankan (disarankan, jalan di latar belakang):

```bash
cd ~/projectTorrent
./server.sh start
```

| Perintah | Fungsi |
|---|---|
| `./server.sh start` | Start bot di latar belakang |
| `./server.sh stop` | Hentikan bot |
| `./server.sh restart` | Restart (aman dari lock SQLite) |
| `./server.sh status` | Cek jalan/mati + PID |
| `./server.sh logs` | Ikuti log langsung (Ctrl+C keluar) |
| `./server.sh logs 100` | Tampilkan 100 baris terakhir lalu ikuti |

`restart.sh` tetap ada dan sekarang sama dengan `./server.sh restart`.

Biar makin singkat, tambahkan alias di `~/.bashrc`:

```bash
echo "alias bot='~/projectTorrent/server.sh'" >> ~/.bashrc
source ~/.bashrc
```

Setelah itu cukup `bot start`, `bot status`, `bot logs`.

Jalan di depan (debug, log muncul di terminal):

```bash
.venv/bin/python bot.py
```

## Pakai

Kirim ke bot (private chat):

- `https://drive.google.com/file/d/..../view?usp=sharing`
- `https://drive.google.com/drive/folders/....`
- `magnet:?xt=urn:btih:...`
- file `.torrent`

Bot mengunduh ke `downloads/`, mengirim file-nya, lalu menghapus unduhannya.

## Menu & perintah

| Perintah | Fungsi |
|---|---|
| `/start`, `/menu`, `/help` | Menu utama dengan tombol |
| `/status` | Status tugas di chat ini + watermark aktif |
| `/watermark` | Lihat / ganti watermark (tombol Ganti & Reset) |
| `/watermark @nama` | Ganti watermark langsung |
| `/watermark reset` | Kembali ke nilai default `.env` |
| `/batal` | Batalkan mode ganti watermark |

## Batasan

- Link Drive harus publik ("anyone with the link").
- Ukuran unggah maksimum `MAX_UPLOAD_MB` (default 2000 MB, batas MTProto).
- Torrent butuh peer/tracker yang reachable; port `TORRENT_PORT` (default 6881)
  dipakai untuk DHT/LSD/UPnP/NAT-PMP. Kalau port diblokir, torrent tetap bisa
  jalan lewat tracker HTTP, tapi magnet tanpa tracker bisa gagal.
- File >20 MB hanya bisa terkirim karena memakai MTProto, bukan Bot API.
  Bot API maksimum 20 MB per unggahan.

## Struktur

| File | Isi |
|---|---|
| `bot.py` | Handler Telegram, progress, unggah file |
| `downloaders.py` | Unduh Drive (gdown) dan torrent (libtorrent) |
| `config.py` | Baca `.env`, simpan watermark |
