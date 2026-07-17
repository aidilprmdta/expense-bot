"""
handlers/hapus.py
───────────────────
Hapus transaksi dari Google Sheets, dengan alur konfirmasi supaya
tidak ada yang kehapus secara tidak sengaja.

Penggunaan:
  /hapus terakhir       → hapus transaksi paling akhir yang tercatat
  /hapus 15             → hapus transaksi di baris nomor 15
                           (nomor baris bisa dilihat dari hasil /cari)
  /hapus konfirmasi     → konfirmasi penghapusan yang sedang menunggu
  /hapus batal          → batalkan penghapusan yang sedang menunggu
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_all_records, get_last_transaction, delete_row
from handlers.rekap import rupiah, EMOJI_KAT

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Cara pakai:\n"
    "`/hapus terakhir` — hapus transaksi paling akhir\n"
    "`/hapus 15` — hapus transaksi di baris nomor 15\n"
    "   _(nomor baris bisa dilihat dari hasil `/cari`)_\n\n"
    "Setelah itu kamu akan diminta konfirmasi:\n"
    "`/hapus konfirmasi` — lanjutkan hapus\n"
    "`/hapus batal` — batalkan"
)


def _safe_int(v) -> int:
    if isinstance(v, (int, float)):
        return max(0, int(v))
    try:
        cleaned = str(v).strip().replace("Rp", "").replace(" ", "")
        if "." in cleaned and "," not in cleaned:
            parts = cleaned.split(".")
            if all(len(p) <= 3 for p in parts[1:]):
                cleaned = cleaned.replace(".", "")
        cleaned = cleaned.replace(",", "")
        return max(0, int(float(cleaned)))
    except (ValueError, TypeError):
        return 0


def _format_konfirmasi(record: dict) -> str:
    kategori = str(record.get("Kategori", "lainnya")).lower().strip()
    emoji    = EMOJI_KAT.get(kategori, "📌")
    nama     = record.get("Nama Item", "Item")
    harga    = _safe_int(record.get("Harga", 0))
    tanggal  = record.get("Tanggal", "-")

    return (
        f"⚠️ *Konfirmasi Hapus Transaksi*\n\n"
        f"{emoji} *{nama}*\n"
        f"💰 {rupiah(harga)}\n"
        f"📅 {tanggal} · {kategori.title()}\n\n"
        f"Yakin mau hapus transaksi ini?\n"
        f"✅ `/hapus konfirmasi` — ya, hapus\n"
        f"❌ `/hapus batal` — batal"
    )


async def cmd_hapus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /hapus."""
    args = context.args or []

    if not args:
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    sub = args[0].lower().strip()

    # ── Konfirmasi penghapusan yang sedang menunggu ────────────
    if sub in ("konfirmasi", "ya", "yes", "y"):
        pending = context.user_data.get("hapus_pending")
        if not pending:
            await update.message.reply_text(
                "⚠️ Tidak ada penghapusan yang menunggu konfirmasi.\n"
                "Mulai dulu dengan `/hapus terakhir` atau `/hapus <nomor>`.",
                parse_mode="Markdown",
            )
            return

        loading = await update.message.reply_text("🗑️ Menghapus... ⏳")
        try:
            await delete_row(pending["row_number"])

            await loading.edit_text(
                f"✅ Transaksi *{pending['nama']}* ({rupiah(pending['harga'])}) berhasil dihapus."
            , parse_mode="Markdown")
        except Exception as e:
            logger.error(f"[/hapus] Gagal hapus baris: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal menghapus transaksi*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
        finally:
            context.user_data.pop("hapus_pending", None)
        return

    # ── Batalkan penghapusan yang sedang menunggu ──────────────
    if sub in ("batal", "cancel", "tidak", "no", "n"):
        had_pending = context.user_data.pop("hapus_pending", None) is not None
        if had_pending:
            await update.message.reply_text("❌ Penghapusan dibatalkan.")
        else:
            await update.message.reply_text("_Tidak ada penghapusan yang perlu dibatalkan._", parse_mode="Markdown")
        return

    # ── Tentukan transaksi mana yang mau dihapus ───────────────
    if sub == "terakhir":
        loading = await update.message.reply_text("🔍 Mencari transaksi terakhir... ⏳")
        try:
            info = await get_last_transaction()
        except Exception as e:
            logger.error(f"[/hapus] Gagal baca Sheets: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal membaca Google Sheets*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
            return

        if info is None:
            await loading.edit_text("_Belum ada transaksi yang tercatat._", parse_mode="Markdown")
            return

        row_number, record = info

    elif sub.isdigit():
        row_number = int(sub)
        if row_number < 2:
            await update.message.reply_text(
                "⚠️ Nomor baris tidak valid — baris 1 adalah header, "
                "transaksi dimulai dari baris 2."
            )
            return

        loading = await update.message.reply_text(f"🔍 Mencari transaksi di baris {row_number}... ⏳")
        try:
            records = await get_all_records()
        except Exception as e:
            logger.error(f"[/hapus] Gagal baca Sheets: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal membaca Google Sheets*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
            return

        idx = row_number - 2  # baris 2 di sheet = index 0 di list records
        if idx < 0 or idx >= len(records):
            await loading.edit_text(
                f"⚠️ Baris nomor {row_number} tidak ditemukan.\n"
                f"Total transaksi saat ini: {len(records)} "
                f"(baris 2 s/d {len(records) + 1})."
            )
            return

        record = records[idx]

    else:
        await update.message.reply_text(
            f"⚠️ Argumen `{sub}` tidak dikenal.\n\n{HELP_TEXT}",
            parse_mode="Markdown",
        )
        return

    # ── Simpan pending & minta konfirmasi ──────────────────────
    context.user_data["hapus_pending"] = {
        "row_number": row_number,
        "nama"      : record.get("Nama Item", "Item"),
        "harga"     : _safe_int(record.get("Harga", 0)),
        "kategori"  : record.get("Kategori", "lainnya"),
    }
    await loading.edit_text(_format_konfirmasi(record), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.hapus
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dummy_record = {
        "Tanggal": "05/07/2026", "Nama Item": "Kopi Susu",
        "Kategori": "Makan", "Harga": 25000, "Catatan": "-",
    }
    print(_format_konfirmasi(dummy_record))
    print()
    print("✅ Format konfirmasi berhasil dibuat")