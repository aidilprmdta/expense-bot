"""
handlers/vision.py
──────────────────
OCR struk belanja menggunakan Google Gemini 2.0 Flash Vision API.

Flow:
  Foto dari Telegram (bytes)
    ↓  PIL.Image  —  koreksi rotasi EXIF, resize jika terlalu besar
    ↓  Gemini 2.0 Flash Vision  —  OCR + ekstraksi terstruktur
    ↓  JSON response  →  parse  →  normalize
    ↓  list[dict]  —  format sama dengan output parse_expense()
    ↓  siap di-append ke Google Sheets

Fungsi publik:
  ocr_struk(image_bytes, caption)  → list[dict]
  format_struk_summary(items)       → str  (pesan Telegram siap kirim)
"""

import io
import os
import re
import json
import logging
from datetime import datetime
from typing import Optional

import PIL.Image
import PIL.ExifTags
import google.generativeai as genai

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

GEMINI_MODEL    = "gemini-2.0-flash"
MAX_IMAGE_BYTES = 3_500_000   # resize jika raw bytes > 3.5 MB
MAX_DIMENSION   = 2048        # maks width/height setelah resize (pixels)

KATEGORI_VALID = frozenset(
    {"makan", "transport", "belanja", "kesehatan", "hiburan", "lainnya"}
)

EMOJI_KATEGORI = {
    "makan"    : "🍽️",
    "transport": "🚗",
    "belanja"  : "🛒",
    "kesehatan": "💊",
    "hiburan"  : "🎮",
    "lainnya"  : "📌",
}

# Lazy-init supaya tidak error saat import jika API key belum ada
_model: Optional[genai.GenerativeModel] = None


def _get_model() -> genai.GenerativeModel:
    global _model
    if _model is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY tidak ditemukan!\n"
                "Tambahkan ke .env: GEMINI_API_KEY=AIza..."
            )
        genai.configure(api_key=api_key)
        _model = genai.GenerativeModel(GEMINI_MODEL)
        logger.info(f"[vision] Model '{GEMINI_MODEL}' siap.")
    return _model


# ─────────────────────────────────────────────────────────────
# OCR PROMPT
# ─────────────────────────────────────────────────────────────

def _build_prompt(caption: str = "") -> str:
    """
    Bangun prompt OCR yang komprehensif.
    caption dari user dipakai sebagai konteks tambahan (tgl, nama toko, dll).
    """
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    ctx_line = f'\nKonteks dari user: "{caption.strip()}"' if caption.strip() else ""

    return f"""Kamu adalah AI OCR untuk struk/nota/kwitansi belanja Indonesia.{ctx_line}

Baca foto ini dan ekstrak semua ITEM INDIVIDUAL yang dibeli.

══════════════════════════════════
ATURAN EKSTRAKSI:
1. Ambil hanya item produk/jasa yang dibeli user
2. SKIP baris berikut (jangan masukkan ke output):
   • Total / Subtotal / Grand Total
   • Pajak (PPN 11%, Tax, Service Charge)
   • Diskon keseluruhan / promo member
   • Ongkos kirim / biaya pengiriman
   • Kembalian / change
3. Quantity: jika "2 × Rp 5.000" → harga = 10000 (total per item)
4. Diskon per item: pakai harga setelah diskon
5. Nama item: hapus kode SKU / barcode (misal "123456 Aqua" → "Aqua")
6. Tanggal: ambil dari struk jika tercetak jelas, jika tidak → {hari_ini}

══════════════════════════════════
ATURAN KATEGORI (pilih SATU yang paling tepat):
  makan      → makanan, minuman, kopi, teh, resto, kafe, warung,
               bakery, snack, delivery food, es krim
  transport  → bensin, solar, pertamax, tol, parkir, grab, gojek,
               ojek, bis, kereta, taksi, bbm, spbu
  belanja    → supermarket, indomaret, alfamart, toko, baju, sepatu,
               elektronik, perabot, sabun, deterjen, peralatan rumah
  kesehatan  → obat, vitamin, suplemen, apotek, klinik, dokter,
               puskesmas, lab, periksa, konsultasi
  hiburan    → bioskop, game, streaming, netflix, spotify, konser,
               wisata, liburan, rekreasi, nonton
  lainnya    → laundry, bengkel, service hp, tagihan, pulsa, topup,
               transfer, atau tidak masuk kategori di atas

══════════════════════════════════
OUTPUT: JSON murni saja — tidak ada teks lain, tidak ada markdown.

Format wajib (selalu pakai wrapper "items"):
{{"items": [
  {{"nama": "Nama Item", "harga": 25000, "kategori": "belanja", "tanggal": "{hari_ini}"}}
]}}

Jika foto bukan struk atau tidak ada item terbaca: {{"items": []}}

══════════════════════════════════
CONTOH untuk struk Indomaret:
{{"items": [
  {{"nama": "Aqua 600ml", "harga": 4000, "kategori": "belanja", "tanggal": "{hari_ini}"}},
  {{"nama": "Indomie Goreng", "harga": 3500, "kategori": "makan", "tanggal": "{hari_ini}"}},
  {{"nama": "Sabun Lifebuoy 90g", "harga": 8500, "kategori": "belanja", "tanggal": "{hari_ini}"}}
]}}

CONTOH untuk struk SPBU:
{{"items": [
  {{"nama": "Pertamax 10L", "harga": 135000, "kategori": "transport", "tanggal": "{hari_ini}"}}
]}}

CONTOH untuk nota restoran:
{{"items": [
  {{"nama": "Nasi Goreng Spesial", "harga": 35000, "kategori": "makan", "tanggal": "{hari_ini}"}},
  {{"nama": "Es Teh Manis", "harga": 8000, "kategori": "makan", "tanggal": "{hari_ini}"}}
]}}"""


