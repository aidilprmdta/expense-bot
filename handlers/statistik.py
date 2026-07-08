"""
handlers/statistik.py
─────────────────────
Statistik tahunan komprehensif dengan grafik visual.

/statistik [YYYY]  → 3 output:
  1. Bar chart bulanan (pengeluaran + pemasukan per bulan)
  2. Pie chart kategori pengeluaran tahun ini
  3. Pesan teks: insight, tren, rekor, perbandingan

Reuse: _aggregate(), _parse_tgl(), _safe_int(), rupiah(), NAMA_BULAN, EMOJI_KAT
       dari handlers/rekap.py (tidak duplikasi kode)
"""

import io
import os
import logging
from datetime import date, datetime, timedelta
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")   # wajib untuk server/bot (non-interactive)
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from telegram import Update, InputMediaPhoto
from telegram.ext import ContextTypes

from handlers.sheets import get_all_records
from handlers.rekap import (
    _parse_tgl, _safe_int, rupiah,
    NAMA_BULAN, EMOJI_KAT,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# WARNA GRAFIK
# ─────────────────────────────────────────────────────────────

COLOR_EXPENSE = "#E05252"     # merah untuk pengeluaran
COLOR_INCOME  = "#4CAF7D"     # hijau untuk pemasukan
COLOR_BG      = "#1C1C1E"     # background gelap (cocok untuk Telegram dark mode)
COLOR_TEXT    = "#E8E8E8"
COLOR_GRID    = "#333333"
COLOR_ACCENT  = "#5B9BD5"

WARNA_KATEGORI = [
    "#E05252", "#F5A623", "#4CAF7D", "#5B9BD5",
    "#9B59B6", "#1ABC9C", "#E67E22", "#95A5A6",
]

# ─────────────────────────────────────────────────────────────
# AGREGASI TAHUNAN (per bulan)
# ─────────────────────────────────────────────────────────────

def _agregasi_tahunan(records: list[dict], tahun: int) -> dict:
    """
    Hitung pengeluaran & pemasukan per bulan + breakdown kategori tahunan.

    Returns:
      {
        "per_bulan": {
            1: {"pengeluaran": int, "pemasukan": int, "count": int},
            ...
            12: {...}
        },
        "per_kategori": {"makan": int, "transport": int, ...},
        "total_keluar" : int,
        "total_masuk"  : int,
        "top_expense"  : {"nama": str, "harga": int, "tanggal": str},   # transaksi terbesar
        "hari_terboros": {"tanggal": str, "total": int},                 # hari paling boros
      }
    """
    per_bulan: dict[int, dict] = {
        m: {"pengeluaran": 0, "pemasukan": 0, "count": 0}
        for m in range(1, 13)
    }
    per_kat: dict[str, int] = defaultdict(int)
    total_keluar = total_masuk = 0
    top_expense  = {"nama": "-", "harga": 0, "tanggal": "-"}
    per_hari: dict[str, int] = defaultdict(int)

    for r in records:
        tgl = _parse_tgl(str(r.get("Tanggal", "")))
        if tgl is None or tgl.year != tahun:
            continue

        harga = _safe_int(r.get("Harga", 0))
        kat   = str(r.get("Kategori", "lainnya")).lower().strip()
        nama  = str(r.get("Nama Item", "")).strip()

        per_bulan[tgl.month]["count"] += 1

        if kat == "pemasukan":
            per_bulan[tgl.month]["pemasukan"] += harga
            total_masuk += harga
        else:
            per_bulan[tgl.month]["pengeluaran"] += harga
            total_keluar += harga
            per_kat[kat] += harga
            per_hari[tgl.strftime("%d/%m/%Y")] += harga

            # Track transaksi terbesar
            if harga > top_expense["harga"]:
                top_expense = {
                    "nama"   : nama,
                    "harga"  : harga,
                    "tanggal": tgl.strftime("%d %b %Y"),
                }

    # Hari paling boros
    hari_terboros = {"tanggal": "-", "total": 0}
    if per_hari:
        tgl_str, total = max(per_hari.items(), key=lambda x: x[1])
        hari_terboros = {"tanggal": tgl_str, "total": total}

    return {
        "per_bulan"    : per_bulan,
        "per_kategori" : dict(sorted(per_kat.items(), key=lambda x: -x[1])),
        "total_keluar" : total_keluar,
        "total_masuk"  : total_masuk,
        "top_expense"  : top_expense,
        "hari_terboros": hari_terboros,
    }


# ─────────────────────────────────────────────────────────────
# CHART 1 — BAR CHART BULANAN
# ─────────────────────────────────────────────────────────────

def _buat_bar_chart(data: dict, tahun: int) -> bytes:
    """
    Bar chart side-by-side: pengeluaran (merah) vs pemasukan (hijau) per bulan.
    Return: PNG bytes.
    """
    bulan_labels = [n[:3] for n in NAMA_BULAN[1:]]   # Jan, Feb, ..., Des
    pengeluaran  = [data["per_bulan"][m]["pengeluaran"] / 1_000 for m in range(1, 13)]
    pemasukan    = [data["per_bulan"][m]["pemasukan"]   / 1_000 for m in range(1, 13)]

    x     = np.arange(12)
    width = 0.4

    fig, ax = plt.subplots(figsize=(12, 5))
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)

    bars_k = ax.bar(x - width/2, pengeluaran, width, label="Pengeluaran",
                    color=COLOR_EXPENSE, alpha=0.9, zorder=3)
    bars_m = ax.bar(x + width/2, pemasukan,   width, label="Pemasukan",
                    color=COLOR_INCOME,  alpha=0.9, zorder=3)

    # Label nilai di atas bar (tampilkan hanya jika > 0)
    for bar in bars_k:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width()/2, h + 5, f"{h:.0f}k",
                    ha="center", va="bottom", fontsize=7, color=COLOR_TEXT, fontweight="bold")
    for bar in bars_m:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width()/2, h + 5, f"{h:.0f}k",
                    ha="center", va="bottom", fontsize=7, color=COLOR_INCOME, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(bulan_labels, color=COLOR_TEXT, fontsize=10)
    ax.yaxis.set_tick_params(labelcolor=COLOR_TEXT)
    ax.set_ylabel("Ribu Rupiah (rb)", color=COLOR_TEXT, fontsize=9)
    ax.set_title(f"💰 Arus Keuangan {tahun} — per Bulan",
                 color=COLOR_TEXT, fontsize=13, fontweight="bold", pad=12)
    ax.legend(facecolor=COLOR_BG, edgecolor=COLOR_GRID,
              labelcolor=COLOR_TEXT, fontsize=9)
    ax.grid(axis="y", color=COLOR_GRID, linestyle="--", alpha=0.5, zorder=0)
    ax.spines[:].set_color(COLOR_GRID)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=COLOR_BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────
