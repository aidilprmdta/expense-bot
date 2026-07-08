"""
handlers/anomaly.py
───────────────────
Deteksi pengeluaran tidak wajar secara otomatis.

Dipanggil setelah setiap transaksi baru disimpan ke Sheets.
Menggunakan kombinasi Z-score statistik + ambang batas adaptif per kategori.

Fungsi publik:
  check_and_alert(update, context, new_items, all_records)
    → kirim pesan peringatan ke user jika ada anomali

Algoritma (3 lapis):
  1. Z-score kategori  — bandingkan dengan rata-rata historis per kategori
  2. Daily spike        — total hari ini > 3× rata-rata harian
  3. Absolute threshold — nilai mutlak per kategori (fallback jika data sedikit)
"""

import logging
import statistics
from datetime import date, datetime, timedelta
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

from handlers.rekap import _parse_tgl, _safe_int, rupiah

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# KONFIGURASI
# ─────────────────────────────────────────────────────────────

# Minimal transaksi historis sebelum Z-score diaktifkan
MIN_HISTORIS = 5

# Z-score ambang: nilai berapa sigma di atas rata-rata dianggap anomali
ZSCORE_THRESHOLD = 2.2

# Ambang absolut per kategori (Rupiah) — dipakai jika data historis kurang
THRESHOLD_ABSOLUT: dict[str, int] = {
    "makan"    : 300_000,    # > 300rb sekali makan → janggal
    "transport": 400_000,    # > 400rb sekali transport → janggal
    "belanja"  : 1_500_000,  # > 1.5jt sekali belanja → janggal
    "kesehatan": 1_000_000,  # > 1jt sekali kesehatan → janggal
    "hiburan"  : 800_000,    # > 800rb sekali hiburan → janggal
    "lainnya"  : 2_000_000,  # > 2jt lainnya → janggal
}

# Jendela historis: berapa hari ke belakang yang dipakai sebagai referensi
JENDELA_HARI = 60

# Rasio spike harian: berapa kali lipat rata-rata agar dianggap spike
DAILY_SPIKE_RATIO = 3.0

# Minimal rata-rata harian agar daily spike check aktif (hindari false positive)
MIN_DAILY_AVG = 50_000   # 50rb

# ─────────────────────────────────────────────────────────────
# HELPER STATISTIK
# ─────────────────────────────────────────────────────────────

def _hitung_zscore(nilai: float, data_historis: list[float]) -> float | None:
    """
    Hitung Z-score nilai terhadap distribusi historis.
    Return None jika std = 0 atau data terlalu sedikit.
    """
    if len(data_historis) < MIN_HISTORIS:
        return None
    try:
        mean = statistics.mean(data_historis)
        std  = statistics.stdev(data_historis)
        if std == 0:
            return None
        return (nilai - mean) / std
    except statistics.StatisticsError:
        return None


def _ambil_historis_kategori(
    records: list[dict],
    kategori: str,
    exclude_today: bool = True,
    jendela_hari: int = JENDELA_HARI,
) -> list[int]:
    """
    Ambil daftar harga historis untuk satu kategori dalam jendela waktu.
    """
    batas_bawah = date.today() - timedelta(days=jendela_hari)
    today       = date.today()
    hasil       = []

    for r in records:
        tgl = _parse_tgl(str(r.get("Tanggal", "")))
        if tgl is None or tgl < batas_bawah:
            continue
        if exclude_today and tgl == today:
            continue
        kat = str(r.get("Kategori", "")).lower().strip()
        if kat != kategori.lower():
            continue
        harga = _safe_int(r.get("Harga", 0))
        if harga > 0:
            hasil.append(harga)

    return hasil


def _hitung_total_harian(records: list[dict], tgl: date) -> int:
    """Hitung total pengeluaran pada tanggal tertentu (exclude pemasukan)."""
    total = 0
    for r in records:
        r_tgl = _parse_tgl(str(r.get("Tanggal", "")))
        if r_tgl != tgl:
            continue
        kat = str(r.get("Kategori", "")).lower()
        if kat == "pemasukan":
            continue
        total += _safe_int(r.get("Harga", 0))
    return total


def _avg_harian(records: list[dict], jendela_hari: int = JENDELA_HARI) -> float:
    """
    Rata-rata pengeluaran per hari aktif dalam jendela waktu.
    Hari aktif = hari yang ada minimal 1 transaksi pengeluaran.
    """
    batas = date.today() - timedelta(days=jendela_hari)
    today = date.today()
    per_hari: dict[date, int] = defaultdict(int)

    for r in records:
        tgl = _parse_tgl(str(r.get("Tanggal", "")))
        if tgl is None or tgl < batas or tgl >= today:
            continue
        kat = str(r.get("Kategori", "")).lower()
        if kat == "pemasukan":
            continue
        per_hari[tgl] += _safe_int(r.get("Harga", 0))

    if not per_hari:
        return 0.0
    return statistics.mean(per_hari.values())