# ─────────────────────────────────────────────────────────────
# IMAGE PREPARATION
# ─────────────────────────────────────────────────────────────

def _prepare_image(image_bytes: bytes) -> PIL.Image.Image:
    """
    Buka image, koreksi rotasi EXIF, resize jika terlalu besar.

    Kenapa perlu EXIF correction:
      Foto dari HP sering disimpan landscape tapi ada metadata rotasi EXIF
      supaya tampil portrait. Tanpa koreksi, Gemini bisa baca struk miring/terbalik.

    Kenapa perlu resize:
      Gemini Vision punya limit ~4MB per inline image.
      Foto HD dari HP bisa 8-20MB. Resize ke 2048px masih cukup jelas untuk OCR.
    """
    # ── Buka image ────────────────────────────────────────────
    try:
        img = PIL.Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        raise ValueError(f"File gambar tidak bisa dibuka: {e}") from e

    # ── Koreksi rotasi EXIF ───────────────────────────────────
    try:
        # Cari orientation tag di EXIF
        exif_data = img._getexif()
        if exif_data:
            orientation_key = next(
                (k for k, v in PIL.ExifTags.TAGS.items() if v == "Orientation"),
                None,
            )
            if orientation_key and orientation_key in exif_data:
                orientation = exif_data[orientation_key]
                ROTATE_MAP = {
                    3: PIL.Image.ROTATE_180,
                    6: PIL.Image.ROTATE_270,
                    8: PIL.Image.ROTATE_90,
                }
                if orientation in ROTATE_MAP:
                    img = img.transpose(ROTATE_MAP[orientation])
                    logger.debug(f"[vision] EXIF rotation applied: {orientation}")
    except (AttributeError, Exception):
        # Tidak semua format punya EXIF (PNG, WebP, dll) — skip saja
        pass

    # ── Konversi ke RGB (hapus alpha channel jika ada) ────────
    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    # ── Resize jika resolusi terlalu tinggi ───────────────────
    w, h = img.size
    if w > MAX_DIMENSION or h > MAX_DIMENSION:
        ratio     = min(MAX_DIMENSION / w, MAX_DIMENSION / h)
        new_w     = int(w * ratio)
        new_h     = int(h * ratio)
        img       = img.resize((new_w, new_h), PIL.Image.LANCZOS)
        logger.info(f"[vision] Resize: {w}×{h} → {new_w}×{new_h}")

    return img


# ─────────────────────────────────────────────────────────────
# GEMINI VISION CALL
# ─────────────────────────────────────────────────────────────

async def _call_gemini_vision(img: PIL.Image.Image, caption: str = "") -> str:
    """
    Kirim image ke Gemini Vision, return raw JSON string.

    Menggunakan generate_content_async (native async dari google-generativeai)
    sehingga tidak perlu asyncio.to_thread() dan tidak memblock event loop.
    """
    model  = _get_model()
    prompt = _build_prompt(caption)

    try:
        response = await model.generate_content_async(
            [prompt, img],
            generation_config=genai.GenerationConfig(
                temperature=0.1,           # rendah = deterministik
                max_output_tokens=4096,    # cukup untuk struk panjang
                response_mime_type="application/json",  # paksa output JSON
            ),
        )
    except Exception as e:
        logger.error(f"[vision] Gemini API error: {e}")
        raise RuntimeError(f"Gagal memanggil Gemini Vision: {e}") from e

    # ── Cek safety filter ─────────────────────────────────────
    if not response.candidates:
        feedback = getattr(response, "prompt_feedback", None)
        reason   = getattr(feedback, "block_reason", "tidak diketahui")
        raise RuntimeError(f"Request diblokir oleh Gemini: {reason}")

    raw = response.text
    if not raw or not raw.strip():
        raise RuntimeError("Gemini mengembalikan respons kosong.")

    logger.debug(f"[vision] Raw response ({len(raw)} chars): {raw[:200]}...")
    return raw.strip()