# CHART 2 — PIE CHART KATEGORI
# ─────────────────────────────────────────────────────────────

def _buat_pie_chart(data: dict, tahun: int) -> bytes | None:
    """
    Donut chart breakdown pengeluaran per kategori.
    Return: PNG bytes, atau None jika tidak ada data.
    """
    per_kat = {k: v for k, v in data["per_kategori"].items() if v > 0}
    if not per_kat:
        return None

    labels  = [k.title() for k in per_kat.keys()]
    values  = list(per_kat.values())
    colors  = WARNA_KATEGORI[:len(labels)]

    fig, ax = plt.subplots(figsize=(8, 6))
    fig.patch.set_facecolor(COLOR_BG)
    ax.set_facecolor(COLOR_BG)

    wedges, texts, autotexts = ax.pie(
        values,
        labels=None,
        colors=colors,
        autopct=lambda pct: f"{pct:.1f}%" if pct > 4 else "",
        startangle=140,
        wedgeprops={"width": 0.55, "edgecolor": COLOR_BG, "linewidth": 2},
        pctdistance=0.75,
    )
    for at in autotexts:
        at.set_color(COLOR_TEXT)
        at.set_fontsize(9)
        at.set_fontweight("bold")

    # Legend dengan nilai Rupiah
    legend_labels = [
        f"{lbl}  —  {rupiah(val)}"
        for lbl, val in zip(labels, values)
    ]
    legend = ax.legend(
        wedges, legend_labels,
        loc="lower center", ncol=2,
        bbox_to_anchor=(0.5, -0.15),
        facecolor=COLOR_BG, edgecolor=COLOR_GRID,
        labelcolor=COLOR_TEXT, fontsize=9,
    )

    # Teks total di tengah donut
    total_str = rupiah(sum(values))
    ax.text(0, 0.08, "Total", ha="center", va="center",
            color=COLOR_TEXT, fontsize=10)
    ax.text(0, -0.12, total_str, ha="center", va="center",
            color=COLOR_EXPENSE, fontsize=12, fontweight="bold")

    ax.set_title(f"🏷️ Breakdown Kategori {tahun}",
                 color=COLOR_TEXT, fontsize=13, fontweight="bold", pad=12)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=COLOR_BG)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────
# PESAN TEKS STATISTIK
# ─────────────────────────────────────────────────────────────

