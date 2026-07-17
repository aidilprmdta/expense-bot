"""
handlers/rutin.py
───────────────────
Kelola transaksi berulang (tagihan bulanan, langganan) yang otomatis
tercatat sendiri tiap tanggal tertentu setiap bulan.

Penggunaan:
  /rutin tambah <hari> <harga> <kategori> <nama>
      contoh: /rutin tambah 5 150000 lainnya Internet Bulanan

  /rutin lihat          → daftar semua transaksi rutin aktif
  /rutin hapus <nomor>  → hapus satu aturan rutin
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_all_rutin, add_rutin, delete_rutin, get_custom_categories
from handlers.ai_parser import KATEGORI_VALID
from handlers.rekap import rupiah, EMOJI_KAT

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Cara pakai:\n"
    "`/rutin tambah <hari> <harga> <kategori> <nama>`\n"
    "   _contoh:_ `/rutin tambah 5 150000 lainnya Internet Bulanan`\n"
    "   _(hari = tanggal tiap bulan, 1-28, biar aman untuk semua bulan)_\n\n"
    "`/rutin lihat` — daftar semua transaksi rutin aktif\n"
    "`/rutin hapus <nomor>` — hapus satu aturan rutin\n\n"
    "_Bot otomatis catat transaksi ini tiap tanggal yang ditentukan, "
    "dan kirim notifikasi ke chat ini._"
)


def _safe_int(v) -> int:
    if isinstance(v, (int, float)):
        return max(0, int(v))
    try:
        cleaned = str(v).strip().replace("Rp", "").replace(" ", "").replace(".", "").replace(",", "")
        return max(0, int(float(cleaned)))
    except (ValueError, TypeError):
        return 0


async def _validate_kategori(nilai: str) -> str:
    kat = nilai.strip().lower()
    if kat in KATEGORI_VALID:
        return kat
    custom = await get_custom_categories()
    if kat in custom:
        return kat
    raise ValueError(
        f"Kategori '{nilai}' tidak dikenal. Cek `/kategori` untuk daftar yang valid."
    )


async def cmd_rutin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /rutin."""
    args = context.args or []
    if not args:
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    sub = args[0].lower().strip()

    # ── /rutin tambah <hari> <harga> <kategori> <nama...> ──────
    if sub == "tambah":
        if len(args) < 5:
            await update.message.reply_text(
                f"⚠️ Argumen kurang.\n\n{HELP_TEXT}", parse_mode="Markdown"
            )
            return

        try:
            hari = int(args[1])
            if not (1 <= hari <= 28):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Hari harus angka 1-28 (dibatasi 28 biar valid untuk semua bulan, "
                "termasuk Februari)."
            )
            return

        harga = _safe_int(args[2])
        if harga <= 0:
            await update.message.reply_text("⚠️ Harga harus lebih dari 0.")
            return

        try:
            kategori = await _validate_kategori(args[3])
        except ValueError as e:
            await update.message.reply_text(f"⚠️ {e}")
            return

        nama = " ".join(args[4:]).strip()
        if not nama:
            await update.message.reply_text("⚠️ Nama transaksi tidak boleh kosong.")
            return

        chat_id = update.effective_chat.id

        loading = await update.message.reply_text("💾 Menyimpan aturan rutin... ⏳")
        try:
            await add_rutin(chat_id, nama.title(), harga, kategori, hari, catatan="rutin otomatis")
            await loading.edit_text(
                f"✅ *Transaksi rutin ditambahkan*\n\n"
                f"📝 {nama.title()}\n"
                f"💰 {rupiah(harga)}\n"
                f"🏷️ {kategori.title()}\n"
                f"📅 Tanggal {hari} tiap bulan\n\n"
                f"_Bot akan otomatis catat & kasih notifikasi tiap tanggal {hari}._",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"[/rutin tambah] Gagal simpan: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal menyimpan*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
        return

    # ── /rutin lihat ────────────────────────────────────────────
    if sub == "lihat":
        loading = await update.message.reply_text("📋 Mengambil daftar rutin... ⏳")
        try:
            rutin_list = await get_all_rutin()
        except Exception as e:
            logger.error(f"[/rutin lihat] Gagal baca: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal membaca data*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
            return

        if not rutin_list:
            await loading.edit_text(
                "_Belum ada transaksi rutin._\n\nTambah dengan `/rutin tambah ...`",
                parse_mode="Markdown",
            )
            return

        baris = ["🔁 *Transaksi Rutin Aktif*\n"]
        for idx, r in enumerate(rutin_list, start=2):  # baris 2 = index pertama
            kategori = str(r.get("Kategori", "lainnya")).lower()
            emoji    = EMOJI_KAT.get(kategori, "📌")
            nama     = r.get("Nama", "Item")
            harga    = _safe_int(r.get("Harga", 0))
            hari     = r.get("Hari", "-")
            baris.append(
                f"{emoji} *{nama}* — {rupiah(harga)}\n"
                f"   📅 Tanggal {hari} · No. `{idx}`"
            )

        baris.append("\n_Hapus dengan `/rutin hapus <No.>`_")
        await loading.edit_text("\n".join(baris), parse_mode="Markdown")
        return

    # ── /rutin hapus <nomor> ────────────────────────────────────
    if sub == "hapus":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text(
                "⚠️ Sebutkan nomor aturan rutin yang mau dihapus.\n"
                "Lihat nomornya dengan `/rutin lihat`.",
                parse_mode="Markdown",
            )
            return

        row_number = int(args[1])
        loading    = await update.message.reply_text("🗑️ Menghapus... ⏳")
        try:
            await delete_rutin(row_number)
            await loading.edit_text("✅ Transaksi rutin berhasil dihapus.")
        except Exception as e:
            logger.error(f"[/rutin hapus] Gagal hapus: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal menghapus*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
        return

    await update.message.reply_text(
        f"⚠️ Perintah `{sub}` tidak dikenal.\n\n{HELP_TEXT}",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────
# JOB HARIAN: cek & catat transaksi rutin yang jatuh tempo hari ini
# ─────────────────────────────────────────────────────────────

async def cek_dan_catat_rutin_harian(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Dijalankan otomatis sekali sehari (via JobQueue).
    Cek semua aturan rutin, catat yang jatuh tempo hari ini,
    dan kirim notifikasi ke chat masing-masing.
    """
    from datetime import date
    from handlers.sheets import append_expenses_batch

    today = date.today()

    try:
        rutin_list = await get_all_rutin()
    except Exception as e:
        logger.error(f"[cek_rutin_harian] Gagal baca daftar rutin: {e}", exc_info=True)
        return

    for r in rutin_list:
        try:
            hari = int(r.get("Hari", 0))
        except (ValueError, TypeError):
            continue

        if hari != today.day:
            continue  # bukan jatuh tempo hari ini

        try:
            chat_id  = int(r.get("ChatID", 0))
            nama     = r.get("Nama", "Transaksi Rutin")
            harga    = _safe_int(r.get("Harga", 0))
            kategori = str(r.get("Kategori", "lainnya")).lower()

            if chat_id == 0 or harga <= 0:
                continue

            item = {
                "nama": nama, "harga": harga, "kategori": kategori,
                "tanggal": today.strftime("%Y-%m-%d"),
            }
            await append_expenses_batch([item], catatan="rutin otomatis")

            emoji = EMOJI_KAT.get(kategori, "📌")
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"🔁 *Transaksi Rutin Tercatat Otomatis*\n\n"
                    f"{emoji} {nama}\n"
                    f"💰 {rupiah(harga)}\n"
                    f"🏷️ {kategori.title()}"
                ),
                parse_mode="Markdown",
            )
            logger.info(f"[cek_rutin_harian] Tercatat: {nama} untuk chat_id={chat_id}")

        except Exception as e:
            logger.error(f"[cek_rutin_harian] Gagal proses satu rutin: {e}", exc_info=True)
            continue


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.rutin
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    assert _safe_int("150.000") == 150000
    assert _safe_int(150000) == 150000
    print("_safe_int: OK ✅")
    print("✅ Test manual /rutin (unit-level) selesai")