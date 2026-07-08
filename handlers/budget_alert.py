"""
handlers/budget_alert.py
──────────────────────────
Cek otomatis setelah transaksi baru dicatat: apakah pengeluaran bulan
ini baru saja melewati 80% atau 100% dari budget bulanan?

Kalau iya, return pesan peringatan yang bisa langsung dikirim bot
sebagai pesan tambahan setelah konfirmasi transaksi.

Dipakai di main.py, dipanggil setelah append_expenses_batch().
"""

import logging

from handlers.sheets import get_budget, get_all_records
from handlers.rekap import _aggregate, resolve_periode, rupiah

logger = logging.getLogger(__name__)

# (threshold %, label, emoji) — dicek dari yang paling tinggi dulu,
# supaya kalau sekali transaksi langsung lompat dari 70% ke 110%,
# yang muncul cuma alert 100% (paling relevan), bukan dobel alert.
THRESHOLDS = [
    (100, "Budget Bulan Ini Terlampaui!", "🚨"),
    (80,  "Mendekati Batas Budget",        "⚠️"),
]


async def cek_budget_alert(items: list[dict]) -> str | None:
    """
    Cek apakah transaksi yang baru saja dicatat (items) membuat
    pengeluaran bulan ini melewati salah satu threshold budget.

    Args:
        items: list item yang baru di-append (dari parse_expense/ocr_struk),
               masing-masing punya "harga" dan "kategori".

    Returns:
        Pesan alert (Markdown) kalau ada threshold baru yang terlampaui,
        None kalau tidak ada (belum sampai threshold, atau budget belum diset).
    """
    try:
        budget = await get_budget()
    except Exception as e:
        logger.warning(f"[budget_alert] Gagal ambil budget: {e}")
        return None

    if budget <= 0:
        return None  # budget belum diset, tidak ada yang bisa dicek

    # Nominal pengeluaran dari transaksi yang baru saja masuk
    # (pemasukan tidak mempengaruhi budget, jadi dikecualikan)
    nominal_baru = sum(
        int(i.get("harga", 0))
        for i in items
        if str(i.get("kategori", "")).lower() != "pemasukan"
    )
    if nominal_baru <= 0:
        return None

    try:
        records = await get_all_records()
    except Exception as e:
        logger.warning(f"[budget_alert] Gagal baca Sheets: {e}")
        return None

    start, end, _ = resolve_periode("bulan")
    data = _aggregate(records, start, end)

    total_sesudah = data["pengeluaran_total"]
    total_sebelum = total_sesudah - nominal_baru

    persen_sebelum = (total_sebelum / budget) * 100
    persen_sesudah = (total_sesudah / budget) * 100

    for threshold, label, emoji in THRESHOLDS:
        # Hanya alert kalau transaksi INI yang bikin melewati threshold —
        # bukan alert berulang tiap kali transaksi baru dicatat setelah lewat.
        if persen_sebelum < threshold <= persen_sesudah:
            sisa = budget - total_sesudah
            pesan = (
                f"{emoji} *{label}*\n\n"
                f"Pengeluaran bulan ini: {rupiah(total_sesudah)} dari "
                f"{rupiah(budget)} ({persen_sesudah:.0f}%)\n"
            )
            if sisa >= 0:
                pesan += f"💰 Sisa budget: {rupiah(sisa)}"
            else:
                pesan += f"📛 Sudah lewat {rupiah(abs(sisa))} dari budget bulan ini."
            return pesan

    return None


# ─────────────────────────────────────────────────────────────
# TEST MANUAL: python -m handlers.budget_alert
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from unittest.mock import AsyncMock, patch

    async def test():
        # ── Skenario 1: budget belum diset -> tidak ada alert ──
        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=0)):
            hasil = await cek_budget_alert([{"harga": 50000, "kategori": "makan"}])
            assert hasil is None
        print("Skenario 1 (budget belum diset): OK ✅ -> None")

        # ── Skenario 2: transaksi cuma pemasukan -> tidak ada alert ──
        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=1_000_000)):
            hasil = await cek_budget_alert([{"harga": 5_000_000, "kategori": "pemasukan"}])
            assert hasil is None
        print("Skenario 2 (cuma pemasukan): OK ✅ -> None")

        # ── Skenario 3: baru lewat 80% -> harus alert ──
        # Budget 1jt. Sebelum transaksi ini: 750rb (75%). Transaksi baru: 100rb -> jadi 850rb (85%)
        DUMMY_RECORDS = [
            {"Tanggal": "01/07/2026", "Nama Item": "A", "Kategori": "Makan", "Harga": 750000},
            {"Tanggal": "07/07/2026", "Nama Item": "B", "Kategori": "Makan", "Harga": 100000},
        ]
        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=1_000_000)), \
             patch(f"{__name__}.get_all_records", new=AsyncMock(return_value=DUMMY_RECORDS)):
            hasil = await cek_budget_alert([{"harga": 100000, "kategori": "makan"}])
            assert hasil is not None
            assert "80" in hasil or "Mendekati" in hasil
        print("Skenario 3 (baru lewat 80%): OK ✅")
        print(hasil)

        # ── Skenario 4: sudah di atas 80% SEBELUM transaksi ini -> TIDAK alert lagi ──
        # Sebelum: 850rb (85%, sudah lewat 80% sebelumnya). Transaksi baru: 20rb -> 870rb (87%)
        DUMMY_RECORDS2 = [
            {"Tanggal": "01/07/2026", "Nama Item": "A", "Kategori": "Makan", "Harga": 850000},
            {"Tanggal": "07/07/2026", "Nama Item": "B", "Kategori": "Makan", "Harga": 20000},
        ]
        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=1_000_000)), \
             patch(f"{__name__}.get_all_records", new=AsyncMock(return_value=DUMMY_RECORDS2)):
            hasil2 = await cek_budget_alert([{"harga": 20000, "kategori": "makan"}])
            assert hasil2 is None
        print("Skenario 4 (sudah lewat 80% sebelumnya, tidak alert ulang): OK ✅")

        # ── Skenario 5: lompat langsung dari 70% ke 110% -> harus alert 100%, bukan 80% ──
        DUMMY_RECORDS3 = [
            {"Tanggal": "01/07/2026", "Nama Item": "A", "Kategori": "Makan", "Harga": 700000},
            {"Tanggal": "07/07/2026", "Nama Item": "B", "Kategori": "Belanja", "Harga": 400000},
        ]
        with patch(f"{__name__}.get_budget", new=AsyncMock(return_value=1_000_000)), \
             patch(f"{__name__}.get_all_records", new=AsyncMock(return_value=DUMMY_RECORDS3)):
            hasil3 = await cek_budget_alert([{"harga": 400000, "kategori": "belanja"}])
            assert hasil3 is not None
            assert "Terlampaui" in hasil3
        print("Skenario 5 (lompat ke >100%, alert paling severe): OK ✅")
        print(hasil3)

        print()
        print("SEMUA TEST budget_alert LULUS ✅")

    asyncio.run(test())
