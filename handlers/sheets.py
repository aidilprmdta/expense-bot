"""
handlers/sheets.py
──────────────────
Integrasi Google Sheets untuk expense tracker.

Fungsi utama (async, aman dipakai di Telegram bot):
  append_expense_to_sheets(data)   → simpan 1 item
  append_expenses_batch(items)     → simpan banyak item sekaligus (untuk foto struk)
  get_monthly_summary()            → rekap bulanan per kategori
  get_today_total()                → total pengeluaran hari ini
  format_rekap(summary)            → format rekap jadi pesan Telegram

Setup koneksi:
  • Development: credentials.json di folder project
  • Production (Railway/VPS): env var GOOGLE_CREDENTIALS_JSON
"""

import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional

import gspread
import gspread.exceptions
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Kolom spreadsheet — urutan ini PENTING
HEADER = ["Tanggal", "Nama Item", "Kategori", "Harga", "Catatan"]
#          A           B            C            D        E

# Warna header (hijau toska — sesuai tema keuangan)
HEADER_BG_COLOR = {"red": 0.13, "green": 0.55, "blue": 0.45}

# Cache koneksi — reconnect otomatis jika stale
_cached_sheet: Optional[gspread.Worksheet] = None


# ─────────────────────────────────────────────────────────────
# SETUP KONEKSI
# ─────────────────────────────────────────────────────────────

def _load_credentials() -> Credentials:
    """
    Load Google service account credentials.

    Strategi (dicoba urutan ini):
      1. File credentials.json di folder project (development)
      2. Env var GOOGLE_CREDENTIALS_JSON berisi isi JSON-nya (production)

    Cara set GOOGLE_CREDENTIALS_JSON di Railway:
      - Buka dashboard Railway → Variables
      - Tambah key: GOOGLE_CREDENTIALS_JSON
      - Value: paste isi credentials.json (satu baris JSON)
    """
    # ── Opsi 1: file lokal ────────────────────────────────────
    creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    if os.path.exists(creds_file):
        logger.info(f"[sheets] Credentials dari file: {creds_file}")
        return Credentials.from_service_account_file(creds_file, scopes=SCOPES)

    # ── Opsi 2: env variable (untuk Railway/VPS) ──────────────
    creds_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    if creds_json_str:
        logger.info("[sheets] Credentials dari environment variable.")
        try:
            info = json.loads(creds_json_str)
        except json.JSONDecodeError as e:
            raise ValueError(
                "GOOGLE_CREDENTIALS_JSON bukan JSON yang valid. "
                f"Pastikan isi env var adalah JSON satu baris. Error: {e}"
            ) from e
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    # ── Tidak ditemukan ───────────────────────────────────────
    raise FileNotFoundError(
        "Google credentials tidak ditemukan!\n\n"
        "Development  → taruh credentials.json di folder project\n"
        "Production   → set env var GOOGLE_CREDENTIALS_JSON "
        "dengan isi credentials.json"
    )


def _connect_to_sheet() -> gspread.Worksheet:
    """
    Buat koneksi baru ke Google Sheets dan return Worksheet.
    Fungsi sinkron — dipanggil via asyncio.to_thread().
    """
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError(
            "SPREADSHEET_ID tidak ada di .env\n"
            "Ambil dari URL spreadsheet: "
            "https://docs.google.com/spreadsheets/d/[INI_ID_NYA]/edit"
        )

    sheet_name = os.getenv("SHEET_NAME", "Sheet1")

    creds  = _load_credentials()
    client = gspread.authorize(creds)

    try:
        workbook = client.open_by_key(spreadsheet_id)
    except gspread.exceptions.SpreadsheetNotFound:
        raise ValueError(
            f"Spreadsheet tidak ditemukan! ID: {spreadsheet_id}\n"
            "Pastikan sudah di-share ke email service account."
        )

    # Coba buka worksheet by name, fallback ke sheet pertama
    try:
        sheet = workbook.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        sheet = workbook.sheet1
        logger.warning(
            f"[sheets] Sheet '{sheet_name}' tidak ada, pakai sheet pertama: "
            f"'{sheet.title}'"
        )

    _ensure_header(sheet)
    logger.info(
        f"[sheets] Terhubung ke '{workbook.title}' → sheet '{sheet.title}'"
    )
    return sheet


