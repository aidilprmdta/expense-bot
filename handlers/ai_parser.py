"""
handlers/ai_parser.py
─────────────────────
Parse teks pengeluaran Bahasa Indonesia → dict terstruktur
menggunakan Groq API (llama-3.1-70b-versatile).

Fungsi utama:
  parse_expense(teks)       → list[dict]   (satu atau banyak item)
  format_konfirmasi(items)  → str          (pesan Telegram siap kirim)

Contoh:
  items = await parse_expense("beli kopi 25rb sama roti bakar 15rb")
  # → [
  #     {"nama": "Kopi", "harga": 25000, "kategori": "makan", "tanggal": "2025-06-26"},
  #     {"nama": "Roti Bakar", "harga": 15000, "kategori": "makan", "tanggal": "2025-06-26"},
  #   ]
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta

from groq import AsyncGroq

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

MODEL = "llama-3.1-8b-instant"
# Alternatif kalau model di atas tidak tersedia:
# MODEL = "llama-3.3-70b-versatile"
# MODEL = "llama3-70b-8192"

KATEGORI_VALID = frozenset(
    {"makan", "transport", "belanja", "kesehatan", "hiburan", "lainnya"}
)

# Inisialisasi client satu kali (lebih efisien)
_client: AsyncGroq | None = None


def get_client() -> AsyncGroq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY tidak ditemukan! Pastikan sudah di .env"
            )
        _client = AsyncGroq(api_key=api_key)
    return _client


# ─────────────────────────────────────────────────────────────
# SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    """
    Bangun system prompt dengan tanggal hari ini yang selalu akurat.
    Dipanggil setiap request supaya tanggal tidak stale.
    """
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    kemarin  = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    return f"""Kamu adalah AI pencatat keuangan pribadi. \
Ekstrak data pengeluaran dari kalimat Bahasa Indonesia.

TANGGAL HARI INI: {hari_ini}

═══════════════════════════════
OUTPUT: hanya JSON, tidak ada teks lain, tidak ada markdown, tidak ada penjelasan.
═══════════════════════════════

FORMAT WAJIB (selalu gunakan wrapper "items"):
{{"items": [
  {{"nama": "...", "harga": 0, "kategori": "...", "tanggal": "..."}}
]}}

═══════════════════════════════
ATURAN HARGA (WAJIB integer Rupiah, bukan string):
  "25rb" / "25k" / "25ribu"      → 25000
  "2.5jt" / "2,5 juta"           → 2500000
  "25.000" / "25,000"            → 25000
  "250 ribu" / "dua ratus lima"  → 250000
  Tidak disebutkan                → 0
═══════════════════════════════
ATURAN KATEGORI (pilih tepat satu):
  makan      → makanan, minuman, kopi, teh, warung, restoran, kafe,
               delivery food, snack, bakery, es krim
  transport  → bensin, parkir, ojek, grab, gojek, bis, kereta,
               tol, taksi, bbm, pertamax
  belanja    → supermarket, indomaret, alfamart, toko, baju, sepatu,
               elektronik, perabot, keperluan rumah, sabun, deterjen
  kesehatan  → obat, dokter, klinik, puskesmas, apotek, vitamin,
               suplemen, periksa, konsultasi
  hiburan    → bioskop, game, streaming, netflix, spotify, nonton,
               konser, liburan, jalan-jalan, wisata
  lainnya    → tagihan, listrik, air, internet, pulsa, transfer,
               top-up, cicilan, atau tidak masuk kategori di atas
═══════════════════════════════
ATURAN TANGGAL (format YYYY-MM-DD):
  Tidak disebutkan         → {hari_ini}
  "kemarin"               → {kemarin}
  "tadi pagi/siang/malam" → {hari_ini}
  "minggu lalu"           → hitung mundur 7 hari dari {hari_ini}
═══════════════════════════════
CONTOH INPUT → OUTPUT:

Input: "beli kopi 25rb"
Output: {{"items": [{{"nama": "Kopi", "harga": 25000, "kategori": "makan", "tanggal": "{hari_ini}"}}]}}

