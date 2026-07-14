"""
handlers/saldo.py
───────────────────
Saldo berjalan yang otomatis ter-update tiap ada transaksi baru,
diedit, atau dihapus — pemasukan nambah saldo, pengeluaran ngurangin.

Penggunaan:
  /saldo              → lihat saldo saat ini
  /saldo set 500000   → koreksi manual saldo ke nilai tertentu
                         (misal buat setup awal, saldo kas yang sudah ada)

Fungsi lain (dipanggil dari main.py, hapus.py, edit.py, rutin.py):
  hitung_delta_items(items) -> int
  terapkan_delta_items(items)      → update saldo dari transaksi BARU
  batalkan_delta_items(record)     → update saldo saat transaksi DIHAPUS
                                      (kebalikan dari efek transaksi itu)
  terapkan_delta_edit(record_lama, updates) → update saldo saat DIEDIT
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_saldo, set_saldo, adjust_saldo
from handlers.rekap import rupiah, _safe_int

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Cara pakai:\n"
    "`/saldo` — lihat saldo saat ini\n"
    "`/saldo set 500000` — koreksi manual saldo ke nilai tertentu\n"
    "   _(dipakai misal buat setup awal, atau kalau saldo meleset)_"
)


# ─────────────────────────────────────────────────────────────
# HITUNG & TERAPKAN DELTA
# ─────────────────────────────────────────────────────────────

def hitung_delta_items(items: list[dict]) -> int:
    """
    Hitung total pengaruh sekumpulan item terhadap saldo.
    Pemasukan → positif (nambah), pengeluaran → negatif (ngurangin).

    items: list dict dengan key "harga" dan "kategori"
    (format dari ai_parser.parse_expense() atau vision.ocr_struk())
    """
    delta = 0
    for item in items:
        harga    = int(item.get("harga", 0))
        kategori = str(item.get("kategori", "")).lower().strip()
        if kategori == "pemasukan":
            delta += harga
        else:
            delta -= harga
    return delta


async def terapkan_delta_items(items: list[dict]) -> int:
    """
    Terapkan pengaruh item-item baru ke saldo (dipanggil setelah
    append_expenses_batch()). Return saldo baru setelah diupdate.
    """
    delta = hitung_delta_items(items)
    if delta == 0:
        return await get_saldo()
    return await adjust_saldo(delta)


async def batalkan_delta_items(record: dict) -> int:
    """
    Batalkan pengaruh SATU transaksi yang sudah tercatat terhadap saldo
    (dipanggil saat transaksi itu DIHAPUS). Efeknya dibalik: kalau
    transaksi itu pemasukan (nambah saldo), maka dihapus = saldo dikurangi
    sebesar itu; kalau pengeluaran, dihapus = saldo ditambah balik.

    record: dict format dari Google Sheets — key "Harga", "Kategori"
    (beda huruf besar-kecil dari format ai_parser, makanya fungsi terpisah)
    """
    harga    = _safe_int(record.get("Harga", 0))
    kategori = str(record.get("Kategori", "")).lower().strip()

    if kategori == "pemasukan":
        delta = -harga   # pemasukan dihapus -> saldo berkurang
    else:
        delta = harga    # pengeluaran dihapus -> saldo bertambah balik

    if delta == 0:
        return await get_saldo()
    return await adjust_saldo(delta)


async def terapkan_delta_edit(record_lama: dict, updates: dict) -> int:
    """
    Sesuaikan saldo saat transaksi DIEDIT (bukan dihapus). Batalkan
    dulu efek nilai lama, baru terapkan efek nilai baru — supaya saldo
    tetap akurat walau harga ATAU kategori yang diubah.

    record_lama: dict format Sheets (sebelum diedit)
    updates    : dict {"harga": ..., "kategori": ...} — field yang berubah
                 (field lain seperti nama/tanggal/catatan tidak mempengaruhi saldo)
    """
    harga_lama    = _safe_int(record_lama.get("Harga", 0))
    kategori_lama = str(record_lama.get("Kategori", "")).lower().strip()

    harga_baru    = updates.get("harga", harga_lama)
    kategori_baru = str(updates.get("kategori", kategori_lama)).lower().strip()

    efek_lama = harga_lama if kategori_lama == "pemasukan" else -harga_lama
    efek_baru = harga_baru if kategori_baru == "pemasukan" else -harga_baru

    delta = efek_baru - efek_lama
    if delta == 0:
        return await get_saldo()
    return await adjust_saldo(delta)


# ─────────────────────────────────────────────────────────────
# TELEGRAM HANDLER
# ─────────────────────────────────────────────────────────────

async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /saldo."""
    args = context.args or []

    if not args:
        loading = await update.message.reply_text("💰 Mengambil saldo... ⏳")
        try:
            saldo = await get_saldo()
        except Exception as e:
            logger.error(f"[/saldo] Gagal baca saldo: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal membaca saldo*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
            return

        emoji = "💰" if saldo >= 0 else "📛"
        keterangan = "" if saldo >= 0 else "\n\n_Saldo minus — pengeluaran lebih besar dari pemasukan tercatat._"
        await loading.edit_text(
            f"{emoji} *Saldo Saat Ini*\n\n{rupiah(saldo)}{keterangan}",
            parse_mode="Markdown",
        )
        return

    sub = args[0].lower().strip()

    if sub == "set":
        if len(args) < 2:
            await update.message.reply_text(
                f"⚠️ Sebutkan nominal saldo.\n\n{HELP_TEXT}", parse_mode="Markdown"
            )
            return
        try:
            nilai_str = args[1].replace(".", "").replace(",", "")
            nilai_baru = int(nilai_str)
        except ValueError:
            await update.message.reply_text(
                "⚠️ Format nominal tidak valid.\nContoh: `/saldo set 500000`",
                parse_mode="Markdown",
            )
            return

        loading = await update.message.reply_text("💾 Menyimpan saldo... ⏳")
        try:
            await set_saldo(nilai_baru)
            await loading.edit_text(
                f"✅ Saldo dikoreksi ke *{rupiah(nilai_baru)}*",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"[/saldo set] Gagal simpan: {e}", exc_info=True)
            await loading.edit_text(
                f"😔 *Gagal menyimpan*\n\n`{type(e).__name__}: {str(e)[:120]}`",
                parse_mode="Markdown",
            )
        return

    await update.message.reply_text(
        f"⚠️ Perintah `{sub}` tidak dikenal.\n\n{HELP_TEXT}",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.saldo
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    items_campuran = [
        {"nama": "Gaji", "harga": 5000000, "kategori": "pemasukan"},
        {"nama": "Kopi", "harga": 25000, "kategori": "makan"},
        {"nama": "Bensin", "harga": 50000, "kategori": "transport"},
    ]
    delta = hitung_delta_items(items_campuran)
    assert delta == 5000000 - 25000 - 50000
    print(f"hitung_delta_items (campuran): {delta} -> OK ✅")

    items_pengeluaran_saja = [{"nama": "Makan", "harga": 30000, "kategori": "makan"}]
    delta2 = hitung_delta_items(items_pengeluaran_saja)
    assert delta2 == -30000
    print(f"hitung_delta_items (pengeluaran saja): {delta2} -> OK ✅")

    items_pemasukan_saja = [{"nama": "Bonus", "harga": 200000, "kategori": "pemasukan"}]
    delta3 = hitung_delta_items(items_pemasukan_saja)
    assert delta3 == 200000
    print(f"hitung_delta_items (pemasukan saja): {delta3} -> OK ✅")

    print()
    print("✅ Test sinkron /saldo selesai (test async ada di suite terpisah)")
