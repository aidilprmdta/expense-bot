"""
handlers/edit.py
──────────────────
Edit transaksi yang sudah tercatat — ubah nominal, kategori, nama,
tanggal, atau catatan tanpa perlu hapus + catat ulang.

Penggunaan:
  /edit 15 harga=30000
  /edit 15 harga=30000 kategori=transport
  /edit 15 nama="Kopi Susu Baru"
  /edit terakhir kategori=makan
"""

import logging
import re
import shlex

from telegram import Update
from telegram.ext import ContextTypes

from handlers.sheets import (
    get_all_records,
    get_last_transaction,
    update_row,
    get_custom_categories,
)
from handlers.ai_parser import KATEGORI_VALID
from handlers.rekap import rupiah, _parse_tgl

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "Cara pakai:\n"
    "`/edit <nomor> <field>=<nilai>` — ubah satu atau lebih field\n"
    "`/edit terakhir <field>=<nilai>` — edit transaksi terakhir\n\n"
    "Field yang bisa diubah: `nama`, `harga`, `kategori`, `tanggal`, `catatan`\n\n"
    "Contoh:\n"
    "`/edit 15 harga=30000`\n"
    "`/edit 15 harga=30000 kategori=transport`\n"
    "`/edit 15 nama=\"Kopi Susu Baru\"`\n"
    "`/edit terakhir kategori=makan`\n\n"
    "_Nomor baris bisa dilihat dari hasil `/cari`._"
)

FIELD_ALIASES = {
    "nama": "nama", "item": "nama",
    "harga": "harga", "nominal": "harga", "jumlah": "harga",
    "kategori": "kategori", "kat": "kategori",
    "tanggal": "tanggal", "tgl": "tanggal",
    "catatan": "catatan", "note": "catatan",
}


def _safe_int(v) -> int:
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


def _parse_harga(nilai: str) -> int:
    """Parse input harga user jadi integer. Terima '30000', '30.000', '30rb', '30k', '1.5jt'."""
    s = nilai.strip().lower().replace(" ", "")
    if s.startswith("rp"):
        s = s[2:]
    multiplier = 1

    if s.endswith("ribu"):
        s, multiplier = s[:-4], 1000
    elif s.endswith("rb"):
        s, multiplier = s[:-2], 1000
    elif s.endswith("juta"):
        s, multiplier = s[:-4], 1_000_000
    elif s.endswith("jt"):
        s, multiplier = s[:-2], 1_000_000
    elif s.endswith("k"):
        s, multiplier = s[:-1], 1000

    s = s.replace(",", ".")  # dukung "1,5jt" gaya Indonesia (koma = desimal)

    # Titik bisa berarti pemisah ribuan ("30.000") ATAU desimal ("1.5jt").
    # Kalau tidak ada multiplier dan formatnya persis grup 3 digit (30.000 /
    # 1.500.000), itu pemisah ribuan → dihapus. Selain itu, titik dianggap desimal.
    if multiplier == 1 and re.match(r"^\d{1,3}(\.\d{3})+$", s):
        s = s.replace(".", "")

    try:
        nilai_akhir = float(s) * multiplier
        if nilai_akhir < 0:
            raise ValueError
        return int(nilai_akhir)
    except ValueError:
        raise ValueError(f"Format harga '{nilai}' tidak valid. Contoh: 30000, 30rb, 1.5jt")


async def _validate_kategori(nilai: str) -> str:
    """Validasi kategori terhadap kategori bawaan + kustom. Return kategori lowercase yang valid."""
    kat = nilai.strip().lower()
    if kat in KATEGORI_VALID:
        return kat

    custom = await get_custom_categories()
    if kat in custom:
        return kat

    raise ValueError(
        f"Kategori '{nilai}' tidak dikenal. Cek daftar kategori dengan /kategori, "
        f"atau tambah dulu dengan /tambahkategori."
    )


def _parse_tanggal_input(nilai: str) -> str:
    """Parse input tanggal user (DD/MM/YYYY atau YYYY-MM-DD) → simpan sbg DD/MM/YYYY."""
    tgl = _parse_tgl(nilai.strip())
    if tgl is None:
        raise ValueError(
            f"Format tanggal '{nilai}' tidak valid. Pakai DD/MM/YYYY atau YYYY-MM-DD."
        )
    return tgl.strftime("%d/%m/%Y")


