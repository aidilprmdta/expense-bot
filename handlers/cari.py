"""
handlers/cari.py
──────────────────
Cari transaksi berdasarkan kata kunci (nama item, kategori, atau catatan).

Penggunaan:
  /cari kopi              → cari transaksi yang mengandung "kopi"
  /cari makan             → cari berdasarkan kategori "makan"
  /cari kopi 50000        → cari "kopi" DAN harga >= 50000 (opsional filter harga)
"""

import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_all_records
from handlers.rekap import rupiah, EMOJI_KAT, _parse_tgl

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Cara pakai:\n"
    "`/cari kopi` — cari kata kunci di nama item, kategori, atau catatan\n"
    "`/cari makan` — bisa juga cari berdasarkan nama kategori\n\n"
    "_Pencarian tidak case-sensitive dan cocok untuk kecocokan sebagian "
    "(\"kopi\" akan ketemu \"Kopi Susu Gula Aren\")._"
)

MAX_HASIL_TAMPIL = 20


def _cocok(record: dict, keyword: str) -> bool:
    """Cek apakah keyword ada di nama item, kategori, atau catatan (case-insensitive)."""
    kw = keyword.lower()
    nama     = str(record.get("Nama Item", "")).lower()
    kategori = str(record.get("Kategori", "")).lower()
    catatan  = str(record.get("Catatan", "")).lower()
    return kw in nama or kw in kategori or kw in catatan


async def cmd_cari(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /cari."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            f"⚠️ Sebutkan kata kunci pencarian.\n\n{HELP_TEXT}",
            parse_mode="Markdown",
        )
        return

    keyword = " ".join(args).strip()
    if len(keyword) < 2:
        await update.message.reply_text(
            "⚠️ Kata kunci minimal 2 karakter ya, biar hasilnya tidak kebanyakan."
        )
        return

    loading = await update.message.reply_text(f"🔍 Mencari '{keyword}'... ⏳")

    try:
        records = await get_all_records()
    except Exception as e:
        logger.error(f"[/cari] Gagal baca Sheets: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal membaca Google Sheets*\n\n`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )
        return

    try:
        # Nomor baris asli di Sheets = index + 2 (baris 1 = header)
        hasil = [
            (idx + 2, r) for idx, r in enumerate(records) if _cocok(r, keyword)
        ]

        # Urutkan: transaksi terbaru dulu (fallback tanggal tak terparse ke paling akhir)
        def _sort_key(item):
            _, r = item
            tgl = _parse_tgl(str(r.get("Tanggal", "")))
            return tgl or date.min

        hasil.sort(key=_sort_key, reverse=True)

        if not hasil:
            await loading.edit_text(
                f"🔍 *Hasil pencarian: '{keyword}'*\n\n"
                f"_Tidak ditemukan transaksi yang cocok._",
                parse_mode="Markdown",
            )
            return

        total_ditemukan = len(hasil)
        total_nominal   = sum(_safe_int(r.get("Harga", 0)) for _, r in hasil)
        ditampilkan     = hasil[:MAX_HASIL_TAMPIL]

        baris = [
            f"🔍 *Hasil pencarian: '{keyword}'*",
            f"Ditemukan {total_ditemukan} transaksi · Total {rupiah(total_nominal)}\n",
        ]

        for row_number, r in ditampilkan:
            kategori = str(r.get("Kategori", "lainnya")).lower().strip()
            emoji    = EMOJI_KAT.get(kategori, "📌")
            nama     = r.get("Nama Item", "Item")
            harga    = _safe_int(r.get("Harga", 0))
            tanggal  = r.get("Tanggal", "-")
            baris.append(
                f"{emoji} *{nama}* — {rupiah(harga)}\n"
                f"   📅 {tanggal} · {kategori.title()} · No. `{row_number}`"
            )

        if total_ditemukan > MAX_HASIL_TAMPIL:
            baris.append(
                f"\n_...dan {total_ditemukan - MAX_HASIL_TAMPIL} transaksi lainnya. "
                f"Pakai kata kunci lebih spesifik untuk mempersempit hasil._"
            )

        baris.append("\n_Mau hapus salah satu? `/hapus <No.>`_")

        await loading.edit_text("\n".join(baris), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"[/cari] Error proses pencarian: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal memproses pencarian*\n\n`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )


def _safe_int(v) -> int:
    """Parse nilai Harga ke int, tahan format Sheets Indonesia ('25.000')."""
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


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.cari
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DUMMY = [
        {"Tanggal": "01/07/2026", "Nama Item": "Kopi Susu Gula Aren", "Kategori": "Makan", "Harga": 25000, "Catatan": "-"},
        {"Tanggal": "02/07/2026", "Nama Item": "Bensin Pertamax", "Kategori": "Transport", "Harga": 80000, "Catatan": "-"},
        {"Tanggal": "03/07/2026", "Nama Item": "Teh Tarik", "Kategori": "Makan", "Harga": 15000, "Catatan": "beli sama kopi tadi"},
        {"Tanggal": "04/07/2026", "Nama Item": "Baju Kaos", "Kategori": "Belanja", "Harga": 120000, "Catatan": "-"},
    ]

    hasil_kopi = [r for r in DUMMY if _cocok(r, "kopi")]
    print(f"Cari 'kopi': {len(hasil_kopi)} hasil (harus 2 — nama + catatan)")
    for r in hasil_kopi:
        print(" -", r["Nama Item"])

    hasil_makan = [r for r in DUMMY if _cocok(r, "makan")]
    print(f"\nCari 'makan' (kategori): {len(hasil_makan)} hasil (harus 2)")

    hasil_kosong = [r for r in DUMMY if _cocok(r, "xyz123")]
    print(f"\nCari 'xyz123': {len(hasil_kosong)} hasil (harus 0)")

    print("\n✅ Semua test manual selesai")