def _buat_pesan_statistik(data: dict, tahun: int, records: list[dict]) -> str:
    """Buat pesan teks ringkasan statistik tahunan."""
    now  = datetime.now()
    SEP  = "━" * 26

    total_k = data["total_keluar"]
    total_m = data["total_masuk"]
    saldo   = total_m - total_k

    # Bulan aktif (ada transaksi)
    bulan_aktif = [
        m for m, v in data["per_bulan"].items()
        if v["pengeluaran"] > 0 or v["pemasukan"] > 0
    ]
    n_bulan = len(bulan_aktif) or 1

    avg_bulan   = total_k // n_bulan
    peak_bulan  = max(data["per_bulan"].items(), key=lambda x: x[1]["pengeluaran"])
    hemat_bulan = min(
        ((m, v) for m, v in data["per_bulan"].items() if v["pengeluaran"] > 0),
        key=lambda x: x[1]["pengeluaran"],
        default=(0, {"pengeluaran": 0}),
    )
    top_kat = next(iter(data["per_kategori"]), "-")
    top_kat_val = data["per_kategori"].get(top_kat, 0)

    # Saldo icon
    saldo_icon = "✅" if saldo >= 0 else "🔴"
    saldo_str  = f"+{rupiah(saldo)}" if saldo >= 0 else f"-{rupiah(abs(saldo))}"

    baris = [
        f"📊 *Statistik Keuangan {tahun}*",
        SEP,
        "",
        "💸 *Ringkasan*",
        f"  Total keluar  : {rupiah(total_k)}",
        f"  Total masuk   : {rupiah(total_m)}",
        f"  {saldo_icon} Saldo bersih : *{saldo_str}*",
        "",
        "📅 *Bulanan*",
        f"  Rata-rata/bulan : {rupiah(avg_bulan)}",
        f"  Bulan terboros  : {NAMA_BULAN[peak_bulan[0]]} ({rupiah(peak_bulan[1]['pengeluaran'])})",
    ]

    if hemat_bulan[0] > 0:
        baris.append(
            f"  Bulan terhemat  : {NAMA_BULAN[hemat_bulan[0]]} ({rupiah(hemat_bulan[1]['pengeluaran'])})"
        )

    if top_kat != "-":
        emoji = EMOJI_KAT.get(top_kat, "📌")
        baris += [
            "",
            "🏷️ *Kategori Terbesar*",
            f"  {emoji} {top_kat.title()} — {rupiah(top_kat_val)}",
            f"  ({round(top_kat_val/total_k*100) if total_k else 0}% dari total pengeluaran)",
        ]

    if data["top_expense"]["harga"] > 0:
        te = data["top_expense"]
        baris += [
            "",
            "🔺 *Transaksi Terbesar*",
            f"  {te['nama']} — {rupiah(te['harga'])}",
            f"  {te['tanggal']}",
        ]

    if data["hari_terboros"]["total"] > 0:
        ht = data["hari_terboros"]
        baris += [
            "",
            "🔥 *Hari Paling Boros*",
            f"  {ht['tanggal']} — {rupiah(ht['total'])}",
        ]

    # Proyeksi jika tahun berjalan
    if tahun == now.year and now.month < 12:
        sisa_bulan = 12 - now.month
        proyeksi = total_k + (avg_bulan * sisa_bulan)
        baris += [
            "",
            SEP,
            f"📈 *Proyeksi Akhir Tahun*",
            f"  Estimasi total : {rupiah(proyeksi)}",
            f"  (jika tren {rupiah(avg_bulan)}/bulan berlanjut)",
        ]

    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# COMMAND HANDLER
# ─────────────────────────────────────────────────────────────

async def cmd_statistik(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /statistik [YYYY]
    Tampilkan statistik tahunan: bar chart, pie chart, + ringkasan teks.
    """
    now   = datetime.now()
    args  = context.args or []

    # Parse tahun dari argumen
    tahun = now.year
    if args:
        try:
            tahun = int(args[0])
            if not (2020 <= tahun <= now.year + 1):
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "Format salah. Gunakan: `/statistik` atau `/statistik 2025`",
                parse_mode="Markdown",
            )
            return

    msg = await update.message.reply_text(
        f"📊 Menyusun statistik {tahun}... ⏳\n"
        "_Membuat grafik, harap tunggu sebentar._",
        parse_mode="Markdown",
    )

    try:
        records = await get_all_records()
        data    = _agregasi_tahunan(records, tahun)

        # Cek ada data atau tidak
        if data["total_keluar"] == 0 and data["total_masuk"] == 0:
            await msg.edit_text(
                f"📊 Tidak ada data transaksi di tahun *{tahun}*.",
                parse_mode="Markdown",
            )
            return

        # Generate charts
        bar_png = _buat_bar_chart(data, tahun)
        pie_png = _buat_pie_chart(data, tahun)
        pesan   = _buat_pesan_statistik(data, tahun, records)

        # Kirim chart-chart sebagai album foto
        media = [InputMediaPhoto(media=bar_png, caption=f"Bar chart bulanan {tahun}")]
        if pie_png:
            media.append(InputMediaPhoto(media=pie_png, caption=f"Pie chart kategori {tahun}"))

        await update.message.reply_media_group(media=media)

        # Kirim pesan teks statistik
        await update.message.reply_text(pesan, parse_mode="Markdown")

        # Hapus pesan loading
        await msg.delete()

    except Exception as e:
        logger.error(f"[statistik] Error: {e}", exc_info=True)
        await msg.edit_text(
            f"😔 Gagal membuat statistik.\n`{type(e).__name__}: {str(e)[:100]}`",
            parse_mode="Markdown",
        )
