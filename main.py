"""
Expense Tracker Bot — main.py
Phase 2: Bot Dasar

Struktur handler:
  /start        → sambut user + jelaskan cara pakai
  /help         → panduan lengkap
  /rekap        → rekap harian/bulanan/tahunan (Phase 6)
  teks biasa    → echo balik (akan diganti AI parser di Phase 3)
  foto          → konfirmasi diterima (akan diganti Gemini Vision di Phase 5)
  command lain  → pesan "tidak dikenal"
"""

import os
import logging
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# HELPER — format harga ke Rupiah
# ─────────────────────────────────────────────────────────────

def rupiah(angka: int) -> str:
    """Contoh: 25000 → 'Rp 25.000'"""
    return f"Rp {angka:,}".replace(",", ".")


# ─────────────────────────────────────────────────────────────
# COMMAND HANDLERS
# ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /start — sambut user dan jelaskan cara pakai bot.
    """
    user = update.effective_user
    nama = user.first_name or "Kamu"

    teks = (
        f"Halo, *{nama}!* 👋\n\n"
        "Aku *Expense Tracker Bot* — pencatat keuangan pribadimu.\n"
        "Tinggal chat atau foto struk, semua langsung masuk Google Sheets.\n\n"
        "━━━━━━━━━━━━━━━\n"
        "💬 *Cara catat pengeluaran:*\n"
        "Ketik aja bebas, contoh:\n"
        "`beli kopi 25rb`\n"
        "`makan siang nasi padang 35.000`\n"
        "`bensin pertamax 80k`\n"
        "`belanja supermarket 200.000`\n\n"
        "📷 *Foto struk / nota:*\n"
        "Kirim foto → semua item otomatis tercatat\n\n"
        "━━━━━━━━━━━━━━━\n"
        "📌 *Command:*\n"
        "/start — pesan ini\n"
        "/help  — panduan lengkap\n"
        "/rekap — lihat rekap pengeluaran\n\n"
        "_Yuk mulai catat! Ketik pengeluaran pertamamu_ 💰"
    )

    await update.message.reply_text(teks, parse_mode="Markdown")
    logger.info(f"/start dari {user.first_name} (id={user.id})")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /help — panduan pemakaian lengkap.
    """
    teks = (
        "📖 *Panduan Expense Tracker Bot*\n\n"

        "━━━━━━━━━━━━━━━\n"
        "💬 *Catat lewat chat (ketik bebas):*\n"
        "`kopi 25rb`\n"
        "`makan siang 35.000`\n"
        "`isi bensin 80k`\n"
        "`bayar listrik PLN 250.000`\n"
        "`beli obat apotek 45rb`\n"
        "`nonton bioskop 75.000`\n\n"
        "AI akan otomatis deteksi:\n"
        "• Nama item\n"
        "• Jumlah harga (Rupiah)\n"
        "• Kategori (makan, transport, belanja, dll)\n"
        "• Tanggal (otomatis hari ini)\n\n"

        "━━━━━━━━━━━━━━━\n"
        "📷 *Catat lewat foto struk:*\n"
        "Kirim foto receipt/nota → semua item terbaca otomatis\n\n"

        "━━━━━━━━━━━━━━━\n"
        "📊 *Lihat rekap:*\n"
        "/rekap        — rekap bulan ini\n"
        "/rekap hari   — rekap hari ini\n"
        "/rekap bulan  — breakdown per kategori bulan ini\n"
        "/grafik       — pie chart breakdown bulan ini\n"
        "/export       — export data ke Excel/CSV\n"
        "/cari <kata>  — cari transaksi\n"
        "/hapus terakhir — hapus transaksi terakhir\n"
        "/edit <no> field=nilai — edit transaksi\n"
        "/banding      — bandingkan bulan ini vs lalu\n\n"

        "━━━━━━━━━━━━━━━\n"
        "🔁 *Transaksi rutin (tagihan bulanan):*\n"
        "/rutin tambah <hari> <harga> <kategori> <nama>\n"
        "/rutin lihat  — daftar rutin aktif\n"
        "/rutin hapus <no> — hapus satu aturan\n\n"

        "━━━━━━━━━━━━━━━\n"
        "📬 *Laporan mingguan otomatis:*\n"
        "/langganan aktif — mulai terima ringkasan tiap Senin pagi\n"
        "/langganan nonaktif — berhenti terima\n\n"

        "━━━━━━━━━━━━━━━\n"
        "💵 *Budget:*\n"
        "/budget            — lihat status budget bulan ini\n"
        "/budget 3000000    — set budget bulanan\n"
        "_Bot otomatis kirim peringatan kalau pengeluaran sudah "
        "80% atau lewat dari budget._\n\n"

        "━━━━━━━━━━━━━━━\n"
        "💰 *Saldo:*\n"
        "/saldo             — lihat saldo saat ini\n"
        "/saldo set 500000  — koreksi saldo manual (misal setup awal)\n"
        "/saldo sinkronkan  — hitung ulang saldo dari seluruh riwayat transaksi\n"
        "_Saldo otomatis ke-update tiap ada transaksi baru, diedit, atau dihapus._\n\n"

        "━━━━━━━━━━━━━━━\n"
        "🏷️ *Kelola kategori:*\n"
        "/kategori              — lihat semua kategori\n"
        "/tambahkategori <nama> — tambah kategori baru\n"
        "/hapuskategori <nama>  — hapus kategori kustom\n\n"

        "━━━━━━━━━━━━━━━\n"
        "💡 *Tips:*\n"
        "• Tidak perlu format khusus, nulis natural saja\n"
        "• Semua data tercatat di Google Sheets kamu\n"
        "• Bisa lihat dan edit langsung dari HP"
    )

    await update.message.reply_text(teks, parse_mode="Markdown")