# ─────────────────────────────────────────────────────────────
# JSON PARSING
# ─────────────────────────────────────────────────────────────

def _parse_json_safe(raw: str) -> dict | list:
    """
    Parse JSON Gemini dengan 3 fallback strategy.
    Walau pakai response_mime_type JSON, kadang masih ada karakter aneh.
    """
    # Strategy 1: parse langsung
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Strategy 3: regex extract JSON object pertama
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"[vision] Gagal parse JSON, return kosong. Raw: {raw[:150]}")
    return {"items": []}


# ─────────────────────────────────────────────────────────────
# NORMALISASI
# ─────────────────────────────────────────────────────────────

def _normalize_items(raw_items: list) -> list[dict]:
    """Normalize dan validasi setiap item dari Gemini output."""
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    result   = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        nama     = _norm_nama(item.get("nama", ""))
        harga    = _norm_harga(item.get("harga", 0))
        kategori = _norm_kategori(item.get("kategori", "belanja"))
        tanggal  = _norm_tanggal(item.get("tanggal", hari_ini))

        # Skip item dengan nama tidak valid
        if not nama or nama.lower() in {"", "none", "null", "item", "-"}:
            continue

        result.append({
            "nama"    : nama,
            "harga"   : harga,
            "kategori": kategori,
            "tanggal" : tanggal,
        })

    return result


def _norm_nama(v) -> str:
    if not v:
        return ""
    nama = str(v).strip()
    # Hapus kode SKU/barcode di awal (pola: angka 4-13 digit diikuti spasi)
    nama = re.sub(r"^\d{4,13}\s+", "", nama)
    # Title case
    return nama.title()


def _norm_harga(v) -> int:
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return max(0, v)
    if isinstance(v, float):
        return max(0, int(round(v)))
    if isinstance(v, str):
        s = str(v).lower().strip()
        s = s.replace("rp", "").replace(" ", "")
        # Juta
        if re.search(r"(jt|juta)", s):
            n = re.sub(r"[^\d.,]", "", s).replace(",", ".")
            try:
                return int(float(n) * 1_000_000)
            except ValueError:
                pass
        # Ribu / k
        if re.search(r"(rb|ribu|k\b)", s):
            n = re.sub(r"[^\d.,]", "", s).replace(",", ".")
            try:
                return int(float(n) * 1_000)
            except ValueError:
                pass
        # Angka biasa (hapus titik/koma sebagai separator ribuan)
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
    return 0


def _norm_kategori(v) -> str:
    k = str(v).lower().strip()
    if k in KATEGORI_VALID:
        return k
    FUZZY: dict[str, str] = {
        "food": "makan", "makanan": "makan", "minuman": "makan",
        "restoran": "makan", "kafe": "makan", "cafe": "makan",
        "restaurant": "makan", "kuliner": "makan",
        "supermarket": "belanja", "minimarket": "belanja",
        "toko": "belanja", "grocery": "belanja", "groceries": "belanja",
        "shopping": "belanja", "kebutuhan": "belanja",
        "transportasi": "transport", "bensin": "transport",
        "bbm": "transport", "ojek": "transport", "fuel": "transport",
        "obat": "kesehatan", "dokter": "kesehatan", "health": "kesehatan",
        "apotek": "kesehatan", "pharmacy": "kesehatan",
        "entertainment": "hiburan", "nonton": "hiburan", "game": "hiburan",
    }
    return FUZZY.get(k, "lainnya")


def _norm_tanggal(v) -> str:
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    if not v or str(v).strip() in ("", "null", "none"):
        return hari_ini
    v = str(v).strip()
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return v
    except ValueError:
        pass
    # Coba format lain yang mungkin keluar dari Gemini
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    logger.warning(f"[vision] Format tanggal tidak dikenal: '{v}', pakai hari ini.")
    return hari_ini


# ─────────────────────────────────────────────────────────────
# FUNGSI UTAMA
# ─────────────────────────────────────────────────────────────