def _ensure_header(sheet: gspread.Worksheet) -> None:
    """
    Pastikan baris pertama adalah header standar.
    Jika sheet kosong, buat dan format header otomatis.
    """
    try:
        row1 = sheet.row_values(1)
    except Exception:
        row1 = []

    if not row1:
        # Sheet kosong — buat header
        sheet.insert_row(HEADER, index=1)

        # Format: bold + warna background
        sheet.format(
            "A1:E1",
            {
                "textFormat"      : {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "backgroundColor" : HEADER_BG_COLOR,
                "horizontalAlignment": "CENTER",
            },
        )
        # Freeze baris header supaya tidak ikut scroll
        sheet.freeze(rows=1)

        # Set lebar kolom supaya rapi
        # (A=120, B=220, C=140, D=120, E=200 pixels)
        sheet.spreadsheet.batch_update({
            "requests": [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId"   : sheet.id,
                            "dimension" : "COLUMNS",
                            "startIndex": i,
                            "endIndex"  : i + 1,
                        },
                        "properties"       : {"pixelSize": w},
                        "fields"           : "pixelSize",
                    }
                }
                for i, w in enumerate([120, 220, 140, 120, 200])
            ]
        })
        logger.info("[sheets] Header dibuat dan diformat.")

    elif row1[:len(HEADER)] != HEADER:
        logger.warning(f"[sheets] Header tidak standar: {row1}")


def _get_sheet() -> gspread.Worksheet:
    """
    Return cached sheet. Reconnect otomatis jika koneksi mati.
    Sinkron — dipanggil via asyncio.to_thread().
    """
    global _cached_sheet

    if _cached_sheet is not None:
        return _cached_sheet

    _cached_sheet = _connect_to_sheet()
    return _cached_sheet


def _reset_connection() -> None:
    """Reset cache koneksi. Dipanggil saat terjadi error API."""
    global _cached_sheet
    _cached_sheet = None
    logger.info("[sheets] Koneksi di-reset, akan reconnect pada request berikutnya.")


# ─────────────────────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────────────────────