def _parse_args(raw_args: list[str]) -> dict:
    """
    Parse argumen setelah nomor/'terakhir' jadi dict field->value.
    Pakai shlex supaya value dengan spasi bisa dikutip: nama="Kopi Susu".
    """
    joined = " ".join(raw_args)
    try:
        tokens = shlex.split(joined)
    except ValueError:
        tokens = raw_args  # fallback kalau kutipan tidak seimbang

    updates_raw = {}
    for token in tokens:
        if "=" not in token:
            continue
        field, _, value = token.partition("=")
        field_key = FIELD_ALIASES.get(field.strip().lower())
        if field_key and value.strip():
            updates_raw[field_key] = value.strip()

    return updates_raw


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler Telegram untuk /edit."""
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
        return

    target      = args[0].lower().strip()
    updates_raw = _parse_args(args[1:])

    if not updates_raw:
        await update.message.reply_text(
            f"⚠️ Tidak ada field valid yang mau diubah.\n\n{HELP_TEXT}",
            parse_mode="Markdown",
        )
        return

    loading = await update.message.reply_text("🔍 Mencari transaksi... ⏳")

    # ── Tentukan row_number & record lama ──────────────────────
    try:
        if target == "terakhir":
            info = await get_last_transaction()
            if info is None:
                await loading.edit_text("_Belum ada transaksi yang tercatat._", parse_mode="Markdown")
                return
            row_number, record_lama = info
        elif target.isdigit():
            row_number = int(target)
            if row_number < 2:
                await loading.edit_text("⚠️ Nomor baris tidak valid — baris 1 adalah header.")
                return
            records = await get_all_records()
            idx = row_number - 2
            if idx < 0 or idx >= len(records):
                await loading.edit_text(
                    f"⚠️ Baris nomor {row_number} tidak ditemukan.\n"
                    f"Total transaksi saat ini: {len(records)} (baris 2 s/d {len(records) + 1})."
                )
                return
            record_lama = records[idx]
        else:
            await loading.edit_text(
                f"⚠️ `{target}` bukan nomor baris atau kata 'terakhir'.\n\n{HELP_TEXT}",
                parse_mode="Markdown",
            )
            return
    except Exception as e:
        logger.error(f"[/edit] Gagal baca Sheets: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal membaca Google Sheets*\n\n`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )
        return

    # ── Validasi & siapkan nilai baru per field ────────────────
    updates_final       = {}
    ringkasan_perubahan = []

    try:
        for field, nilai in updates_raw.items():
            if field == "harga":
                harga_baru = _parse_harga(nilai)
                updates_final["harga"] = harga_baru
                ringkasan_perubahan.append(
                    f"💰 Harga: {rupiah(_safe_int(record_lama.get('Harga', 0)))} → *{rupiah(harga_baru)}*"
                )
            elif field == "kategori":
                kat_baru = await _validate_kategori(nilai)
                updates_final["kategori"] = kat_baru.title()
                ringkasan_perubahan.append(
                    f"🏷️ Kategori: {record_lama.get('Kategori', '-')} → *{kat_baru.title()}*"
                )
            elif field == "tanggal":
                tgl_baru = _parse_tanggal_input(nilai)
                updates_final["tanggal"] = tgl_baru
                ringkasan_perubahan.append(
                    f"📅 Tanggal: {record_lama.get('Tanggal', '-')} → *{tgl_baru}*"
                )
            elif field == "nama":
                nama_baru = nilai.strip().title()
                updates_final["nama"] = nama_baru
                ringkasan_perubahan.append(
                    f"📝 Nama: {record_lama.get('Nama Item', '-')} → *{nama_baru}*"
                )
            elif field == "catatan":
                catatan_baru = nilai.strip() or "-"
                updates_final["catatan"] = catatan_baru
                ringkasan_perubahan.append(
                    f"🗒️ Catatan: {record_lama.get('Catatan', '-')} → *{catatan_baru}*"
                )
    except ValueError as e:
        await loading.edit_text(f"⚠️ {e}")
        return

    # ── Terapkan perubahan ──────────────────────────────────────
    try:
        await update_row(row_number, updates_final)
    except Exception as e:
        logger.error(f"[/edit] Gagal update Sheets: {e}", exc_info=True)
        await loading.edit_text(
            f"😔 *Gagal menyimpan perubahan*\n\n`{type(e).__name__}: {str(e)[:120]}`",
            parse_mode="Markdown",
        )
        return

    await loading.edit_text(
        f"✅ *Transaksi #{row_number} berhasil diperbarui*\n\n" + "\n".join(ringkasan_perubahan),
        parse_mode="Markdown",
    )


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.edit
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Test _parse_harga dengan berbagai format
    assert _parse_harga("30000") == 30000
    assert _parse_harga("30.000") == 30000
    assert _parse_harga("30rb") == 30000
    assert _parse_harga("30ribu") == 30000
    assert _parse_harga("1.5jt") == 1500000
    assert _parse_harga("1,5jt") == 1500000
    assert _parse_harga("2k") == 2000
    assert _parse_harga("1.500.000") == 1500000
    assert _parse_harga("Rp30000") == 30000
    assert _parse_harga("Rp 30.000") == 30000
    print("_parse_harga: semua format OK ✅")

    try:
        _parse_harga("abc")
        print("ERROR: harusnya raise ValueError")
    except ValueError:
        print("_parse_harga menolak input invalid: OK ✅")

    # Test _parse_args dengan quoting
    hasil = _parse_args(['harga=30000', 'kategori=transport'])
    assert hasil == {"harga": "30000", "kategori": "transport"}
    print("_parse_args (tanpa quote): OK ✅")

    hasil2 = _parse_args(['nama="Kopi', 'Susu', 'Baru"'])
    assert hasil2 == {"nama": "Kopi Susu Baru"}
    print("_parse_args (dengan quote multi-word): OK ✅")

    # Test _parse_tanggal_input
    assert _parse_tanggal_input("2026-07-05") == "05/07/2026"
    assert _parse_tanggal_input("05/07/2026") == "05/07/2026"
    print("_parse_tanggal_input: OK ✅")

    print()
    print("✅ Semua test manual /edit LULUS")