# ─────────────────────────────────────────────────────────────
# DETEKSI ANOMALI PER ITEM
# ─────────────────────────────────────────────────────────────

def _cek_item(item: dict, records: list[dict]) -> dict | None:
    """
    Cek apakah satu item transaksi dianggap anomali.

    Returns dict {"level": "warning"|"danger", "pesan": str} atau None.
    """
    harga    = _safe_int(item.get("harga", 0))
    kategori = str(item.get("kategori", "")).lower()
    nama     = str(item.get("nama", "item")).title()

    if harga <= 0 or kategori == "pemasukan":
        return None

    # ── Layer 1: Z-score vs historis kategori ─────────────────
    historis  = _ambil_historis_kategori(records, kategori)
    z_score   = _hitung_zscore(harga, historis)

    if z_score is not None and z_score >= ZSCORE_THRESHOLD:
        avg     = statistics.mean(historis)
        berapa  = round(z_score, 1)
        level   = "danger" if z_score >= 3.5 else "warning"
        return {
            "level": level,
            "tipe" : "zscore",
            "pesan": (
                f"*{nama}* ({rupiah(harga)}) "
                f"*{berapa}×* di atas rata-rata {kategori.title()}mu "
                f"({rupiah(int(avg))}/trx dalam {JENDELA_HARI} hari terakhir)"
            ),
        }

    # ── Layer 2: Absolute threshold (fallback) ─────────────────
    batas = THRESHOLD_ABSOLUT.get(kategori, 2_000_000)
    if harga >= batas and len(historis) < MIN_HISTORIS:
        return {
            "level": "warning",
            "tipe" : "absolute",
            "pesan": (
                f"*{nama}* ({rupiah(harga)}) cukup besar "
                f"untuk kategori *{kategori.title()}*"
            ),
        }

    return None


# ─────────────────────────────────────────────────────────────
# DETEKSI SPIKE HARIAN
# ─────────────────────────────────────────────────────────────

def _cek_daily_spike(records: list[dict]) -> dict | None:
    """
    Cek apakah total pengeluaran hari ini jauh di atas rata-rata harian.
    """
    total_hari_ini = _hitung_total_harian(records, date.today())
    avg_harian     = _avg_harian(records)

    if avg_harian < MIN_DAILY_AVG or total_hari_ini == 0:
        return None

    rasio = total_hari_ini / avg_harian
    if rasio >= DAILY_SPIKE_RATIO:
        return {
            "level": "warning",
            "tipe" : "daily_spike",
            "pesan": (
                f"Total pengeluaran hari ini *{rupiah(total_hari_ini)}* "
                f"sudah *{rasio:.1f}×* lebih besar dari rata-rata harian "
                f"({rupiah(int(avg_harian))})"
            ),
        }
    return None


# ─────────────────────────────────────────────────────────────
# FORMAT PESAN ALERT
# ─────────────────────────────────────────────────────────────

def _format_alert(item_alerts: list[dict], daily_alert: dict | None) -> str:
    """Gabungkan semua alert jadi satu pesan Telegram."""
    baris = ["⚠️ *Pengeluaran Tidak Wajar Terdeteksi!*\n"]

    for a in item_alerts:
        icon = "🚨" if a["level"] == "danger" else "⚠️"
        baris.append(f"{icon} {a['pesan']}")

    if daily_alert:
        baris.append(f"\n📈 {daily_alert['pesan']}")

    baris += [
        "",
        "_Ini otomatis tercatat di Sheets. Pastikan ini memang benar!_",
        "_Balas /hapus terakhir jika salah input._",
    ]
    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# FUNGSI UTAMA (dipanggil dari handle_teks dan handle_foto)
# ─────────────────────────────────────────────────────────────

async def check_and_alert(
    update  : Update,
    context : ContextTypes.DEFAULT_TYPE,
    new_items: list[dict],
    all_records: list[dict],
) -> None:
    """
    Cek anomali dan kirim pesan peringatan jika ada.

    Args:
        update      : Telegram Update object
        new_items   : list item baru yang baru disimpan (dari parse_expense / ocr_struk)
        all_records : semua records dari get_all_records() — TERMASUK item baru
    """
    try:
        item_alerts = []
        for item in new_items:
            alert = _cek_item(item, all_records)
            if alert:
                item_alerts.append(alert)

        daily_alert = _cek_daily_spike(all_records)

        if item_alerts or daily_alert:
            pesan = _format_alert(item_alerts, daily_alert)
            await update.message.reply_text(pesan, parse_mode="Markdown")
            logger.info(
                f"[anomaly] Alert dikirim: {len(item_alerts)} item + "
                f"{'1 daily spike' if daily_alert else 'no spike'}"
            )

    except Exception as e:
        # Jangan sampai error anomaly menghentikan flow utama
        logger.warning(f"[anomaly] Error saat cek anomali (diabaikan): {e}")
