"""
handlers/rekap.py
─────────────────
Command /rekap untuk melihat ringkasan pengeluaran dari Google Sheets.

Penggunaan:
  /rekap            → hari ini + bulan ini (default)
  /rekap hari       → hari ini saja
  /rekap kemarin    → kemarin
  /rekap minggu     → 7 hari terakhir
  /rekap bulan      → bulan ini
  /rekap tahun      → tahun ini
  /rekap 06/2025    → bulan spesifik
"""

import os
import calendar
import logging
from datetime import date, datetime, timedelta
from collections import defaultdict

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import get_all_records, get_budget, set_budget

logger = logging.getLogger(__name__)

EMOJI_KAT: dict[str, str] = {
    "makan"    : "🍽️",
    "transport": "🚗",
    "belanja"  : "🛒",
    "kesehatan": "💊",
    "hiburan"  : "🎮",
    "pemasukan": "💰",
    "lainnya"  : "📌",
}

NAMA_BULAN = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

NAMA_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

SEP = "━" * 28

HELP_TEXT = (
    "Pilihan:\n"
    "`/rekap` — hari ini + bulan ini\n"
    "`/rekap hari` — hari ini\n"
    "`/rekap kemarin` — kemarin\n"
    "`/rekap minggu` — 7 hari terakhir\n"
    "`/rekap bulan` — bulan ini\n"
    "`/rekap tahun` — tahun ini\n"
    "`/rekap 06/2025` — bulan spesifik"
)


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def rupiah(n: int) -> str:
    return f"Rp {n:,}".replace(",", ".")


def _safe_int(v) -> int:
    """
    Parse nilai Harga dari Sheets ke int — tahan terhadap semua format.
    numericise_ignore=["all"] di sheets.py membuat semua nilai return string.
    Google Sheets locale Indonesia bisa format angka '25.000' (bukan '25000').
    """
    if isinstance(v, (int, float)):
        return max(0, int(v))
    try:
        # Hapus semua karakter non-digit kecuali minus
        cleaned = str(v).strip().replace("Rp", "").replace(" ", "")
        # Tangani format ribuan dengan titik: "25.000" → "25000"
        # Deteksi: jika ada titik tapi tidak ada koma, titik = separator ribuan
        if "." in cleaned and "," not in cleaned:
            parts = cleaned.split(".")
            if all(len(p) <= 3 for p in parts[1:]):  # pola ribuan
                cleaned = cleaned.replace(".", "")
        cleaned = cleaned.replace(",", "")
        return max(0, int(float(cleaned)))
    except (ValueError, TypeError):
        return 0


def _bar(persen: int, lebar: int = 20) -> str:
    """Buat progress bar teks. Contoh (30%): ██████░░░░░░░░░░░░░░ 30%"""
    persen  = max(0, min(100, persen))
    terisi  = round(persen / 100 * lebar)
    return f"{'█' * terisi}{'░' * (lebar - terisi)} {persen}%"


def _mini_bar(persen: int, lebar: int = 8) -> str:
    """Mini bar 8 karakter untuk tabel kategori. 39% → ███░░░░░"""
    terisi = round(persen / 100 * lebar)
    return "█" * terisi + "░" * (lebar - terisi)


