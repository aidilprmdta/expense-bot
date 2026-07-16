"""
handlers/saldo.py
───────────────────
Saldo = Budget bulanan - Pengeluaran bulan ini + Pemasukan bulan ini.

Beda dari versi sebelumnya (akumulasi murni sepanjang riwayat), saldo di
sini SELALU dihitung ulang langsung dari budget + transaksi bulan
berjalan tiap kali dicek — bukan disimpan permanen. Jadi otomatis akurat
tanpa perlu sinkronisasi manual, dan selalu konsisten dengan angka yang
tampil di /rekap.

Penggunaan:
  /saldo   → lihat saldo saat ini (budget - pengeluaran + pemasukan bulan ini)
"""

import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_budget, get_all_records
from handlers.rekap import rupiah, _aggregate, resolve_periode

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "`/saldo` — lihat saldo bulan ini\n\n"
    "Rumus: *Budget - Pengeluaran bulan ini + Pemasukan bulan ini*"
)


# ─────────────────────────────────────────────────────────────
# HITUNG SALDO
# ─────────────────────────────────────────────────────────────

async def hitung_saldo_bulan_ini() -> dict:
    """
    Hitung saldo bulan berjalan: budget - pengeluaran + pemasukan.

    Return dict:
        {
            "budget"     : int,
            "pengeluaran": int,
            "pemasukan"  : int,
            "saldo"      : int,   # budget - pengeluaran + pemasukan
        }
    """
    budget  = await get_budget()
    records = await get_all_records()

    start, end, _ = resolve_periode("bulan")
    data = _aggregate(records, start, end)

    pengeluaran = data["pengeluaran_total"]
    pemasukan   = data["pemasukan_total"]
    saldo       = budget - pengeluaran + pemasukan

    return {
        "budget"     : budget,
        "pengeluaran": pengeluaran,
        "pemasukan"  : pemasukan,
        "saldo"      : saldo,
    }


# ─────────────────────────────────────────────────────────────
# TELEGRAM HANDLER
# ─────────────────────────────────────────────────────────────

async def cmd_saldo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /saldo."""
    loading = await update.message.reply_text("💰 Menghitung saldo... ⏳")

    try:
        hasil = await hitung_saldo_bulan_ini()
    except Exception as e:
        logger.error(f"[/saldo] Gagal hitung saldo: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal menghitung saldo*\n\n`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )
        return

    saldo = hasil["saldo"]
    emoji = "💰" if saldo >= 0 else "📛"

    baris = [f"{emoji} *Saldo Bulan Ini*\n"]

    if hasil["budget"] > 0:
        baris.append(f"🎯 Budget: {rupiah(hasil['budget'])}")
    else:
        baris.append("🎯 Budget: _belum diset (`/budget <jumlah>`)_")

    baris.append(f"💸 Pengeluaran: -{rupiah(hasil['pengeluaran'])}")
    baris.append(f"📥 Pemasukan: +{rupiah(hasil['pemasukan'])}")
    baris.append("")
    baris.append(f"{emoji} *Saldo: {rupiah(saldo)}*")

    if saldo < 0:
        baris.append("\n_Saldo minus — pengeluaran + budget yang kepakai lebih besar dari pemasukan._")

    await loading.edit_text("\n".join(baris), parse_mode="Markdown")


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.saldo
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, patch

    async def test():
        hari_ini_str = date.today().strftime("%d/%m/%Y")

        DUMMY = [
            {"Tanggal": hari_ini_str, "Nama Item": "Gaji", "Kategori": "Pemasukan", "Harga": "141000"},
            {"Tanggal": hari_ini_str, "Nama Item": "Lainnya", "Kategori": "Lainnya", "Harga": "265000"},
            {"Tanggal": hari_ini_str, "Nama Item": "Bensin", "Kategori": "Transport", "Harga": "120000"},
            {"Tanggal": hari_ini_str, "Nama Item": "Makan", "Kategori": "Makan", "Harga": "41000"},
        ]

        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=600000)), \
             patch(f"{__name__}.get_all_records", new=AsyncMock(return_value=DUMMY)):
            hasil = await hitung_saldo_bulan_ini()
            print(hasil)
            # budget 600rb - pengeluaran 426rb + pemasukan 141rb = 315rb
            assert hasil["budget"] == 600000
            assert hasil["pengeluaran"] == 426000
            assert hasil["pemasukan"] == 141000
            assert hasil["saldo"] == 315000
        print()
        print("hitung_saldo_bulan_ini(): OK ✅ -> saldo = 600rb - 426rb + 141rb = 315rb")

        # ── Tanpa budget (budget=0) ──
        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=0)), \
             patch(f"{__name__}.get_all_records", new=AsyncMock(return_value=DUMMY)):
            hasil2 = await hitung_saldo_bulan_ini()
            assert hasil2["saldo"] == 0 - 426000 + 141000
        print(f"Tanpa budget: saldo = {hasil2['saldo']} -> OK ✅ (tetap masuk akal, cuma pemasukan-pengeluaran)")

        print()
        print("✅ Semua test /saldo (formula baru) selesai")

    asyncio.run(test())