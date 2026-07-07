"""
handlers/grafik.py
───────────────────
Generate pie chart breakdown pengeluaran per kategori pakai matplotlib,
dikirim sebagai foto langsung di chat Telegram.

Penggunaan:
  /grafik            → grafik bulan ini (default)
  /grafik hari       → grafik hari ini
  /grafik minggu     → grafik 7 hari terakhir
  /grafik bulan      → grafik bulan ini
  /grafik tahun      → grafik tahun ini
  /grafik 06/2025    → grafik bulan spesifik
"""

import io
import logging

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, aman dipakai di server/bot
import matplotlib.pyplot as plt

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_all_records
from handlers.rekap import _aggregate, resolve_periode, rupiah, EMOJI_KAT

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Pilihan:\n"
    "`/grafik` — bulan ini\n"
    "`/grafik hari` — hari ini\n"
    "`/grafik minggu` — 7 hari terakhir\n"
    "`/grafik bulan` — bulan ini\n"
    "`/grafik tahun` — tahun ini\n"
    "`/grafik 06/2025` — bulan spesifik"
)

# Palet warna konsisten — dipilih supaya enak dilihat & beda-beda cukup jelas
WARNA_PALET = [
    "#FF6B6B", "#4ECDC4", "#FFD93D", "#6A8EAE", "#95D5B2",
    "#C77DFF", "#FF9F1C", "#A0C4FF", "#F694C1", "#B5838D",
]


def _build_pie_chart(per_kategori: dict, judul: str) -> io.BytesIO | None:
    """
    Buat pie chart dari breakdown per kategori (hanya pengeluaran,
    kategori 'pemasukan' sengaja dikecualikan karena beda sifat).

    Args:
        per_kategori: {"makan": {"total": int, "count": int}, ...}
        judul: judul chart, misal "Juli 2026"

    Returns:
        BytesIO berisi PNG, atau None kalau tidak ada data pengeluaran.
    """
    # Keluarkan kategori pemasukan — pie chart ini fokus pengeluaran
    data = {
        kat: val["total"]
        for kat, val in per_kategori.items()
        if kat != "pemasukan" and val["total"] > 0
    }

    if not data:
        return None

    labels = [kat.title() for kat in data.keys()]
    values = list(data.values())
    total  = sum(values)
    warna  = [WARNA_PALET[i % len(WARNA_PALET)] for i in range(len(labels))]

    fig, ax = plt.subplots(figsize=(7, 6.5), dpi=150)

    wedges, _texts, autotexts = ax.pie(
        values,
        colors=warna,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
        pctdistance=0.78,
        startangle=90,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 2},
        textprops={"fontsize": 11, "fontweight": "bold", "color": "white"},
    )

    # Label kategori + nominal di legend, biar tidak numpuk di chart kecil
    legend_labels = [
        f"{lbl} — {rupiah(val)} ({val/total*100:.0f}%)"
        for lbl, val in zip(labels, values)
    ]
    ax.legend(
        wedges, legend_labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=10,
        frameon=False,
    )

    # Teks total di tengah donut
    ax.text(
        0, 0, f"Total\n{rupiah(total)}",
        ha="center", va="center",
        fontsize=13, fontweight="bold",
    )

    ax.set_title(f"Pengeluaran — {judul}", fontsize=15, fontweight="bold", pad=15)
    ax.axis("equal")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf


async def cmd_grafik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /grafik."""
    args = context.args or []
    arg  = args[0].lower().strip() if args else "bulan"

    tgt_bulan = tgt_tahun = None

    if "/" in arg:
        try:
            parts     = arg.split("/")
            tgt_bulan = int(parts[0])
            tgt_tahun = int(parts[1])
            if not (1 <= tgt_bulan <= 12) or tgt_tahun < 2020:
                raise ValueError
            arg = "bulan"
        except (ValueError, IndexError):
            await update.message.reply_text(
                "⚠️ Format bulan tidak valid.\nContoh: `/grafik 06/2025`",
                parse_mode="Markdown",
            )
            return

    VALID = {"hari", "kemarin", "minggu", "bulan", "tahun"}
    if arg not in VALID:
        await update.message.reply_text(
            f"⚠️ Mode `{arg}` tidak dikenal.\n\n{HELP_TEXT}",
            parse_mode="Markdown",
        )
        return

    loading = await update.message.reply_text("📊 Membuat grafik... ⏳")

    try:
        records = await get_all_records()
    except Exception as e:
        logger.error(f"[/grafik] Gagal baca Sheets: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal membaca Google Sheets*\n\n"
            f"`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )
        return

    try:
        start, end, label = resolve_periode(arg, tgt_bulan, tgt_tahun)
        data = _aggregate(records, start, end)

        if data["item_count"] == 0:
            await loading.edit_text(
                f"📊 *Grafik {label}*\n\n_Belum ada transaksi di periode ini._",
                parse_mode="Markdown",
            )
            return

        chart = _build_pie_chart(data["per_kategori"], label)

        if chart is None:
            await loading.edit_text(
                f"📊 *Grafik {label}*\n\n"
                "_Tidak ada data pengeluaran untuk digambar "
                "(mungkin cuma ada pemasukan)._",
                parse_mode="Markdown",
            )
            return

        await update.message.reply_photo(
            photo=chart,
            caption=(
                f"📊 *Breakdown Pengeluaran — {label}*\n"
                f"💰 Total: {rupiah(data['pengeluaran_total'])} "
                f"({data['pengeluaran_count']} transaksi)"
            ),
            parse_mode="Markdown",
        )
        await loading.delete()

    except Exception as e:
        logger.error(f"[/grafik] Error proses/generate chart: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal membuat grafik*\n\n`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.grafik
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Data dummy — tidak perlu koneksi Sheets
    dummy_per_kategori = {
        "makan"     : {"total": 850000, "count": 12},
        "transport" : {"total": 420000, "count": 8},
        "belanja"   : {"total": 300000, "count": 5},
        "hiburan"   : {"total": 150000, "count": 3},
        "kesehatan" : {"total": 90000,  "count": 2},
        "lainnya"   : {"total": 60000,  "count": 2},
        "pemasukan" : {"total": 5000000, "count": 1},  # harus dikecualikan dari pie
    }

    chart = _build_pie_chart(dummy_per_kategori, "Juli 2026 (TEST)")
    if chart:
        with open("test_grafik_output.png", "wb") as f:
            f.write(chart.read())
        print("✅ Chart tersimpan ke test_grafik_output.png")
    else:
        print("❌ Chart gagal dibuat (tidak ada data)")
