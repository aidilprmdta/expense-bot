"""
handlers/voice.py
─────────────────
Voice note → transaksi otomatis.

Flow:
  User kirim voice note di Telegram (.ogg opus)
    ↓  Download bytes dari Telegram server
    ↓  Groq Whisper large-v3 transcription (gratis, sangat cepat)
    ↓  parse_expense(teks_transkripsi)   ← reuse AI parser yang sudah ada
    ↓  append_expenses_batch(items)       ← simpan ke Sheets
    ↓  check_and_alert(...)              ← cek anomali
    ↓  Reply: transkripsi + konfirmasi item

Kenapa Groq Whisper:
  - Sudah punya GROQ_API_KEY
  - Support .ogg opus native (format Telegram)
  - ~1-3 detik untuk voice < 1 menit
  - Gratis dalam free tier
  - Bahasa Indonesia akurasi tinggi

Fungsi publik:
  handle_voice(update, context)  → dipanggil dari main.py via MessageHandler
"""

import os
import logging

from telegram import Update
from telegram.ext import ContextTypes
from groq import AsyncGroq

from handlers.ai_parser import parse_expense, format_konfirmasi
from handlers.sheets    import append_expenses_batch, get_all_records

logger = logging.getLogger(__name__)

WHISPER_MODEL = "whisper-large-v3"

# Groq Whisper limit: 25 MB, ~10 menit audio
# Telegram voice note limit: 20 MB, tapi biasanya < 2 MB untuk pesan normal
MAX_VOICE_BYTES = 20 * 1024 * 1024   # 20 MB

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError("GROQ_API_KEY tidak ditemukan di .env")
        _client = AsyncGroq(api_key=api_key)
    return _client


# ─────────────────────────────────────────────────────────────
# TRANSKRIPSI
# ─────────────────────────────────────────────────────────────

async def _transkripsi(audio_bytes: bytes, file_ext: str = "ogg") -> str:
    """
    Transkripsi audio ke teks menggunakan Groq Whisper large-v3.

    Args:
        audio_bytes : raw bytes audio (.ogg dari Telegram voice note)
        file_ext    : ekstensi file ("ogg", "mp3", "wav", dll)

    Returns:
        str : teks transkripsi (bahasa Indonesia)

    Raises:
        RuntimeError : jika Groq API error
        ValueError   : jika audio terlalu besar atau tidak valid
    """
    if not audio_bytes:
        raise ValueError("Audio bytes kosong.")
    if len(audio_bytes) > MAX_VOICE_BYTES:
        raise ValueError(
            f"File audio terlalu besar ({len(audio_bytes)/1024/1024:.1f} MB). "
            f"Maksimum {MAX_VOICE_BYTES/1024/1024:.0f} MB."
        )

    client = _get_client()
    logger.info(f"[voice] Transkripsi dimulai — {len(audio_bytes):,} bytes")

    try:
        filename     = f"voice.{file_ext}"
        transkripsi  = await client.audio.transcriptions.create(
            file            = (filename, audio_bytes),
            model           = WHISPER_MODEL,
            language        = "id",          # Bahasa Indonesia
            response_format = "text",        # return str langsung (bukan dict)
            temperature     = 0.0,           # deterministik
        )
        # Groq response_format="text" langsung return string
        teks = str(transkripsi).strip() if transkripsi else ""
        logger.info(f"[voice] Transkripsi selesai: '{teks[:80]}...'")
        return teks

    except Exception as e:
        logger.error(f"[voice] Groq Whisper error: {e}")
        raise RuntimeError(f"Gagal transkripsi audio: {e}") from e


# ─────────────────────────────────────────────────────────────
# MAIN HANDLER
# ─────────────────────────────────────────────────────────────

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler untuk voice note yang dikirim ke bot.

    Alur:
      1. Download .ogg dari Telegram
      2. Transkripsi → teks via Groq Whisper
      3. Parse teks → item pengeluaran via Groq LLM
      4. Simpan ke Google Sheets
      5. Cek anomali
      6. Reply: teks transkripsi + konfirmasi item

    Dipanggil dari main.py:
      app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    """
    from handlers.anomaly import check_and_alert

    user = update.effective_user
    logger.info(
        f"[voice] Voice note dari {user.first_name} (id={user.id}), "
        f"durasi={update.message.voice.duration}s"
    )

    # ── Pesan loading ─────────────────────────────────────────
    loading = await update.message.reply_text(
        "🎤 Voice diterima! Sedang transkripsi...\n"
        "_Groq Whisper sedang mendengarkan_ 👂",
        parse_mode="Markdown",
    )

    try:
        # ── 1. Download voice note ─────────────────────────────
        voice_file = update.message.voice
        tg_file    = await context.bot.get_file(voice_file.file_id)
        audio_bytes = await tg_file.download_as_bytearray()

        logger.info(f"[voice] Download selesai: {len(audio_bytes):,} bytes")

        # ── 2. Transkripsi dengan Groq Whisper ─────────────────
        # Telegram kirim voice sebagai .oga (ogg opus) — Groq support ini
        teks = await _transkripsi(bytes(audio_bytes), file_ext="ogg")

        if not teks or len(teks.strip()) < 3:
            await loading.edit_text(
                "🎤 Voice diterima, tapi teksnya tidak terdengar jelas. 🤔\n\n"
                "Tips:\n"
                "• Ucapkan dengan jelas dan pelan\n"
                "• Contoh: *'beli kopi dua puluh lima ribu'*\n"
                "• Hindari kebisingan di sekitar",
                parse_mode="Markdown",
            )
            return

        # ── 3. Edit pesan: tampilkan transkripsi ──────────────
        await loading.edit_text(
            f"🎤 *Transkripsi:*\n`{teks}`\n\n"
            "_Sedang memproses ke Sheets..._ ⏳",
            parse_mode="Markdown",
        )

        # ── 4. Parse teks → item pengeluaran ──────────────────
        items = await parse_expense(teks)

        # ── 5. Simpan ke Google Sheets ─────────────────────────
        await append_expenses_batch(items, catatan="via voice note")

        # ── 6. Edit pesan: tampilkan konfirmasi ───────────────
        konfirmasi = format_konfirmasi(items)
        await loading.edit_text(
            f"🎤 *Voice:* `{teks}`\n\n{konfirmasi}",
            parse_mode="Markdown",
        )

        # ── 7. Cek anomali (non-blocking) ─────────────────────
        try:
            all_records = await get_all_records()
            await check_and_alert(update, context, items, all_records)
        except Exception:
            pass   # anomaly check bukan fitur kritikal

    except ValueError as e:
        # Audio tidak valid / tidak ada teks
        logger.warning(f"[voice] ValueError: {e}")
        await loading.edit_text(
            f"🎤 *Tidak bisa diproses:*\n{e}\n\n"
            "Coba kirim ulang atau ketik pengeluaran secara manual.",
            parse_mode="Markdown",
        )

    except RuntimeError as e:
        # API error (Groq Whisper atau Groq LLM)
        logger.error(f"[voice] RuntimeError: {e}")
        await loading.edit_text(
            "Maaf, gagal memproses voice note. 😔\n"
            "Coba lagi atau ketik pengeluaran secara manual."
        )

    except Exception as e:
        logger.error(f"[voice] Unexpected error: {e}", exc_info=True)
        await loading.edit_text(
            "Terjadi kesalahan tidak terduga. 🙏\n"
            "Coba lagi atau ketik pengeluaran secara manual."
        )