async def cmd_rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /rekap [hari|kemarin|minggu|bulan|tahun|MM/YYYY]
    Phase 6 aktif — delegasi ke handlers/rekap.py.
    """
    from handlers.rekap import cmd_rekap as _rekap
    await _rekap(update, context)


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /budget [jumlah]  — lihat atau set budget bulanan.
    Phase 6 aktif — delegasi ke handlers/rekap.py.
    """
    from handlers.rekap import cmd_budget as _budget
    await _budget(update, context)


async def cmd_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kategori — lihat semua kategori bawaan + kustom."""
    from handlers.kategori import cmd_kategori as _kategori
    await _kategori(update, context)


async def cmd_tambahkategori(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tambahkategori <nama> — tambah kategori kustom baru."""
    from handlers.kategori import cmd_tambahkategori as _tambah
    await _tambah(update, context)


async def cmd_hapuskategori(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hapuskategori <nama> — hapus kategori kustom."""
    from handlers.kategori import cmd_hapuskategori as _hapus
    await _hapus(update, context)


async def cmd_grafik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/grafik [hari|minggu|bulan|tahun|MM/YYYY] — pie chart breakdown pengeluaran."""
    from handlers.grafik import cmd_grafik as _grafik
    await _grafik(update, context)


async def cmd_export(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/export [periode] [format] — export data ke file Excel/CSV."""
    from handlers.export import cmd_export as _export
    await _export(update, context)


async def cmd_cari(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/cari <kata kunci> — cari transaksi berdasarkan nama/kategori/catatan."""
    from handlers.cari import cmd_cari as _cari
    await _cari(update, context)


async def cmd_hapus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hapus terakhir|<nomor> — hapus transaksi (dengan konfirmasi)."""
    from handlers.hapus import cmd_hapus as _hapus
    await _hapus(update, context)


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/edit <nomor|terakhir> <field>=<nilai> — edit transaksi tanpa hapus ulang."""
    from handlers.edit import cmd_edit as _edit
    await _edit(update, context)


async def cmd_banding(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/banding [MM/YYYY MM/YYYY] — bandingkan pengeluaran dua bulan."""
    from handlers.banding import cmd_banding as _banding
    await _banding(update, context)


async def cmd_rutin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/rutin tambah|lihat|hapus — kelola transaksi berulang (tagihan bulanan)."""
    from handlers.rutin import cmd_rutin as _rutin
    await _rutin(update, context)


async def cmd_langganan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/langganan aktif|nonaktif — kelola langganan laporan mingguan otomatis."""
    from handlers.laporan_mingguan import cmd_langganan as _langganan
    await _langganan(update, context)


async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/saldo [set <nominal>] — lihat atau koreksi saldo berjalan."""
    from handlers.saldo import cmd_saldo as _saldo
    await _saldo(update, context)


# ─────────────────────────────────────────────────────────────
# MESSAGE HANDLERS
# ─────────────────────────────────────────────────────────────

async def handle_teks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler pesan teks biasa.
    Phase 3: parse dengan Groq AI → konfirmasi ke user.
    Phase 4: tambah append_to_sheets() setelah parse berhasil.
    """
    from handlers.ai_parser import parse_expense, format_konfirmasi

    teks = update.message.text
    user = update.effective_user
    logger.info(f"Teks dari {user.first_name} (id={user.id}): '{teks}'")

    # Tunjukkan "sedang mengetik..." supaya user tahu bot sedang proses
    await update.message.reply_chat_action("typing")

    try:
        # ── Phase 3: Parse dengan Groq AI ─────────────────────
        items = await parse_expense(teks)

        # ── Phase 4: Simpan ke Google Sheets ──────────────────
        from handlers.sheets import append_expenses_batch   # ada 's' di expenses
        await append_expenses_batch(items, catatan="via chat")

        # Kirim konfirmasi ke user
        await update.message.reply_text(
            format_konfirmasi(items),
            parse_mode="Markdown",
        )

        # ── Cek budget alert (proaktif, tidak perlu /budget manual) ──
        try:
            from handlers.budget_alert import cek_budget_alert
            alert = await cek_budget_alert(items)
            if alert:
                await update.message.reply_text(alert, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"[handle_teks] Gagal cek budget alert: {e}")

        # ── Update saldo berjalan ──────────────────────────────
        try:
            from handlers.saldo import terapkan_delta_items
            await terapkan_delta_items(items)
        except Exception as e:
            logger.warning(f"[handle_teks] Gagal update saldo: {e}")

    except ValueError:
        # Teks tidak mengandung pengeluaran yang bisa diparsing
        await update.message.reply_text(
            "Hmm, aku kurang paham maksudnya. 🤔\n\n"
            "Coba tulis seperti:\n"
            "`beli kopi 25rb`\n"
            "`makan siang 35.000`\n"
            "`bensin 80k`",
            parse_mode="Markdown",
        )

    except RuntimeError as e:
        # Error koneksi ke Groq
        logger.error(f"Groq error: {e}")
        await update.message.reply_text(
            "Maaf, AI sedang tidak bisa dihubungi. 😔\n"
            "Coba lagi dalam beberapa detik ya!"
        )


async def handle_foto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler foto struk — Phase 5 (Gemini Vision aktif).

    Flow:
      1. Download foto resolusi tertinggi dari Telegram
      2. OCR + ekstraksi item dengan Gemini 2.0 Flash Vision
      3. Batch-append semua item ke Google Sheets (1 API call)
      4. Edit pesan "sedang proses" → ringkasan item yang ditemukan
    """
    from handlers.vision import ocr_struk, format_struk_summary
    from handlers.sheets import append_expenses_batch

    user    = update.effective_user
    caption = update.message.caption or ""

    logger.info(
        f"Foto dari {user.first_name} (id={user.id}), caption='{caption}'"
    )

    # ── Kirim pesan "sedang memproses" dulu ───────────────────
    # Bot edit pesan ini nanti → lebih bersih daripada 2 pesan terpisah
    proses_msg = await update.message.reply_text(
        "📷 Foto diterima! Sedang membaca struk...\n"
        "_Ini bisa makan 5–10 detik_ ⏳",
        parse_mode="Markdown",
    )

    try:
        # ── 1. Download foto dari Telegram ────────────────────
        foto      = update.message.photo[-1]      # resolusi tertinggi
        tg_file   = await context.bot.get_file(foto.file_id)
        img_bytes = await tg_file.download_as_bytearray()

        logger.info(f"[handle_foto] Download: {len(img_bytes):,} bytes")

        # ── 2. OCR dengan Gemini Vision ───────────────────────
        items = await ocr_struk(bytes(img_bytes), caption=caption)

        # ── 3. Batch append semua item ke Google Sheets ───────
        await append_expenses_batch(items, catatan="via foto struk")

        # ── 4. Edit pesan proses → ringkasan ──────────────────
        await proses_msg.edit_text(
            format_struk_summary(items),
            parse_mode="Markdown",
        )

        # ── 5. Cek budget alert (proaktif, tidak perlu /budget manual) ──
        try:
            from handlers.budget_alert import cek_budget_alert
            alert = await cek_budget_alert(items)
            if alert:
                await update.message.reply_text(alert, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"[handle_foto] Gagal cek budget alert: {e}")

        # ── 6. Update saldo berjalan ────────────────────────────
        try:
            from handlers.saldo import terapkan_delta_items
            await terapkan_delta_items(items)
        except Exception as e:
            logger.warning(f"[handle_foto] Gagal update saldo: {e}")

    except ValueError as e:
        # Foto buram, bukan struk, atau tidak ada item terbaca
        logger.warning(f"[handle_foto] Tidak ada item: {e}")
        await proses_msg.edit_text(
            f"📷 *Foto diterima, tapi...*\n\n"
            f"{e}\n\n"
            "Atau coba ketik pengeluarannya manual:\n"
            "`beli sesuatu 50rb`",
            parse_mode="Markdown",
        )

    except RuntimeError as e:
        # Error API Gemini atau Sheets
        logger.error(f"[handle_foto] API error: {e}")
        await proses_msg.edit_text(
            "Maaf, terjadi kesalahan saat memproses foto. 😔\n"
            "Coba lagi dalam beberapa saat ya!"
        )

    except Exception as e:
        logger.error(f"[handle_foto] Unexpected: {e}", exc_info=True)
        await proses_msg.edit_text(
            "Terjadi kesalahan tidak terduga. 🙏\n"
            "Coba lagi atau ketik pengeluaran secara manual."
        )


async def handle_command_unknown(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handler untuk command yang tidak dikenali."""
    await update.message.reply_text(
        "Hmm, command itu belum aku kenal. 🤔\n"
        "Ketik /help untuk lihat command yang tersedia."
    )


# ─────────────────────────────────────────────────────────────
# ERROR HANDLER
# ─────────────────────────────────────────────────────────────

async def error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log semua exception yang tidak ter-handle."""
    logger.error(
        "Exception saat handle update:",
        exc_info=context.error,
    )
    # Kirim pesan error ke user kalau bisa
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            "Maaf, terjadi kesalahan. Coba lagi sebentar ya! 🙏"
        )


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError(
            "TELEGRAM_TOKEN tidak ditemukan!\n"
            "Pastikan file .env ada dan berisi: TELEGRAM_TOKEN=xxxxxxxx"
        )

    # Build application
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # ── Startup: cek koneksi Google Sheets ───────────────────
    async def on_startup(app):
        from handlers.sheets import test_connection
        ok = await test_connection()
        if ok:
            logger.info("Startup: Google Sheets terhubung.")
        else:
            logger.warning(
                "Startup: Google Sheets TIDAK terhubung. "
                "Cek credentials.json dan SPREADSHEET_ID di .env."
            )

    app.post_init = on_startup

    # ── Job harian: cek transaksi rutin yang jatuh tempo ──────
    from datetime import time as dtime
    from handlers.rutin import cek_dan_catat_rutin_harian
    from handlers.laporan_mingguan import kirim_laporan_mingguan

    if app.job_queue is not None:
        app.job_queue.run_daily(
            cek_dan_catat_rutin_harian,
            time=dtime(hour=8, minute=0),  # jam 08:00 waktu server
            name="cek_rutin_harian",
        )
        logger.info("Job harian 'cek_rutin_harian' terdaftar (08:00).")

        # PENTING: di python-telegram-bot v20+, 0=Minggu, 1=Senin, ..., 6=Sabtu
        # (berubah dari versi lama yang 0=Senin). Jadi Senin = 1, bukan 0.
        app.job_queue.run_daily(
            kirim_laporan_mingguan,
            time=dtime(hour=8, minute=0),
            days=(1,),  # 1 = Senin
            name="laporan_mingguan",
        )
        logger.info("Job mingguan 'laporan_mingguan' terdaftar (Senin 08:00).")
    else:
        logger.warning(
            "JobQueue tidak tersedia — transaksi rutin & laporan mingguan TIDAK "
            "akan otomatis jalan. Install dengan: pip install \"python-telegram-bot[job-queue]\""
        )

    # ── Command handlers ──────────────────────────────────────
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("rekap",  cmd_rekap))
    app.add_handler(CommandHandler("budget", cmd_budget))
    app.add_handler(CommandHandler("kategori",       cmd_kategori))
    app.add_handler(CommandHandler("tambahkategori", cmd_tambahkategori))
    app.add_handler(CommandHandler("hapuskategori",  cmd_hapuskategori))
    app.add_handler(CommandHandler("grafik", cmd_grafik))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("cari", cmd_cari))
    app.add_handler(CommandHandler("hapus", cmd_hapus))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("banding", cmd_banding))
    app.add_handler(CommandHandler("rutin", cmd_rutin))
    app.add_handler(CommandHandler("langganan", cmd_langganan))
    app.add_handler(CommandHandler("saldo", cmd_saldo))

    # ── Message handlers ──────────────────────────────────────
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_teks,
    ))
    app.add_handler(MessageHandler(
        filters.PHOTO,
        handle_foto,
    ))
    app.add_handler(MessageHandler(
        filters.COMMAND,
        handle_command_unknown,
    ))

    # ── Error handler ─────────────────────────────────────────
    app.add_error_handler(error_handler)

    # ── Start polling ─────────────────────────────────────────
    logger.info("Bot berjalan... Tekan Ctrl+C untuk stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()