"""
Expense Tracker Bot — main.py
Phase 2: Bot Dasar

Struktur handler:
  /start        → sambut user + jelaskan cara pakai
  /help         → panduan lengkap
  /rekap        → rekap pengeluaran dari Google Sheets
  teks biasa    → echo balik (akan diganti AI parser di Phase 3)
  foto          → konfirmasi diterima (akan diganti Gemini Vision di Phase 5)
  command lain  → pesan "tidak dikenal"
"""

import os
import logging
from dotenv import load_dotenv

from telegram import Update
from telegram.error import TimedOut
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
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY") or None
TELEGRAM_TIMEOUT = float(os.getenv("TELEGRAM_TIMEOUT", "30"))

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
        "/rekap        — ringkasan hari + bulan + kategori\n"
        "/rekap hari   — rekap hari ini\n"
        "/rekap bulan  — breakdown per kategori bulan ini\n\n"

        "━━━━━━━━━━━━━━━\n"
        "💡 *Tips:*\n"
        "• Tidak perlu format khusus, nulis natural saja\n"
        "• Semua data tercatat di Google Sheets kamu\n"
        "• Bisa lihat dan edit langsung dari HP"
    )

    await update.message.reply_text(teks, parse_mode="Markdown")


async def cmd_rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /rekap         — ringkasan hari + bulan + breakdown kategori
    /rekap hari    — rekap hari ini saja
    /rekap bulan   — rekap bulan ini per kategori
    """
    from handlers.sheets import (
        get_today_summary,
        get_monthly_summary,
        format_rekap,
        format_rekap_hari,
        format_rekap_lengkap,
    )

    args = context.args
    sub  = args[0].lower() if args else None

    await update.message.reply_chat_action("typing")

    try:
        budget = int(os.getenv("MONTHLY_BUDGET", "0") or 0)

        if sub == "hari":
            summary = await get_today_summary()
            if summary["jumlah_item"] == 0:
                teks = (
                    f"📊 *Rekap Hari Ini*\n\n"
                    f"Belum ada pengeluaran tercatat hari ini "
                    f"({summary['periode']})."
                )
            else:
                teks = format_rekap_hari(summary)

        elif sub == "bulan":
            summary = await get_monthly_summary()
            if summary["jumlah_item"] == 0:
                teks = (
                    f"📊 *Rekap {summary['periode']}*\n\n"
                    "Belum ada pengeluaran tercatat bulan ini."
                )
            else:
                teks = format_rekap(summary, budget=budget)

        else:
            hari  = await get_today_summary()
            bulan = await get_monthly_summary()
            if hari["jumlah_item"] == 0 and bulan["jumlah_item"] == 0:
                teks = (
                    f"📊 *Rekap Pengeluaran*\n\n"
                    f"Belum ada pengeluaran tercatat di {bulan['periode']}."
                )
            else:
                teks = format_rekap_lengkap(hari, bulan, budget=budget)

        await update.message.reply_text(teks, parse_mode="Markdown")
        logger.info(f"/rekap {sub or 'default'} dari user id={update.effective_user.id}")

    except RuntimeError as e:
        logger.error(f"Sheets error saat /rekap: {e}")
        await update.message.reply_text(
            "Maaf, gagal membaca data dari Google Sheets. 😔\n"
            "Coba lagi sebentar ya!"
        )

    except Exception as e:
        logger.error(f"Unexpected error saat /rekap: {e}", exc_info=True)
        await update.message.reply_text(
            "Maaf, terjadi kesalahan saat membuat rekap. 🙏\n"
            "Coba lagi sebentar ya!"
        )


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
        items = await parse_expense(teks)
    except ValueError:
        await update.message.reply_text(
            "Hmm, aku kurang paham maksudnya. 🤔\n\n"
            "Coba tulis seperti:\n"
            "`beli kopi 25rb`\n"
            "`makan siang 35.000`\n"
            "`bensin 80k`",
            parse_mode="Markdown",
        )
        return
    except RuntimeError as e:
        logger.error(f"Groq error: {e}")
        await update.message.reply_text(
            "Maaf, AI sedang tidak bisa dihubungi. 😔\n"
            "Coba lagi dalam beberapa detik ya!"
        )
        return

    try:
        from handlers.sheets import append_expenses_batch
        await append_expenses_batch(items, catatan="via chat")
    except RuntimeError as e:
        logger.error(f"Sheets error: {e}")
        await update.message.reply_text(
            "Maaf, gagal menyimpan ke Google Sheets. 😔\n"
            "Coba lagi sebentar ya!"
        )
        return

    await update.message.reply_text(
        format_konfirmasi(items),
        parse_mode="Markdown",
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

    builder = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(TELEGRAM_TIMEOUT)
        .read_timeout(TELEGRAM_TIMEOUT)
        .write_timeout(TELEGRAM_TIMEOUT)
        .pool_timeout(TELEGRAM_TIMEOUT)
        .get_updates_connect_timeout(TELEGRAM_TIMEOUT)
        .get_updates_read_timeout(TELEGRAM_TIMEOUT)
        .get_updates_write_timeout(TELEGRAM_TIMEOUT)
        .get_updates_pool_timeout(TELEGRAM_TIMEOUT)
        .post_init(on_startup)
    )
    if TELEGRAM_PROXY:
        builder = builder.proxy(TELEGRAM_PROXY).get_updates_proxy(TELEGRAM_PROXY)
        logger.info(f"Telegram proxy aktif: {TELEGRAM_PROXY}")

    app = builder.build()

    # ── Command handlers ──────────────────────────────────────
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("rekap",  cmd_rekap))

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
    try:
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            bootstrap_retries=5,
        )
    except TimedOut:
        logger.error(
            "Gagal konek ke Telegram API (timeout). "
            "Cek koneksi internet, firewall, atau set TELEGRAM_PROXY di .env."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()