def _fmt_tgl(tgl: date) -> str:
    """date → 'Rabu, 26 Jun 2025'"""
    bln = ["","Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
    return f"{NAMA_HARI[tgl.weekday()]}, {tgl.day} {bln[tgl.month]} {tgl.year}"


def _parse_tgl(s: str) -> date | None:
    if not s:
        return None
        
    s = str(s).strip()
    
    # PERCOBAAN 1: Membaca angka mentah Google Sheets (Misal: "46208")
    if s.isdigit():
        try:
            # Mengubah angka serial (hari sejak 30 Des 1899) menjadi tanggal Masehi
            return (datetime(1899, 12, 30) + timedelta(days=int(s))).date()
        except Exception:
            pass

    # PERCOBAAN 2: Membaca format DD/MM/YYYY (Misal: 05/07/2026)
    try:
        return datetime.strptime(s, "%d/%m/%Y").date()
    except ValueError:
        pass
        
    # PERCOBAAN 3: Membaca format YYYY-MM-DD (Misal: 2026-07-05)
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        pass
        
    return None

# ─────────────────────────────────────────────────────────────
# AGREGASI
# ─────────────────────────────────────────────────────────────

def _aggregate(records: list[dict], start: date, end: date) -> dict:
    """
    Hitung total + breakdown per kategori untuk records dalam [start, end].

    Records dari get_all_records() punya keys:
      Tanggal, Nama Item, Kategori, Harga, Catatan

    Returns:
      {
        "pemasukan_total": int,
        "pemasukan_count": int,
        "pengeluaran_total": int,
        "pengeluaran_count": int,
        "total"       : int,
        "item_count"  : int,
        "per_kategori": {"makan": {"total": int, "count": int}, ...}
      }
    """
    per_kat: dict = defaultdict(lambda: {"total": 0, "count": 0})
    pemasukan_total = pemasukan_count = 0
    pengeluaran_total = pengeluaran_count = 0

    for r in records:
        tgl = _parse_tgl(str(r.get("Tanggal", "")))
        if tgl is None or not (start <= tgl <= end):
            continue

        try:
            harga = _safe_int(r.get("Harga", 0))
        except (ValueError, TypeError):
            harga = 0

        kat = str(r.get("Kategori", "Lainnya")).lower().strip()
        if not kat:
            kat = "lainnya"

        per_kat[kat]["total"] += harga
        per_kat[kat]["count"] += 1

        if kat == "pemasukan":
            pemasukan_total += harga
            pemasukan_count += 1
        else:
            pengeluaran_total += harga
            pengeluaran_count += 1

    sorted_kat = dict(
        sorted(per_kat.items(), key=lambda x: x[1]["total"], reverse=True)
    )
    return {
        "pemasukan_total": pemasukan_total,
        "pemasukan_count": pemasukan_count,
        "pengeluaran_total": pengeluaran_total,
        "pengeluaran_count": pengeluaran_count,
        "total": pengeluaran_total,
        "item_count": pemasukan_count + pengeluaran_count,
        "per_kategori": sorted_kat
    }


# ─────────────────────────────────────────────────────────────
# FORMAT SECTIONS
# ─────────────────────────────────────────────────────────────

def _fmt_hari(data: dict, tgl: date, label: str = "Hari Ini") -> str:
    """
    Format rekap satu hari terpisah pemasukan dan pengeluaran.
    """
    header = f"📅 *{label}* — {_fmt_tgl(tgl)}"

    if data["item_count"] == 0:
        return f"{header}\n\n_Belum ada transaksi._"

    baris = [header, ""]

    # 📥 Pemasukan
    baris.append("📥 *Pemasukan*")
    pemasukan_items = []
    for kat, val in data["per_kategori"].items():
        if kat == "pemasukan" and val["total"] > 0:
            emoji = EMOJI_KAT.get(kat, "💰")
            nama  = kat.title().ljust(12)
            hrg   = rupiah(val["total"])
            pemasukan_items.append(f"{emoji}  {nama}  {hrg}")

    if pemasukan_items:
        baris.extend(pemasukan_items)
        baris.append(f"Total Pemasukan: *{rupiah(data['pemasukan_total'])}* ({data['pemasukan_count']} trx)")
    else:
        baris.append("_Tidak ada pemasukan._")

    baris.append("")

    # 💸 Pengeluaran
    baris.append("💸 *Pengeluaran*")
    pengeluaran_items = []
    for kat, val in data["per_kategori"].items():
        if kat != "pemasukan" and val["total"] > 0:
            emoji = EMOJI_KAT.get(kat, "📌")
            nama  = kat.title().ljust(12)
            hrg   = rupiah(val["total"])
            pengeluaran_items.append(f"{emoji}  {nama}  {hrg}")

    if pengeluaran_items:
        baris.extend(pengeluaran_items)
        baris.append(f"Total Pengeluaran: *{rupiah(data['pengeluaran_total'])}* ({data['pengeluaran_count']} trx)")
    else:
        baris.append("_Tidak ada pengeluaran._")

    # Selisih PERIODE INI (bukan saldo total kumulatif)
    baris.append("")
    baris.append("━" * 28)
    net_saldo = data["pemasukan_total"] - data["pengeluaran_total"]
    if net_saldo >= 0:
        baris.append(f"⚖️ Selisih periode ini: *+{rupiah(net_saldo)}*")
    else:
        baris.append(f"⚖️ Selisih periode ini: *-{rupiah(abs(net_saldo))}*")

    return "\n".join(baris)


def _fmt_bulan(data: dict, bulan: int, tahun: int, budget: int = 0) -> str:
    """
    Format rekap bulanan dengan persentase + mini bar + info budget.
    """
    header = f"📅 *Bulan Ini* — {NAMA_BULAN[bulan]} {tahun}"

    if data["item_count"] == 0:
        return f"{header}\n\n_Belum ada transaksi di {NAMA_BULAN[bulan]} {tahun}._"

    baris = [header, ""]

    # 📥 Pemasukan
    baris.append("📥 *Pemasukan*")
    pemasukan_items = []
    pem_total = data["pemasukan_total"]
    for kat, val in data["per_kategori"].items():
        if kat == "pemasukan" and val["total"] > 0:
            emoji  = EMOJI_KAT.get(kat, "💰")
            persen = round(val["total"] / pem_total * 100) if pem_total else 0
            mini   = _mini_bar(persen)
            nama   = kat.title().ljust(10)
            hrg    = rupiah(val["total"])
            pemasukan_items.append(f"{emoji}  {nama}  ({persen:>2}%)  {hrg}  {mini}")

    if pemasukan_items:
        baris.extend(pemasukan_items)
        baris.append(f"Total Pemasukan: *{rupiah(pem_total)}* ({data['pemasukan_count']} trx)")
    else:
        baris.append("_Tidak ada pemasukan._")

    baris.append("")

    # 💸 Pengeluaran
    baris.append("💸 *Pengeluaran*")
    pengeluaran_items = []
    peng_total = data["pengeluaran_total"]
    for kat, val in data["per_kategori"].items():
        if kat != "pemasukan" and val["total"] > 0:
            emoji  = EMOJI_KAT.get(kat, "📌")
            persen = round(val["total"] / peng_total * 100) if peng_total else 0
            mini   = _mini_bar(persen)
            nama   = kat.title().ljust(10)
            hrg    = rupiah(val["total"])
            pengeluaran_items.append(f"{emoji}  {nama}  ({persen:>2}%)  {hrg}  {mini}")

    if pengeluaran_items:
        baris.extend(pengeluaran_items)
        baris.append(f"Total Pengeluaran: *{rupiah(peng_total)}* ({data['pengeluaran_count']} trx)")
    else:
        baris.append("_Tidak ada pengeluaran._")

    # Selisih PERIODE INI (bukan saldo total kumulatif)
    baris.append("")
    baris.append("━" * 28)
    net_saldo = pem_total - peng_total
    if net_saldo >= 0:
        baris.append(f"⚖️ Selisih bulan ini: *+{rupiah(net_saldo)}*")
    else:
        baris.append(f"⚖️ Selisih bulan ini: *-{rupiah(abs(net_saldo))}*")

    # Budget block
    if budget > 0:
        terpakai_pct = min(100, round(peng_total / budget * 100)) if budget else 0
        sisa         = budget - peng_total
        pct_sisa     = 100 - terpakai_pct

        baris += [
            "",
            "─" * 28,
            f"🎯 Budget Pengeluaran: *{rupiah(budget)}*",
            _bar(terpakai_pct),
        ]
        if sisa >= 0:
            baris.append(f"✅ *Tersisa dari budget: {rupiah(sisa)}* ({pct_sisa}%)")
        else:
            baris.append(f"⚠️ *Over budget {rupiah(abs(sisa))}!*")

        # Saldo = Budget - Pengeluaran + Pemasukan bulan ini
        saldo = budget - peng_total + pem_total
        emoji_saldo = "💰" if saldo >= 0 else "📛"
        baris.append(f"{emoji_saldo} *Saldo (Budget + Pemasukan - Pengeluaran): {rupiah(saldo)}*")

    return "\n".join(baris)


def _fmt_tahun(data: dict, tahun: int) -> str:
    """
    Format rekap tahunan.
    """
    header = f"📅 *Tahun Ini* — {tahun}"

    if data["item_count"] == 0:
        return f"{header}\n\n_Belum ada data tahun {tahun}._"

    baris = [header, ""]

    # 📥 Pemasukan
    baris.append("📥 *Pemasukan*")
    pemasukan_items = []
    pem_total = data["pemasukan_total"]
    for kat, val in data["per_kategori"].items():
        if kat == "pemasukan" and val["total"] > 0:
            emoji  = EMOJI_KAT.get(kat, "💰")
            persen = round(val["total"] / pem_total * 100) if pem_total else 0
            mini   = _mini_bar(persen)
            nama   = kat.title().ljust(10)
            hrg    = rupiah(val["total"])
            pemasukan_items.append(f"{emoji}  {nama}  ({persen:>2}%)  {hrg}  {mini}")

    if pemasukan_items:
        baris.extend(pemasukan_items)
        baris.append(f"Total Pemasukan: *{rupiah(pem_total)}* ({data['pemasukan_count']} trx)")
    else:
        baris.append("_Tidak ada pemasukan._")

    baris.append("")

    # 💸 Pengeluaran
    baris.append("💸 *Pengeluaran*")
    pengeluaran_items = []
    peng_total = data["pengeluaran_total"]
    for kat, val in data["per_kategori"].items():
        if kat != "pemasukan" and val["total"] > 0:
            emoji  = EMOJI_KAT.get(kat, "📌")
            persen = round(val["total"] / peng_total * 100) if peng_total else 0
            mini   = _mini_bar(persen)
            nama   = kat.title().ljust(10)
            hrg    = rupiah(val["total"])
            pengeluaran_items.append(f"{emoji}  {nama}  ({persen:>2}%)  {hrg}  {mini}")

    if pengeluaran_items:
        baris.extend(pengeluaran_items)
        baris.append(f"Total Pengeluaran: *{rupiah(peng_total)}* ({data['pengeluaran_count']} trx)")
    else:
        baris.append("_Tidak ada pengeluaran._")

    # Selisih PERIODE INI
    baris.append("")
    baris.append("━" * 28)
    net_saldo = pem_total - peng_total
    if net_saldo >= 0:
        baris.append(f"⚖️ Selisih tahun ini: *+{rupiah(net_saldo)}*")
    else:
        baris.append(f"⚖️ Selisih tahun ini: *-{rupiah(abs(net_saldo))}*")

    return "\n".join(baris)


def _fmt_minggu(data: dict) -> str:
    """Format rekap 7 hari terakhir."""
    today  = date.today()
    start  = today - timedelta(days=6)
    bln    = ["","Jan","Feb","Mar","Apr","Mei","Jun","Jul","Agu","Sep","Okt","Nov","Des"]
    header = (
        f"📅 *7 Hari Terakhir*\n"
        f"_{start.day} {bln[start.month]} – "
        f"{today.day} {bln[today.month]} {today.year}_"
    )

    if data["item_count"] == 0:
        return f"{header}\n\n_Belum ada transaksi 7 hari terakhir._"

    baris = [header, ""]

    # 📥 Pemasukan
    baris.append("📥 *Pemasukan*")
    pemasukan_items = []
    pem_total = data["pemasukan_total"]
    for kat, val in data["per_kategori"].items():
        if kat == "pemasukan" and val["total"] > 0:
            emoji  = EMOJI_KAT.get(kat, "💰")
            persen = round(val["total"] / pem_total * 100) if pem_total else 0
            mini   = _mini_bar(persen)
            nama   = kat.title().ljust(10)
            hrg    = rupiah(val["total"])
            pemasukan_items.append(f"{emoji}  {nama}  ({persen:>2}%)  {hrg}  {mini}")

    if pemasukan_items:
        baris.extend(pemasukan_items)
        baris.append(f"Total Pemasukan: *{rupiah(pem_total)}* ({data['pemasukan_count']} trx)")
    else:
        baris.append("_Tidak ada pemasukan._")

    baris.append("")

    # 💸 Pengeluaran
    baris.append("💸 *Pengeluaran*")
    pengeluaran_items = []
    peng_total = data["pengeluaran_total"]
    for kat, val in data["per_kategori"].items():
        if kat != "pemasukan" and val["total"] > 0:
            emoji  = EMOJI_KAT.get(kat, "📌")
            persen = round(val["total"] / peng_total * 100) if peng_total else 0
            mini   = _mini_bar(persen)
            nama   = kat.title().ljust(10)
            hrg    = rupiah(val["total"])
            pengeluaran_items.append(f"{emoji}  {nama}  ({persen:>2}%)  {hrg}  {mini}")

    if pengeluaran_items:
        baris.extend(pengeluaran_items)
        baris.append(f"Total Pengeluaran: *{rupiah(peng_total)}* ({data['pengeluaran_count']} trx)")
    else:
        baris.append("_Tidak ada pengeluaran._")

    # Selisih PERIODE INI
    baris.append("")
    baris.append("━" * 28)
    net_saldo = pem_total - peng_total
    if net_saldo >= 0:
        baris.append(f"⚖️ Selisih 7 hari ini: *+{rupiah(net_saldo)}*")
    else:
        baris.append(f"⚖️ Selisih 7 hari ini: *-{rupiah(abs(net_saldo))}*")

    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# BUILDER PESAN
# ─────────────────────────────────────────────────────────────

def resolve_periode(
    mode     : str      = "bulan",
    tgt_bulan: int|None = None,
    tgt_tahun: int|None = None,
) -> tuple[date, date, str]:
    """
    Ubah mode ("hari"/"kemarin"/"minggu"/"bulan"/"tahun") jadi rentang
    tanggal (start, end) + label periode yang bisa dibaca manusia.

    Dipakai bersama oleh /rekap dan /grafik supaya logika rentang
    tanggal tidak duplikat.
    """
    now   = datetime.now()
    today = now.date()

    if mode == "hari":
        return today, today, "Hari Ini"

    if mode == "kemarin":
        kemarin = today - timedelta(days=1)
        return kemarin, kemarin, "Kemarin"

    if mode == "minggu":
        start = today - timedelta(days=6)
        return start, today, "7 Hari Terakhir"

    if mode == "tahun":
        start = date(now.year, 1, 1)
        end   = date(now.year, 12, 31)
        return start, end, f"Tahun {now.year}"

    # default / "bulan"
    b     = tgt_bulan or now.month
    t     = tgt_tahun or now.year
    start = date(t, b, 1)
    end   = date(t, b, calendar.monthrange(t, b)[1])
    return start, end, f"{NAMA_BULAN[b]} {t}"


async def build_rekap_pesan(
    records       : list[dict],
    mode          : str      = "default",
    tgt_bulan     : int|None = None,
    tgt_tahun     : int|None = None,
    budget_override: int|None = None,
) -> str:
    """
    Buat pesan rekap lengkap sesuai mode.

    budget_override: kalau diisi, pakai nilai ini langsung tanpa query
    Google Sheets — dipakai untuk test manual dengan data dummy
    (lihat blok `if __name__ == "__main__"` di bawah).
    """
    now    = datetime.now()
    today  = now.date()
    budget = budget_override if budget_override is not None else await get_budget()

    if mode == "hari":
        data = _aggregate(records, today, today)
        return f"📊 *Rekap Keuangan*\n\n{SEP}\n{_fmt_hari(data, today)}"

    if mode == "kemarin":
        kemarin = today - timedelta(days=1)
        data    = _aggregate(records, kemarin, kemarin)
        return f"📊 *Rekap Keuangan*\n\n{SEP}\n{_fmt_hari(data, kemarin, label='Kemarin')}"

    if mode == "minggu":
        start = today - timedelta(days=6)
        data  = _aggregate(records, start, today)
        return f"📊 *Rekap Keuangan*\n\n{SEP}\n{_fmt_minggu(data)}"

    if mode == "tahun":
        start = date(now.year, 1, 1)
        end   = date(now.year, 12, 31)
        data  = _aggregate(records, start, end)
        return f"📊 *Rekap Keuangan*\n\n{SEP}\n{_fmt_tahun(data, now.year)}"

    if mode == "bulan" or (tgt_bulan and tgt_tahun):
        b     = tgt_bulan or now.month
        t     = tgt_tahun or now.year
        start = date(t, b, 1)
        end   = date(t, b, calendar.monthrange(t, b)[1])
        data  = _aggregate(records, start, end)
        return f"📊 *Rekap Keuangan*\n\n{SEP}\n{_fmt_bulan(data, b, t, budget)}"

    # default: hari ini + bulan ini
    data_hari  = _aggregate(records, today, today)
    bln_start  = date(now.year, now.month, 1)
    bln_end    = date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
    data_bulan = _aggregate(records, bln_start, bln_end)

    return (
        f"📊 *Rekap Keuangan*\n\n"
        f"{SEP}\n{_fmt_hari(data_hari, today)}\n\n"
        f"{SEP}\n{_fmt_bulan(data_bulan, now.month, now.year, budget)}"
    )


# ─────────────────────────────────────────────────────────────
# TELEGRAM HANDLERS
# ─────────────────────────────────────────────────────────────

async def cmd_rekap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /rekap."""
    args = context.args or []
    arg  = args[0].lower().strip() if args else "default"

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
                "⚠️ Format bulan tidak valid.\n"
                "Contoh: `/rekap 06/2025`",
                parse_mode="Markdown",
            )
            return

    VALID = {"default", "hari", "kemarin", "minggu", "bulan", "tahun"}
    if arg not in VALID:
        await update.message.reply_text(
            f"⚠️ Mode `{arg}` tidak dikenal.\n\n{HELP_TEXT}",
            parse_mode="Markdown",
        )
        return

    loading = await update.message.reply_text("📊 Menghitung rekap keuangan... ⏳")

    try:
        records = await get_all_records()
    except Exception as e:
        logger.error(f"[/rekap] Gagal baca Sheets: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal membaca Google Sheets*\n\n"
            f"`{type(e).__name__}: {str(e)[:120]}`\n\n"
            "Pastikan:\n"
            "• `credentials.json` ada di folder bot\n"
            "• `SPREADSHEET_ID` di `.env` benar\n"
            "• Spreadsheet sudah di-share ke email service account",
            parse_mode="Markdown",
        )
        return

    try:
        if not records:
            await loading.edit_text(
                "📊 *Rekap Keuangan*\n\n"
                "_Belum ada data di Google Sheets._\n\n"
                "Mulai catat: `beli kopi 25rb` atau `gaji 5jt`",
                parse_mode="Markdown",
            )
            return

        logger.info(
            f"[/rekap] mode={arg}, records={len(records)}, "
            f"sample_tanggal={records[0].get('Tanggal','?') if records else '-'}, "
            f"user={update.effective_user.id}"
        )

        pesan = await build_rekap_pesan(
            records, mode=arg,
            tgt_bulan=tgt_bulan, tgt_tahun=tgt_tahun,
        )
        await loading.edit_text(pesan, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"[/rekap] Error proses data: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal memproses data*\n\n"
            f"`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )


async def cmd_budget(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /budget            → status budget bulan ini
    /budget 3000000    → set budget (aktif sampai bot restart)
    """
    args = context.args or []

    if args:
        try:
            new_budget = int(str(args[0]).replace(".", "").replace(",", ""))
            if new_budget <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text(
                "⚠️ Format tidak valid.\nContoh: `/budget 3000000`",
                parse_mode="Markdown",
            )
            return

        saving = await update.message.reply_text("💾 Menyimpan budget... ⏳")
        try:
            await set_budget(new_budget)
            await saving.edit_text(
                f"✅ Budget bulanan diset ke *{rupiah(new_budget)}*\n\n"
                f"_Tersimpan permanen di Google Sheets (tab Config) — "
                f"tidak akan hilang meski bot di-restart._",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"[/budget] Gagal simpan budget: {e}", exc_info=True)
            await saving.edit_text(
                "😔 Gagal menyimpan budget ke Google Sheets. Coba lagi ya."
            )
        return

    loading = await update.message.reply_text("📊 Menghitung... ⏳")
    budget  = await get_budget()
    try:
        records = await get_all_records()
        now    = datetime.now()
        start  = date(now.year, now.month, 1)
        end    = date(now.year, now.month, calendar.monthrange(now.year, now.month)[1])
        data   = _aggregate(records, start, end)
        total  = data["total"]

        if budget == 0:
            pesan = (
                f"💰 *{NAMA_BULAN[now.month]} {now.year}*\n\n"
                f"Total Pengeluaran: *{rupiah(total)}*\n"
                f"({data['pengeluaran_count']} transaksi pengeluaran)\n\n"
                f"_Set budget dengan:_ `/budget 3000000`"
            )
        else:
            terpakai_pct = min(100, round(total / budget * 100))
            sisa         = budget - total
            pct_sisa     = 100 - terpakai_pct
            icon         = "✅" if sisa >= 0 else "⚠️"
            status       = "Aman" if sisa >= 0 else "Over budget!"

            pesan = (
                f"💰 *Budget {NAMA_BULAN[now.month]} {now.year}*\n\n"
                f"Budget    : *{rupiah(budget)}*\n"
                f"Terpakai  : *{rupiah(total)}* ({terpakai_pct}%)\n"
                f"{'Tersisa' if sisa >= 0 else 'Over':<9}: "
                f"*{rupiah(abs(sisa))}* ({pct_sisa if sisa >= 0 else terpakai_pct - 100}%)\n\n"
                f"{_bar(terpakai_pct)}\n\n"
                f"{icon} {status}"
            )

        await loading.edit_text(pesan, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"[/budget] Error: {e}", exc_info=True)
        await loading.edit_text("😔 Gagal mengambil data. Coba lagi.")


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.rekap
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
    )

    # Data dummy — tidak perlu koneksi Sheets
    TODAY = date.today().strftime("%d/%m/%Y")
    KEMARIN = (date.today() - timedelta(days=1)).strftime("%d/%m/%Y")
    BLN = date.today().month
    THN = date.today().year

    DUMMY = [
        {"Tanggal": TODAY,    "Nama Item": "Kopi",          "Kategori": "Makan",     "Harga": 25000},
        {"Tanggal": TODAY,    "Nama Item": "Grab ke kantor", "Kategori": "Transport", "Harga": 18000},
        {"Tanggal": TODAY,    "Nama Item": "Gaji Bulanan",  "Kategori": "Pemasukan", "Harga": 5000000},
        {"Tanggal": TODAY,    "Nama Item": "Makan Siang",   "Kategori": "Makan",     "Harga": 35000},
        {"Tanggal": KEMARIN,  "Nama Item": "Indomaret",      "Kategori": "Belanja",   "Harga": 85000},
        {"Tanggal": f"05/{BLN:02d}/{THN}", "Nama Item": "Bensin",    "Kategori": "Transport", "Harga": 80000},
        {"Tanggal": f"10/{BLN:02d}/{THN}", "Nama Item": "Makan Keluarga", "Kategori": "Makan", "Harga": 175000},
        {"Tanggal": f"15/{BLN:02d}/{THN}", "Nama Item": "Obat Apotek", "Kategori": "Kesehatan", "Harga": 45000},
        {"Tanggal": f"20/{BLN:02d}/{THN}", "Nama Item": "Bioskop",  "Kategori": "Hiburan",   "Harga": 75000},
        {"Tanggal": f"22/{BLN:02d}/{THN}", "Nama Item": "Listrik",  "Kategori": "Lainnya",   "Harga": 120000},
    ]

    async def run():
        for mode in ["default", "hari", "kemarin", "minggu", "bulan", "tahun"]:
            print(f"\n{'='*55}\nMODE: {mode}\n{'='*55}")
            print(await build_rekap_pesan(DUMMY, mode=mode, budget_override=1_500_000))

    asyncio.run(run())