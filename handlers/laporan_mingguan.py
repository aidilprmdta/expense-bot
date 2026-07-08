"""
handlers/laporan_mingguan.py
──────────────────────────────
Kirim ringkasan pengeluaran minggu lalu secara otomatis tiap Senin
pagi, tanpa perlu user ketik /rekap manual.

Penggunaan:
  /langganan aktif     → mulai terima laporan mingguan
  /langganan nonaktif  → berhenti terima laporan mingguan
  /langganan           → cek status langganan
"""

import logging
from datetime import date, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import (
    get_all_records,
    get_weekly_subscribers,
    add_weekly_subscriber,
    remove_weekly_subscriber,
)
from handlers.rekap import _aggregate, rupiah, EMOJI_KAT

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Cara pakai:\n"
    "`/langganan aktif` — mulai terima ringkasan mingguan tiap Senin pagi\n"
    "`/langganan nonaktif` — berhenti terima\n"
    "`/langganan` — cek status langganan saat ini"
)


async def cmd_langganan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /langganan."""
    args    = context.args or []
    chat_id = update.effective_chat.id

    if not args:
        subscribers = await get_weekly_subscribers()
        status = "✅ aktif" if chat_id in subscribers else "❌ belum aktif"
        await update.message.reply_text(
            f"📬 Status langganan laporan mingguan: {status}\n\n{HELP_TEXT}",
            parse_mode="Markdown",
        )
        return

    sub = args[0].lower().strip()

    if sub in ("aktif", "on", "ya"):
        loading = await update.message.reply_text("💾 Menyimpan... ⏳")
        try:
            baru = await add_weekly_subscriber(chat_id)
            if baru:
                await loading.edit_text(
                    "✅ *Langganan laporan mingguan aktif!*\n\n"
                    "Kamu bakal dapat ringkasan pengeluaran tiap *Senin pagi jam 08:00*, "
                    "otomatis tanpa perlu ketik `/rekap`.",
                    parse_mode="Markdown",
                )
            else:
                await loading.edit_text("_Kamu sudah berlangganan laporan mingguan._", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"[/langganan] Gagal simpan: {e}", exc_info=True)
            await loading.edit_text(f"😔 Gagal menyimpan: `{type(e).__name__}`", parse_mode="Markdown")
        return

    if sub in ("nonaktif", "off", "tidak", "batal"):
        loading = await update.message.reply_text("🔕 Memproses... ⏳")
        try:
            berhasil = await remove_weekly_subscriber(chat_id)
            if berhasil:
                await loading.edit_text("✅ Langganan laporan mingguan dinonaktifkan.")
            else:
                await loading.edit_text("_Kamu memang belum berlangganan._", parse_mode="Markdown")
        except Exception as e:
            logger.error(f"[/langganan] Gagal hapus: {e}", exc_info=True)
            await loading.edit_text(f"😔 Gagal memproses: `{type(e).__name__}`", parse_mode="Markdown")
        return

    await update.message.reply_text(
        f"⚠️ Perintah `{sub}` tidak dikenal.\n\n{HELP_TEXT}",
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────
# ISI LAPORAN
# ─────────────────────────────────────────────────────────────

def _rentang_minggu_lalu(hari_ini: date) -> tuple[date, date]:
    """
    Return (start, end) untuk Senin-Minggu minggu LALU (bukan minggu
    berjalan), dihitung relatif terhadap hari_ini.
    """
    # hari_ini.weekday(): Senin=0 ... Minggu=6
    senin_minggu_ini = hari_ini - timedelta(days=hari_ini.weekday())
    senin_minggu_lalu = senin_minggu_ini - timedelta(days=7)
    minggu_minggu_lalu = senin_minggu_lalu + timedelta(days=6)
    return senin_minggu_lalu, minggu_minggu_lalu


def build_laporan_mingguan(data: dict, start: date, end: date) -> str:
    """Bangun teks ringkasan mingguan dari hasil _aggregate()."""
    label = f"{start.strftime('%d %b')} – {end.strftime('%d %b %Y')}"

    if data["item_count"] == 0:
        return (
            f"📬 *Ringkasan Mingguan*\n{label}\n\n"
            f"_Nggak ada transaksi tercatat minggu lalu._"
        )

    baris = [
        f"📬 *Ringkasan Mingguan*",
        f"{label}\n",
        f"💸 Pengeluaran: *{rupiah(data['pengeluaran_total'])}* ({data['pengeluaran_count']} transaksi)",
    ]

    if data["pemasukan_total"] > 0:
        baris.append(f"💰 Pemasukan: *{rupiah(data['pemasukan_total'])}*")
        selisih = data["pemasukan_total"] - data["pengeluaran_total"]
        emoji_selisih = "📈" if selisih >= 0 else "📉"
        baris.append(f"{emoji_selisih} Selisih: *{rupiah(abs(selisih))}* {'surplus' if selisih >= 0 else 'defisit'}")

    per_kat = {k: v for k, v in data["per_kategori"].items() if k != "pemasukan" and v["total"] > 0}
    if per_kat:
        baris.append("\n📂 *Kategori Terbesar:*")
        for kat, val in sorted(per_kat.items(), key=lambda x: x[1]["total"], reverse=True)[:3]:
            emoji = EMOJI_KAT.get(kat, "📌")
            baris.append(f"{emoji} {kat.title()}: {rupiah(val['total'])}")

    baris.append("\n_Ketik /rekap buat lihat lebih detail._")
    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# JOB MINGGUAN: kirim laporan ke semua subscriber tiap Senin
# ─────────────────────────────────────────────────────────────

async def kirim_laporan_mingguan(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dijalankan otomatis tiap Senin jam 08:00 via JobQueue."""
    try:
        subscribers = await get_weekly_subscribers()
    except Exception as e:
        logger.error(f"[laporan_mingguan] Gagal baca subscriber: {e}", exc_info=True)
        return

    if not subscribers:
        return  # tidak ada yang berlangganan, tidak perlu proses lebih lanjut

    try:
        records = await get_all_records()
    except Exception as e:
        logger.error(f"[laporan_mingguan] Gagal baca Sheets: {e}", exc_info=True)
        return

    start, end = _rentang_minggu_lalu(date.today())
    data = _aggregate(records, start, end)
    pesan = build_laporan_mingguan(data, start, end)

    for chat_id in subscribers:
        try:
            await context.bot.send_message(chat_id=chat_id, text=pesan, parse_mode="Markdown")
            logger.info(f"[laporan_mingguan] Terkirim ke chat_id={chat_id}")
        except Exception as e:
            # Satu chat gagal (misal user block bot) tidak boleh menghentikan yang lain
            logger.warning(f"[laporan_mingguan] Gagal kirim ke chat_id={chat_id}: {e}")
            continue


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.laporan_mingguan
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import date as _date

    # Test _rentang_minggu_lalu: kalau hari ini Senin, 15 Juli 2026,
    # minggu lalu harusnya 6 Juli (Senin) - 12 Juli (Minggu) 2026
    hari_ini_test = _date(2026, 7, 15)  # Rabu
    start, end = _rentang_minggu_lalu(hari_ini_test)
    print(f"Hari ini (test): {hari_ini_test} ({hari_ini_test.strftime('%A')})")
    print(f"Minggu lalu: {start} ({start.strftime('%A')}) s/d {end} ({end.strftime('%A')})")
    assert start.strftime("%A") == "Monday"
    assert end.strftime("%A") == "Sunday"
    assert (end - start).days == 6
    print("_rentang_minggu_lalu: OK ✅\n")

    # Test build_laporan_mingguan dengan data dummy
    from handlers.rekap import _aggregate as agg
    DUMMY = [
        {"Tanggal": "06/07/2026", "Nama Item": "Kopi", "Kategori": "Makan", "Harga": 200000},
        {"Tanggal": "08/07/2026", "Nama Item": "Bensin", "Kategori": "Transport", "Harga": 100000},
        {"Tanggal": "10/07/2026", "Nama Item": "Gaji Mingguan", "Kategori": "Pemasukan", "Harga": 1000000},
    ]
    data = agg(DUMMY, _date(2026, 7, 6), _date(2026, 7, 12))
    pesan = build_laporan_mingguan(data, _date(2026, 7, 6), _date(2026, 7, 12))
    print(pesan)
    assert "Makan" in pesan and "surplus" in pesan
    print("\nbuild_laporan_mingguan: OK ✅")

    # Test dengan data kosong
    data_kosong = agg([], _date(2026, 7, 6), _date(2026, 7, 12))
    pesan_kosong = build_laporan_mingguan(data_kosong, _date(2026, 7, 6), _date(2026, 7, 12))
    assert "Nggak ada transaksi" in pesan_kosong
    print("build_laporan_mingguan (data kosong): OK ✅")

    print()
    print("✅ Semua test manual /langganan selesai")