async def ocr_struk(image_bytes: bytes, caption: str = "") -> list[dict]:
    """
    OCR foto struk belanja → list item pengeluaran terstruktur.

    Args:
        image_bytes : bytes gambar dari Telegram (JPEG/PNG/WebP)
        caption     : teks caption yang dikirim user bersama foto (opsional)
                      Contoh: "struk alfamart kemarin", "nota makan siang"

    Returns:
        list[dict], tiap item berisi:
          - nama     (str) : "Aqua 600ml"
          - harga    (int) : 4000
          - kategori (str) : "belanja"
          - tanggal  (str) : "2025-06-26"

    Raises:
        ValueError  : Foto tidak valid atau tidak ada item terbaca
        RuntimeError: Gemini API error

    Contoh:
        items = await ocr_struk(photo_bytes, caption="struk indomaret tadi")
        # → [
        #     {"nama": "Aqua 600ml",  "harga": 4000,  "kategori": "belanja", ...},
        #     {"nama": "Indomie Goreng", "harga": 3500, "kategori": "makan", ...},
        #   ]
    """
    if not image_bytes:
        raise ValueError("Image bytes kosong.")

    logger.info(
        f"[vision] OCR dimulai — "
        f"size={len(image_bytes):,} bytes, caption='{caption}'"
    )

    # ── 1. Siapkan image ──────────────────────────────────────
    try:
        img = _prepare_image(image_bytes)
        logger.info(f"[vision] Image siap: {img.size[0]}×{img.size[1]} px, mode={img.mode}")
    except ValueError as e:
        raise
    except Exception as e:
        raise ValueError(f"Gagal memproses gambar: {e}") from e

    # ── 2. Kirim ke Gemini Vision ─────────────────────────────
    raw = await _call_gemini_vision(img, caption)

    # ── 3. Parse JSON ─────────────────────────────────────────
    data = _parse_json_safe(raw)

    # Ekstrak list items dari berbagai format output
    if isinstance(data, dict):
        raw_items = data.get("items", [])
        if isinstance(raw_items, dict):        # edge case: items berupa object bukan array
            raw_items = [raw_items]
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []

    # ── 4. Normalize ──────────────────────────────────────────
    items = _normalize_items(raw_items)

    if not items:
        raise ValueError(
            "Tidak ada item yang berhasil dibaca dari foto.\n\n"
            "Tips:\n"
            "• Pastikan foto terang dan fokus\n"
            "• Foto dari atas, struk lurus (tidak miring)\n"
            "• Seluruh struk masuk dalam frame"
        )

    logger.info(f"[vision] Selesai — {len(items)} item diekstrak.")
    return items


# ─────────────────────────────────────────────────────────────
# FORMAT PESAN TELEGRAM
# ─────────────────────────────────────────────────────────────

def rupiah(angka: int) -> str:
    return f"Rp {angka:,}".replace(",", ".")


def format_struk_summary(items: list[dict], toko: str = "") -> str:
    """
    Format list item struk menjadi pesan konfirmasi Telegram.

    Output contoh (3 item):
      📷 Struk berhasil dibaca!

      🛒 Aqua 600ml          Rp 4.000
      🍽️ Indomie Goreng      Rp 3.500
      🛒 Sabun Lifebuoy 90g  Rp 8.500
      ───────────────────────
      📦 3 item  |  💰 Total: Rp 16.000
      ✅ Tersimpan di Google Sheets
    """
    if not items:
        return "Tidak ada item yang berhasil dibaca."

    header = f"📷 *Struk {'`' + toko + '`' + ' ' if toko else ''}berhasil dibaca!*\n"
    baris  = [header]

    total = 0
    for item in items:
        emoji = EMOJI_KATEGORI.get(item["kategori"], "📌")
        nama  = item["nama"]
        hrg   = item["harga"]
        total += hrg
        # Format: emoji nama (left) ... harga (right), fixed-width via spaces
        baris.append(f"{emoji} {nama}  —  {rupiah(hrg)}")

    baris.append(f"{'─' * 28}")
    baris.append(f"📦 *{len(items)} item*  |  💰 *Total: {rupiah(total)}*")
    baris.append("✅ _Tersimpan ke Google Sheets_")

    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# TEST MANUAL
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(
        format="%(asctime)s | %(levelname)s | %(message)s",
        level=logging.INFO,
    )

    async def run_test(path: str):
        print("=" * 55)
        print(f"TEST OCR STRUK: {path}")
        print("=" * 55)

        with open(path, "rb") as f:
            img_bytes = f.read()

        try:
            items = await ocr_struk(img_bytes, caption="test manual")
            print(f"\n{format_struk_summary(items)}\n")
            print("\nData mentah:")
            for i, item in enumerate(items, 1):
                print(f"  [{i}] {item}")
        except ValueError as e:
            print(f"ValueError: {e}")
        except RuntimeError as e:
            print(f"RuntimeError: {e}")

    # Jalankan: python -m handlers.vision foto_struk.jpg
    if len(sys.argv) < 2:
        print("Usage: python -m handlers.vision <path_to_receipt_image>")
        print("Contoh: python -m handlers.vision struk.jpg")
    else:
        asyncio.run(run_test(sys.argv[1]))