Input: "isi bensin pertamax 80.000 kemarin"
Output: {{"items": [{{"nama": "Bensin Pertamax", "harga": 80000, "kategori": "transport", "tanggal": "{kemarin}"}}]}}

Input: "kopi 15rb sama roti bakar 20rb"
Output: {{"items": [
  {{"nama": "Kopi", "harga": 15000, "kategori": "makan", "tanggal": "{hari_ini}"}},
  {{"nama": "Roti Bakar", "harga": 20000, "kategori": "makan", "tanggal": "{hari_ini}"}}
]}}

Input: "belanja alfamart: air mineral 5rb, sabun 12rb, rinso 25rb"
Output: {{"items": [
  {{"nama": "Air Mineral", "harga": 5000, "kategori": "belanja", "tanggal": "{hari_ini}"}},
  {{"nama": "Sabun", "harga": 12000, "kategori": "belanja", "tanggal": "{hari_ini}"}},
  {{"nama": "Rinso", "harga": 25000, "kategori": "belanja", "tanggal": "{hari_ini}"}}
]}}"""


# ─────────────────────────────────────────────────────────────
# FUNGSI UTAMA
# ─────────────────────────────────────────────────────────────

async def parse_expense(teks: str) -> list[dict]:
    """
    Parse teks pengeluaran Bahasa Indonesia format bebas.

    Args:
        teks: Kalimat pengeluaran.
              Contoh: "beli kopi 25rb sama roti bakar 15rb"

    Returns:
        list[dict], masing-masing item berisi:
          - nama     (str)  : nama item, title-case
          - harga    (int)  : harga dalam Rupiah, selalu integer
          - kategori (str)  : makan/transport/belanja/kesehatan/hiburan/lainnya
          - tanggal  (str)  : format "YYYY-MM-DD"

    Raises:
        ValueError  : Teks kosong atau tidak ada data pengeluaran.
        RuntimeError: Gagal menghubungi Groq API.

    Contoh:
        items = await parse_expense("makan siang nasi padang 35rb")
        # → [{"nama": "Nasi Padang", "harga": 35000,
        #      "kategori": "makan", "tanggal": "2025-06-26"}]
    """
    # ── Validasi input ─────────────────────────────────────────
    teks = teks.strip()
    if not teks:
        raise ValueError("Teks kosong — tidak ada yang bisa diparsing.")

    logger.info(f"[ai_parser] Memproses: '{teks}'")

    # ── Panggil Groq API ───────────────────────────────────────
    raw_response = await _call_groq(teks)
    logger.debug(f"[ai_parser] Raw response: {raw_response}")

    # ── Parse JSON ─────────────────────────────────────────────
    data = _parse_json_safe(raw_response)

    # ── Normalize & validasi ───────────────────────────────────
    items = _normalize_items(data)

    if not items:
        raise ValueError(
            "AI tidak menemukan data pengeluaran yang valid dari teks ini."
        )

    logger.info(f"[ai_parser] Berhasil parse {len(items)} item: {items}")
    return items


# ─────────────────────────────────────────────────────────────
# GROQ API CALL
# ─────────────────────────────────────────────────────────────

async def _call_groq(teks: str) -> str:
    """
    Kirim request ke Groq dan return raw string response.
    Menggunakan response_format json_object agar output selalu JSON.
    """
    try:
        client   = get_client()
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user",   "content": teks},
            ],
            temperature=0.1,   # rendah = konsisten, deterministik
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f"[ai_parser] Groq error: {e}")
        raise RuntimeError(f"Gagal menghubungi AI: {e}") from e


# ─────────────────────────────────────────────────────────────
# JSON PARSING
# ─────────────────────────────────────────────────────────────

def _parse_json_safe(raw: str) -> dict | list:
    """
    Parse JSON dengan beberapa fallback strategy.
    LLM kadang menambah markdown atau teks sebelum/sesudah JSON.
    """
    # Strategy 1: parse langsung
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown code block jika ada
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: extract JSON object pertama dari string
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Response dari AI bukan JSON yang valid.\n"
        f"Response: {raw[:200]}"
    )


# ─────────────────────────────────────────────────────────────
# NORMALISASI
# ─────────────────────────────────────────────────────────────

def _normalize_items(data: dict | list) -> list[dict]:
    """
    Ambil list item dari berbagai format output LLM.

    Format yang dihandle:
      {"items": [...]}    → format yang diinginkan
      {"item": {...}}     → satu item tanpa wrapper array
      [{...}, {...}]      → langsung array (tanpa wrapper)
      {...}               → satu item langsung (tanpa wrapper)
    """
    if isinstance(data, dict):
        if "items" in data:
            raw_items = data["items"]
            if isinstance(raw_items, dict):   # items berisi dict, bukan list
                raw_items = [raw_items]
        elif "item" in data:
            raw_items = [data["item"]]
        else:
            # Coba treat seluruh dict sebagai satu item
            raw_items = [data]
    elif isinstance(data, list):
        raw_items = data
    else:
        return []

    # Normalize tiap item
    result = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        normalized = {
            "nama"     : _normalize_nama(item.get("nama", "Item")),
            "harga"    : _normalize_harga(item.get("harga", 0)),
            "kategori" : _normalize_kategori(item.get("kategori", "lainnya")),
            "tanggal"  : _normalize_tanggal(item.get("tanggal", "")),
        }
        result.append(normalized)

    return result


def _normalize_nama(nilai) -> str:
    """Pastikan nama berupa string yang bersih, title-case."""
    nama = str(nilai).strip()
    if not nama or nama.lower() in {"item", "none", "null", ""}:
        return "Item Tanpa Nama"
    return nama.title()


def _normalize_harga(nilai) -> int:
    """
    Pastikan harga selalu integer Rupiah ≥ 0.

    Handles:
      int         → langsung dipakai
      float       → dibulatkan ke int
      str "25000" → diparse ke int
      str "25rb"  → fallback manual (seharusnya sudah dilakukan AI)
    """
    if isinstance(nilai, bool):
        return 0
    if isinstance(nilai, int):
        return max(0, nilai)
    if isinstance(nilai, float):
        return max(0, int(nilai))
    if isinstance(nilai, str):
        nilai = nilai.strip().lower()
        # Kalau AI lupa convert (jarang terjadi, tapi antisipasi)
        nilai = nilai.replace("rp", "").replace(" ", "")
        if re.search(r"(jt|juta)", nilai):
            angka = re.sub(r"[^\d.,]", "", nilai).replace(",", ".")
            try:
                return int(float(angka) * 1_000_000)
            except ValueError:
                pass
        if re.search(r"(rb|ribu|k)", nilai):
            angka = re.sub(r"[^\d.,]", "", nilai).replace(",", ".")
            try:
                return int(float(angka) * 1_000)
            except ValueError:
                pass
        # Hapus semua non-digit (titik/koma sebagai separator ribuan)
        digits = re.sub(r"[^\d]", "", nilai)
        return int(digits) if digits else 0
    return 0


def _normalize_kategori(nilai) -> str:
    """
    Pastikan kategori valid. Fuzzy match untuk common variations.
    """
    kat = str(nilai).lower().strip()
    if kat in KATEGORI_VALID:
        return kat

    # Mapping variasi kata yang mungkin keluar dari LLM
    fuzzy: dict[str, str] = {
        # makan
        "food": "makan", "makanan": "makan", "minuman": "makan",
        "restoran": "makan", "kafe": "makan", "cafe": "makan",
        "minum": "makan", "kuliner": "makan",
        # transport
        "transportasi": "transport", "bensin": "transport",
        "bbm": "transport", "ojek": "transport", "grab": "transport",
        "gojek": "transport", "kendaraan": "transport",
        # belanja
        "shopping": "belanja", "grocery": "belanja",
        "groceries": "belanja", "supermarket": "belanja",
        "kebutuhan": "belanja", "rumah tangga": "belanja",
        # kesehatan
        "health": "kesehatan", "medis": "kesehatan",
        "obat": "kesehatan", "dokter": "kesehatan",
        # hiburan
        "entertainment": "hiburan", "rekreasi": "hiburan",
        "nonton": "hiburan", "game": "hiburan",
    }
    return fuzzy.get(kat, "lainnya")


def _normalize_tanggal(nilai: str) -> str:
    """
    Pastikan format tanggal YYYY-MM-DD.
    Fallback ke hari ini jika tidak valid.
    """
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    if not nilai or not isinstance(nilai, str):
        return hari_ini
    nilai = nilai.strip()
    # Validasi format
    try:
        datetime.strptime(nilai, "%Y-%m-%d")
        return nilai
    except ValueError:
        logger.warning(f"[ai_parser] Format tanggal tidak valid: '{nilai}', pakai hari ini.")
        return hari_ini


# ─────────────────────────────────────────────────────────────
# FORMAT PESAN TELEGRAM
# ─────────────────────────────────────────────────────────────

EMOJI_KATEGORI: dict[str, str] = {
    "makan"     : "🍽️",
    "transport" : "🚗",
    "belanja"   : "🛒",
    "kesehatan" : "💊",
    "hiburan"   : "🎮",
    "lainnya"   : "📌",
}


def rupiah(angka: int) -> str:
    """Format integer ke string Rupiah. Contoh: 25000 → 'Rp 25.000'"""
    return f"Rp {angka:,}".replace(",", ".")


def format_konfirmasi(items: list[dict]) -> str:
    """
    Format list item menjadi pesan konfirmasi Telegram yang rapi.

    Single item:
      ✅ Tercatat!
      🍽️ Nasi Padang
      💰 Rp 35.000
      🏷️ Makan
      📅 26 Jun 2025

    Multi item:
      ✅ 3 item tercatat!
      🍽️ Kopi — Rp 25.000
      🍽️ Roti Bakar — Rp 15.000
      🛒 Sabun — Rp 12.000
      ─────────────
      💰 Total: Rp 52.000
    """
    if not items:
        return "Tidak ada item yang berhasil dicatat."

    if len(items) == 1:
        item   = items[0]
        emoji  = EMOJI_KATEGORI.get(item["kategori"], "📌")
        tanggal_fmt = _fmt_tanggal(item["tanggal"])
        return (
            f"✅ *Tercatat!*\n\n"
            f"{emoji} *{item['nama']}*\n"
            f"💰 {rupiah(item['harga'])}\n"
            f"🏷️ {item['kategori'].title()}\n"
            f"📅 {tanggal_fmt}"
        )

    # Multi-item
    baris = [f"✅ *{len(items)} item tercatat!*\n"]
    total = 0
    for item in items:
        emoji  = EMOJI_KATEGORI.get(item["kategori"], "📌")
        baris.append(f"{emoji} {item['nama']} — {rupiah(item['harga'])}")
        total += item["harga"]

    baris.append(f"{'─' * 20}")
    baris.append(f"💰 *Total: {rupiah(total)}*")
    return "\n".join(baris)


def _fmt_tanggal(iso: str) -> str:
    """Ubah '2025-06-26' → '26 Jun 2025'."""
    try:
        dt = datetime.strptime(iso, "%Y-%m-%d")
        bulan = [
            "", "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
            "Jul", "Agu", "Sep", "Okt", "Nov", "Des"
        ]
        return f"{dt.day} {bulan[dt.month]} {dt.year}"
    except ValueError:
        return iso


# ─────────────────────────────────────────────────────────────
# TESTING MANUAL (jalankan: python -m handlers.ai_parser)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    TEST_CASES = [
        "beli kopi 25rb",
        "makan siang nasi padang 35.000",
        "isi bensin pertamax 80k kemarin",
        "kopi 15rb sama roti bakar 20rb",
        "belanja alfamart: air mineral 5rb, sabun 12rb, rinso 25rb",
        "grab ke kantor 18000",
        "beli obat batuk di apotek 45rb",
        "bayar netflix 54ribu",
        "transfer ke mama 500rb",
    ]

    async def run_tests():
        print("=" * 50)
        print("TEST AI PARSER")
        print("=" * 50)
        for teks in TEST_CASES:
            print(f"\nInput : '{teks}'")
            try:
                items = await parse_expense(teks)
                pesan = format_konfirmasi(items)
                print(f"Output:\n{pesan}")
            except Exception as e:
                print(f"ERROR : {e}")
            print("-" * 40)

    asyncio.run(run_tests())