def _fmt_date(iso_date: str) -> str:
    """Ubah 'YYYY-MM-DD' → 'dd/mm/yyyy' untuk tampilan Sheets."""
    try:
        dt = datetime.strptime(iso_date, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        return datetime.now().strftime("%d/%m/%Y")


def _build_row(data: dict, catatan: str = "-") -> list:
    """
    Build list baris siap append dari dict expense.

    Input dict (dari parse_expense):
      nama     : str   → "Nasi Padang"
      harga    : int   → 35000
      kategori : str   → "makan"
      tanggal  : str   → "2025-06-26"
      catatan  : str   → (opsional)

    Output list (urutan kolom A–E):
      ["26/06/2025", "Nasi Padang", "Makan", 35000, "-"]
    """
    catatan_final = str(data.get("catatan", catatan) or catatan).strip() or "-"

    return [
        _fmt_date(data.get("tanggal", "")),            # A: Tanggal
        str(data.get("nama", "Item")).strip().title(), # B: Nama Item
        str(data.get("kategori", "lainnya")).title(),  # C: Kategori
        int(data.get("harga", 0)),                     # D: Harga (integer)
        catatan_final,                                 # E: Catatan
    ]


# ─────────────────────────────────────────────────────────────
# WRITE — APPEND KE SHEETS
# ─────────────────────────────────────────────────────────────

def _sync_append_row(row: list) -> None:
    """Append satu baris. Sinkron + auto-retry sekali jika gagal."""
    try:
        sheet = _get_sheet()
        sheet.append_row(
            row,
            value_input_option="USER_ENTERED",  # angka dikenali sebagai angka
            insert_data_option="INSERT_ROWS",
        )
    except gspread.exceptions.APIError as e:
        logger.warning(f"[sheets] API error, retry setelah reconnect: {e}")
        _reset_connection()
        sheet = _get_sheet()
        sheet.append_row(
            row,
            value_input_option="USER_ENTERED",
            insert_data_option="INSERT_ROWS",
        )


def _sync_append_rows(rows: list[list]) -> None:
    """Append banyak baris sekaligus. Sinkron + auto-retry."""
    try:
        sheet = _get_sheet()
        sheet.append_rows(
            rows,
            value_input_option="USER_ENTERED",
            insert_data_option="INSERT_ROWS",
        )
    except gspread.exceptions.APIError as e:
        logger.warning(f"[sheets] API error, retry setelah reconnect: {e}")
        _reset_connection()
        sheet = _get_sheet()
        sheet.append_rows(
            rows,
            value_input_option="USER_ENTERED",
            insert_data_option="INSERT_ROWS",
        )


async def append_expense_to_sheets(data: dict, catatan: str = "-") -> None:
    """
    Append SATU pengeluaran ke Google Sheets.

    Args:
        data   : dict dari parse_expense() — berisi nama, harga, kategori, tanggal
        catatan: catatan tambahan opsional (default "-")

    Raises:
        RuntimeError: jika gagal setelah retry

    Contoh:
        await append_expense_to_sheets(
            {"nama": "Kopi", "harga": 25000, "kategori": "makan", "tanggal": "2025-06-26"},
            catatan="via chat"
        )
        # → baris baru di Sheets: 26/06/2025 | Kopi | Makan | 25000 | via chat
    """
    row = _build_row(data, catatan)
    logger.info(f"[sheets] Append row: {row}")

    try:
        await asyncio.to_thread(_sync_append_row, row)
        logger.info("[sheets] ✅ Berhasil append 1 item.")
    except Exception as e:
        logger.error(f"[sheets] Gagal append: {e}")
        raise RuntimeError(f"Gagal simpan ke Google Sheets: {e}") from e


async def append_expenses_batch(items: list[dict], catatan: str = "-") -> None:
    """
    Append BANYAK pengeluaran sekaligus (1 API call, lebih efisien).
    Gunakan ini saat proses foto struk dengan banyak item.

    Args:
        items  : list of dict dari parse_expense()
        catatan: catatan yang sama untuk semua item (default "-")

    Contoh:
        await append_expenses_batch(
            [
                {"nama": "Air Mineral", "harga": 5000, ...},
                {"nama": "Sabun", "harga": 12000, ...},
            ],
            catatan="via foto struk"
        )
    """
    if not items:
        logger.warning("[sheets] append_expenses_batch dipanggil dengan list kosong.")
        return

    rows = [_build_row(item, catatan) for item in items]
    logger.info(f"[sheets] Batch append {len(rows)} rows.")

    try:
        await asyncio.to_thread(_sync_append_rows, rows)
        logger.info(f"[sheets] ✅ Berhasil batch append {len(rows)} item.")
    except Exception as e:
        logger.error(f"[sheets] Gagal batch append: {e}")
        raise RuntimeError(f"Gagal simpan ke Google Sheets: {e}") from e


# ─────────────────────────────────────────────────────────────
# READ — UNTUK REKAP (Phase 6)
# ─────────────────────────────────────────────────────────────

def _sync_get_all_records() -> list[dict]:
    """
    Ambil semua records dari Google Sheets sebagai list of dict.

    PENTING:
    - numericise_ignore=["all"]: semua kolom return sebagai string.
      Tanpa ini, kolom Tanggal yang diformat sebagai Date di Sheets
      dikembalikan sebagai angka serial (misal 46033, bukan '05/07/2026').
      _safe_int() di rekap.py handle parsing Harga dari string.

    - Tidak pakai expected_headers: lebih toleran jika header Sheets
      berbeda sedikit (spasi ekstra, case berbeda, dll).
    """
    sheet = _get_sheet()
    return sheet.get_all_records(
        numericise_ignore=["all"],
    )


async def get_all_records() -> list[dict]:
    """Return semua baris expense sebagai list of dict."""
    return await asyncio.to_thread(_sync_get_all_records)


# ─────────────────────────────────────────────────────────────
# BUDGET PERSISTEN — disimpan di tab "Config" pada spreadsheet
# yang sama, supaya tidak hilang saat bot di-restart (beda
# dengan os.environ yang cuma hidup selama proses berjalan).
# ─────────────────────────────────────────────────────────────

CONFIG_SHEET_NAME = "Config"
BUDGET_KEY        = "BUDGET_BULANAN"

_cached_config_sheet: Optional[gspread.Worksheet] = None


def _connect_to_config_sheet() -> gspread.Worksheet:
    """
    Buka (atau buat) worksheet "Config" di spreadsheet yang sama dengan
    data expense. Dipakai untuk menyimpan setting kecil seperti budget.
    Sinkron — dipanggil via asyncio.to_thread().
    """
    _get_sheet()  # pastikan koneksi utama sudah terbentuk (reuse auth)
    spreadsheet_id = os.getenv("SPREADSHEET_ID")
    creds          = _load_credentials()
    client         = gspread.authorize(creds)
    workbook       = client.open_by_key(spreadsheet_id)

    try:
        config_sheet = workbook.worksheet(CONFIG_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        config_sheet = workbook.add_worksheet(
            title=CONFIG_SHEET_NAME, rows=10, cols=2
        )
        config_sheet.update("A1:B1", [["Key", "Value"]])
        config_sheet.format(
            "A1:B1",
            {
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "backgroundColor": HEADER_BG_COLOR,
            },
        )
        logger.info(f"[sheets] Tab '{CONFIG_SHEET_NAME}' dibuat.")

    return config_sheet


def _get_config_sheet() -> gspread.Worksheet:
    """Return cached config sheet. Reconnect otomatis jika kosong."""
    global _cached_config_sheet
    if _cached_config_sheet is not None:
        return _cached_config_sheet
    _cached_config_sheet = _connect_to_config_sheet()
    return _cached_config_sheet


def _sync_get_budget() -> int:
    """Baca nilai BUDGET_BULANAN dari tab Config. Default 0 jika belum diset."""
    try:
        sheet = _get_config_sheet()
        cell  = sheet.find(BUDGET_KEY)
        if cell is None:
            return 0
        value = sheet.cell(cell.row, cell.col + 1).value
        return max(0, int(str(value).replace(".", "").replace(",", "").strip() or 0))
    except Exception as e:
        logger.warning(f"[sheets] Gagal baca budget: {e}")
        return 0


def _sync_set_budget(value: int) -> None:
    """Simpan/update nilai BUDGET_BULANAN di tab Config."""
    sheet = _get_config_sheet()
    cell  = sheet.find(BUDGET_KEY)
    if cell is None:
        sheet.append_row([BUDGET_KEY, value])
    else:
        sheet.update_cell(cell.row, cell.col + 1, value)


async def get_budget() -> int:
    """Ambil budget bulanan yang tersimpan permanen (persist antar restart)."""
    return await asyncio.to_thread(_sync_get_budget)


async def set_budget(value: int) -> None:
    """Simpan budget bulanan secara permanen ke Google Sheets."""
    await asyncio.to_thread(_sync_set_budget, value)


# ─────────────────────────────────────────────────────────────
# KATEGORI KUSTOM — user bisa tambah kategori sendiri di luar
# kategori bawaan (makan/transport/belanja/kesehatan/hiburan/
# pemasukan/lainnya). Disimpan sebagai satu baris comma-separated
# di tab "Config" yang sama dengan budget.
# ─────────────────────────────────────────────────────────────

CUSTOM_CATEGORY_KEY = "KATEGORI_KUSTOM"


def _sync_get_custom_categories() -> list[str]:
    """Baca daftar kategori kustom dari tab Config. Return list kosong jika belum ada."""
    try:
        sheet = _get_config_sheet()
        cell  = sheet.find(CUSTOM_CATEGORY_KEY)
        if cell is None:
            return []
        value = sheet.cell(cell.row, cell.col + 1).value or ""
        return [c.strip().lower() for c in value.split(",") if c.strip()]
    except Exception as e:
        logger.warning(f"[sheets] Gagal baca kategori kustom: {e}")
        return []


def _sync_save_custom_categories(categories: list[str]) -> None:
    """Simpan ulang seluruh daftar kategori kustom (overwrite)."""
    sheet = _get_config_sheet()
    value = ",".join(categories)
    cell  = sheet.find(CUSTOM_CATEGORY_KEY)
    if cell is None:
        sheet.append_row([CUSTOM_CATEGORY_KEY, value])
    else:
        sheet.update_cell(cell.row, cell.col + 1, value)


async def get_custom_categories() -> list[str]:
    """Ambil daftar kategori kustom yang sudah ditambahkan user."""
    return await asyncio.to_thread(_sync_get_custom_categories)


async def add_custom_category(nama: str) -> tuple[bool, str]:
    """
    Tambah kategori kustom baru.
    Return (berhasil: bool, pesan: str).
    """
    from handlers.ai_parser import KATEGORI_VALID  # local import: hindari circular import

    nama = nama.strip().lower()
    if not nama or not all(c.isalnum() or c == " " for c in nama):
        return False, "Nama kategori tidak valid. Gunakan huruf/angka saja, tanpa simbol."
    if len(nama) > 20:
        return False, "Nama kategori terlalu panjang (maks 20 karakter)."
    if nama in KATEGORI_VALID:
        return False, f"Kategori '{nama}' sudah ada (kategori bawaan)."

    current = await get_custom_categories()
    if nama in current:
        return False, f"Kategori '{nama}' sudah ada di daftar kustom."

    current.append(nama)
    await asyncio.to_thread(_sync_save_custom_categories, current)
    return True, f"Kategori '{nama}' berhasil ditambahkan."


async def remove_custom_category(nama: str) -> tuple[bool, str]:
    """
    Hapus kategori kustom.
    Return (berhasil: bool, pesan: str).
    """
    nama    = nama.strip().lower()
    current = await get_custom_categories()
    if nama not in current:
        return False, f"Kategori '{nama}' tidak ditemukan di daftar kustom."

    current.remove(nama)
    await asyncio.to_thread(_sync_save_custom_categories, current)
    return True, f"Kategori '{nama}' berhasil dihapus."


async def get_monthly_summary(bulan: int = None, tahun: int = None) -> dict:
    """
    Rekap pengeluaran per bulan.

    Return:
        {
            "total"       : 850000,
            "per_kategori": {"makan": 350000, "transport": 200000, ...},
            "jumlah_item" : 18,
            "periode"     : "Juni 2025",
        }
    """
    now   = datetime.now()
    bulan = bulan or now.month
    tahun = tahun or now.year

    records   = await get_all_records()
    target    = f"{bulan:02d}/{tahun}"   # "06/2025" — cocok dengan format dd/mm/yyyy

    per_kategori: dict[str, int] = {}
    total        = 0
    jumlah_item  = 0

    for r in records:
        tanggal = str(r.get("Tanggal", "")).strip()
        if len(tanggal) < 10:
            continue
        # Format kolom: "26/06/2025" — match dengan mm/yyyy di posisi 3-9
        if target not in tanggal[3:]:
            continue

        harga    = int(r.get("Harga", 0))
        kategori = str(r.get("Kategori", "Lainnya")).lower()

        total                    += harga
        per_kategori[kategori]    = per_kategori.get(kategori, 0) + harga
        jumlah_item              += 1

    # Sort per kategori dari terbesar
    per_kategori_sorted = dict(
        sorted(per_kategori.items(), key=lambda x: x[1], reverse=True)
    )

    nama_bulan = [
        "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
        "Juli", "Agustus", "September", "Oktober", "November", "Desember",
    ]

    return {
        "total"       : total,
        "per_kategori": per_kategori_sorted,
        "jumlah_item" : jumlah_item,
        "periode"     : f"{nama_bulan[bulan]} {tahun}",
    }


async def get_today_total() -> int:
    """Total pengeluaran hari ini (integer Rupiah)."""
    summary = await get_today_summary()
    return summary["total"]


async def get_today_summary() -> dict:
    """
    Rekap pengeluaran hari ini.

    Return:
        {
            "total"       : 85000,
            "per_kategori": {"makan": 35000, "transport": 50000, ...},
            "jumlah_item" : 3,
            "periode"     : "29/06/2025",
        }
    """
    records  = await get_all_records()
    hari_ini = datetime.now().strftime("%d/%m/%Y")

    per_kategori: dict[str, int] = {}
    total        = 0
    jumlah_item  = 0

    for r in records:
        tanggal = str(r.get("Tanggal", "")).strip()
        if tanggal != hari_ini:
            continue

        harga    = int(r.get("Harga", 0))
        kategori = str(r.get("Kategori", "Lainnya")).lower()

        total                    += harga
        per_kategori[kategori]    = per_kategori.get(kategori, 0) + harga
        jumlah_item              += 1

    per_kategori_sorted = dict(
        sorted(per_kategori.items(), key=lambda x: x[1], reverse=True)
    )

    return {
        "total"       : total,
        "per_kategori": per_kategori_sorted,
        "jumlah_item" : jumlah_item,
        "periode"     : hari_ini,
    }


async def test_connection() -> bool:
    """Cek apakah koneksi Google Sheets berhasil."""
    try:
        await asyncio.to_thread(_get_sheet)
        return True
    except Exception as e:
        logger.warning(f"[sheets] test_connection gagal: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# FORMAT REKAP — PESAN TELEGRAM
# ─────────────────────────────────────────────────────────────

EMOJI_KATEGORI = {
    "makan"     : "🍽️",
    "transport" : "🚗",
    "belanja"   : "🛒",
    "kesehatan" : "💊",
    "hiburan"   : "🎮",
    "pemasukan" : "💰",
    "lainnya"   : "📌",
}


def rupiah(angka: int) -> str:
    """Format integer ke string Rupiah. 25000 → 'Rp 25.000'"""
    return f"Rp {angka:,}".replace(",", ".")


def _tabel_kategori(per_kat: dict, total: int) -> str:
    """Format breakdown kategori sebagai tabel monospace untuk Telegram."""
    if not per_kat or total <= 0:
        return ""

    baris = [
        f"{'Kategori':<12} {'Nominal':>14}  {'%':>4}",
        "─" * 34,
    ]
    for kat, jml in per_kat.items():
        persen = round(jml / total * 100)
        nama   = kat.title()[:11]
        baris.append(f"{nama:<12} {rupiah(jml):>14}  {persen:>3}%")

    return "```\n" + "\n".join(baris) + "\n```"


def _tabel_ringkasan(hari: dict, bulan: dict) -> str:
    """Tabel ringkasan total hari ini vs bulan ini."""
    baris = [
        f"{'Periode':<14} {'Total':>14}  {'Item':>5}",
        "─" * 36,
        f"{'Hari ini':<14} {rupiah(hari['total']):>14}  {hari['jumlah_item']:>5}",
        f"{'Bulan ini':<14} {rupiah(bulan['total']):>14}  {bulan['jumlah_item']:>5}",
    ]
    return "```\n" + "\n".join(baris) + "\n```"


def format_rekap_hari(summary: dict) -> str:
    """Format rekap harian dengan tabel kategori."""
    total   = summary["total"]
    per_kat = summary["per_kategori"]
    jumlah  = summary["jumlah_item"]
    periode = summary["periode"]

    baris = [
        f"📊 *Rekap Hari Ini*",
        f"📅 {periode}\n",
        f"💰 Total: *{rupiah(total)}*",
        f"📦 {jumlah} transaksi",
    ]

    tabel = _tabel_kategori(per_kat, total)
    if tabel:
        baris.append("\n*Per kategori:*")
        baris.append(tabel)

    return "\n".join(baris)


def format_rekap_lengkap(hari: dict, bulan: dict, budget: int = 0) -> str:
    """Rekap gabungan: ringkasan hari + bulan, breakdown kategori bulan ini."""
    periode = bulan["periode"]

    baris = [
        f"📊 *Rekap Pengeluaran*",
        f"📅 {periode}\n",
        "*Ringkasan:*",
        _tabel_ringkasan(hari, bulan),
    ]

    if bulan["per_kategori"]:
        baris.append("*Breakdown bulan ini:*")
        baris.append(_tabel_kategori(bulan["per_kategori"], bulan["total"]))

    if budget > 0:
        sisa   = budget - bulan["total"]
        status = "✅ Aman" if sisa >= 0 else "⚠️ Over budget!"
        baris.append(f"\n{'─' * 22}")
        baris.append(f"🎯 Budget: {rupiah(budget)}")
        baris.append(f"{status}: {rupiah(abs(sisa))} {'tersisa' if sisa >= 0 else 'lebih'}")

    return "\n".join(baris)


def format_rekap(summary: dict, budget: int = 0) -> str:
    """
    Format dict summary menjadi pesan rekap Telegram yang rapi.

    Contoh output:
      📊 Rekap Juni 2025

      💰 Total: Rp 850.000
      📦 18 transaksi

      Per kategori:
      🍽️ Makan        Rp 350.000  (41%)
      🚗 Transport     Rp 200.000  (24%)
      🛒 Belanja       Rp 180.000  (21%)
      📌 Lainnya       Rp 120.000  (14%)

      ─────────────────────
      🟢 Budget: Rp 3.000.000
      ✅ Sisa: Rp 2.150.000
    """
    total       = summary["total"]
    per_kat     = summary["per_kategori"]
    jumlah      = summary["jumlah_item"]
    periode     = summary["periode"]

    baris = [
        f"📊 *Rekap {periode}*\n",
        f"💰 Total: *{rupiah(total)}*",
        f"📦 {jumlah} transaksi",
    ]

    tabel = _tabel_kategori(per_kat, total)
    if tabel:
        baris.append("\n*Per kategori:*")
        baris.append(tabel)

    if budget > 0:
        sisa   = budget - total
        status = "✅ Aman" if sisa >= 0 else "⚠️ Over budget!"
        baris.append(f"\n{'─' * 22}")
        baris.append(f"🎯 Budget: {rupiah(budget)}")
        baris.append(f"{status}: {rupiah(abs(sisa))} {'tersisa' if sisa >= 0 else 'lebih'}")

    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# TEST MANUAL
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s",
        level=logging.INFO,
    )

    async def run_test():
        print("=" * 50)
        print("TEST GOOGLE SHEETS INTEGRATION")
        print("=" * 50)

        # Test 1: append satu item
        print("\n[1] Append satu item...")
        await append_expense_to_sheets(
            {
                "nama"    : "Kopi Test",
                "harga"   : 25000,
                "kategori": "makan",
                "tanggal" : datetime.now().strftime("%Y-%m-%d"),
            },
            catatan="test manual",
        )
        print("    ✅ Berhasil!")

        # Test 2: batch append
        print("\n[2] Batch append 2 item...")
        await append_expenses_batch([
            {"nama": "Roti", "harga": 15000, "kategori": "makan",
             "tanggal": datetime.now().strftime("%Y-%m-%d")},
            {"nama": "Air Mineral", "harga": 5000, "kategori": "belanja",
             "tanggal": datetime.now().strftime("%Y-%m-%d")},
        ], catatan="test batch")
        print("    ✅ Berhasil!")

        # Test 3: baca rekap
        print("\n[3] Ambil rekap bulan ini...")
        summary = await get_monthly_summary()
        pesan   = format_rekap(summary, budget=3_000_000)
        print(f"\n{pesan}")

        # Test 4: total hari ini
        total_hari_ini = await get_today_total()
        print(f"\n[4] Total hari ini: {rupiah(total_hari_ini)}")

    asyncio.run(run_test())