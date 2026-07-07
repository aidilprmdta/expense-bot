"""
handlers/kategori.py
─────────────────────
Command untuk kelola kategori kustom.

Penggunaan:
  /kategori                    → lihat semua kategori (bawaan + kustom)
  /tambahkategori <nama>       → tambah kategori baru
  /hapuskategori <nama>        → hapus kategori kustom
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from handlers.ai_parser import KATEGORI_VALID, EMOJI_KATEGORI
from handlers.sheets import (
    get_custom_categories,
    add_custom_category,
    remove_custom_category,
)

logger = logging.getLogger(__name__)


async def cmd_kategori(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/kategori — tampilkan semua kategori bawaan + kustom."""
    loading = await update.message.reply_text("📂 Mengambil daftar kategori... ⏳")

    try:
        custom = await get_custom_categories()
    except Exception as e:
        logger.error(f"[/kategori] Gagal ambil kategori kustom: {e}", exc_info=True)
        await loading.edit_text("😔 Gagal mengambil daftar kategori. Coba lagi ya.")
        return

    baris = ["📂 *Daftar Kategori*\n", "*Bawaan:*"]
    for kat in sorted(KATEGORI_VALID):
        emoji = EMOJI_KATEGORI.get(kat, "📌")
        baris.append(f"{emoji} {kat.title()}")

    baris.append("")
    if custom:
        baris.append("*Kustom (buatanmu):*")
        for kat in custom:
            baris.append(f"🏷️ {kat.title()}")
    else:
        baris.append("_Belum ada kategori kustom._")

    baris.append("")
    baris.append("━━━━━━━━━━━━━━━")
    baris.append("Tambah: `/tambahkategori nama`")
    baris.append("Hapus : `/hapuskategori nama`")

    await loading.edit_text("\n".join(baris), parse_mode="Markdown")


async def cmd_tambahkategori(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/tambahkategori <nama> — tambah kategori kustom baru."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚠️ Sebutkan nama kategorinya.\nContoh: `/tambahkategori pendidikan`",
            parse_mode="Markdown",
        )
        return

    nama    = " ".join(args)
    loading = await update.message.reply_text("💾 Menyimpan kategori... ⏳")

    try:
        berhasil, pesan = await add_custom_category(nama)
        icon = "✅" if berhasil else "⚠️"
        await loading.edit_text(f"{icon} {pesan}")
    except Exception as e:
        logger.error(f"[/tambahkategori] Error: {e}", exc_info=True)
        await loading.edit_text("😔 Gagal menyimpan kategori. Coba lagi ya.")


async def cmd_hapuskategori(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/hapuskategori <nama> — hapus kategori kustom."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "⚠️ Sebutkan nama kategori yang mau dihapus.\n"
            "Contoh: `/hapuskategori pendidikan`",
            parse_mode="Markdown",
        )
        return

    nama    = " ".join(args)
    loading = await update.message.reply_text("🗑️ Menghapus kategori... ⏳")

    try:
        berhasil, pesan = await remove_custom_category(nama)
        icon = "✅" if berhasil else "⚠️"
        await loading.edit_text(f"{icon} {pesan}")
    except Exception as e:
        logger.error(f"[/hapuskategori] Error: {e}", exc_info=True)
        await loading.edit_text("😔 Gagal menghapus kategori. Coba lagi ya.")
