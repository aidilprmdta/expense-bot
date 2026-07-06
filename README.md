# 🤖 Telegram Expense Tracker Bot

Bot Telegram pintar untuk mencatat pengeluaran harian secara otomatis ke Google Sheets. Dilengkapi dengan kecerdasan buatan (AI) dari Groq untuk memproses input teks natural dan mengekstrak data dari gambar struk belanja (Vision/OCR).

## ✨ Fitur Utama

* **Natural Language Processing:** Catat pengeluaran hanya dengan mengetik bahasa sehari-hari (contoh: "Tadi beli makan siang 25 ribu"). AI akan otomatis mendeteksi nominal dan kategori.
* **Receipt Scanner (OCR):** Cukup kirimkan foto struk belanja, bot akan membaca total pengeluaran dan item yang dibeli.
* **Google Sheets Integration:** Semua data pengeluaran langsung tersimpan dan terorganisir rapi di Spreadsheet pribadi Anda.
* **Always-On (Termux Ready):** Dapat dijalankan 24/7 di server lokal atau perangkat Android menggunakan Termux + Tmux.

## 🛠️ Teknologi yang Digunakan

* [Python 3](https://www.python.org/)
* [Python Telegram Bot](https://python-telegram-bot.org/) - Framework API Telegram
* [Groq API](https://groq.com/) - LLM untuk pemrosesan teks dan gambar
* [Gspread](https://docs.gspread.org/) - Integrasi Google Sheets API
* [Pillow (PIL)](https://python-pillow.org/) - Pemrosesan gambar struk

## ⚙️ Persyaratan (Prerequisites)

Sebelum menjalankan bot ini, pastikan Anda telah menyiapkan:
1. **Token Bot Telegram** (Dapatkan dari [@BotFather](https://t.me/BotFather) di Telegram).
2. **Groq API Key** (Dapatkan dari Groq Console).
3. **Google Service Account Credentials** (`credentials.json`) dengan akses ke Google Sheets dan Google Drive API.
4. Sebuah **Google Spreadsheet** (Jangan lupa *Share* akses edit ke email *Service Account* Anda).

## 🚀 Cara Instalasi

1. **Clone repositori ini:**
   ```bash
   git clone [https://github.com/aidilprmdta/expense-bot.git](https://github.com/aidilprmdta/expense-bot.git)
   cd expense-bot

```

2. **Buat Virtual Environment (Opsional namun disarankan):**
```bash
python -m venv venv
source venv/bin/activate  # Untuk Linux/Mac/Termux

```


3. **Instal dependencies:**
```bash
python -m pip install -r requirements.txt

```


*(Catatan untuk pengguna Termux Android: Jika mengalami error saat menginstal `pydantic-core`, gunakan versi Pydantic 1.x dan gunakan mirror server).*
4. **Konfigurasi Environment:**
Buat file bernama `.env` di dalam folder utama dan isi dengan format berikut:
```env
TELEGRAM_TOKEN=isi_token_bot_telegram_anda_disini
GROQ_API_KEY=isi_api_key_groq_anda_disini
SPREADSHEET_ID=isi_id_google_sheets_anda_disini

```


5. **Tambahkan Kredensial Google:**
Masukkan file `credentials.json` Anda ke dalam folder utama repositori ini.

## 💻 Cara Menjalankan Bot

Jalankan perintah berikut di terminal Anda:

```bash
python main.py

```

Jika berhasil, terminal akan menampilkan log bahwa bot telah berjalan. Buka Telegram dan kirimkan perintah `/start` ke bot Anda.

## 📱 Catatan Khusus Pengguna Termux (Android)

Untuk menjalankan bot ini 24/7 di latar belakang menggunakan Termux, Anda memerlukan `tmux` dan beberapa sistem library tambahan untuk pemrosesan gambar:

```bash
# Instalasi library dasar untuk Pillow (Pemrosesan Gambar)
pkg install libjpeg-turbo freetype zlib -y

# Menjalankan bot di background menggunakan tmux
tmux new -s bot_pengeluaran
python main.py
# Tekan Ctrl+B lalu D untuk menyembunyikan layar

```

*Pastikan fitur "Acquire wakelock" di Termux aktif dan batasan baterai (Battery Saver) untuk aplikasi Termux telah dimatikan.*

## 🤝 Kontribusi

*Pull request* sangat dipersilakan. Untuk perubahan besar, harap buka *issue* terlebih dahulu untuk mendiskusikan apa yang ingin Anda ubah.

## 📄 Lisensi

[MIT](https://choosealicense.com/licenses/mit/)

```

```