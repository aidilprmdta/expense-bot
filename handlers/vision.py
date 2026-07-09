"""
handlers/vision.py
──────────────────
OCR struk belanja via Groq Vision.

Model: llama-3.2-11b-vision-preview
  ✅ Support vision (image_url + base64)
  ✅ Free tier Groq

FIX: llama-3.2-11b-vision-preview TIDAK support response_format=json_object
     saat dipakai dengan input gambar → Groq lempar 400 JSON validate failed.
     Solusi: hapus response_format, andalkan prompt + _parse_json_safe().
"""

import io
import os
import re
import json
import base64
import logging
from datetime import datetime
from typing import Optional

import PIL.Image
import PIL.ExifTags
from groq import AsyncGroq

# ─────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

VISION_MODEL  = "llama-3.2-11b-vision-preview"
MAX_DIMENSION = 2048
MAX_MB        = 18

KATEGORI_VALID = frozenset(
    {"makan", "transport", "belanja", "kesehatan", "hiburan", "pemasukan", "lainnya"}
)
EMOJI_KATEGORI = {
    "makan"    : "🍽️",
    "transport": "🚗",
    "belanja"  : "🛒",
    "kesehatan": "💊",
    "hiburan"  : "🎮",
    "pemasukan": "💰",
    "lainnya"  : "📌",
}

_client: Optional[AsyncGroq] = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY tidak ditemukan!\n"
                "Pastikan sudah ada di .env: GROQ_API_KEY=gsk_..."
            )
        _client = AsyncGroq(api_key=api_key)
        logger.info(f"[vision] Groq client siap — model: {VISION_MODEL}")
    return _client


# ─────────────────────────────────────────────────────────────
# OCR PROMPT
# ─────────────────────────────────────────────────────────────

def _build_prompt(caption: str = "") -> str:
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    ctx = f'\nKonteks tambahan dari user: "{caption.strip()}"' if caption.strip() else ""

    return f"""Kamu adalah AI asisten pembaca struk/nota/kwitansi belanja di Indonesia.{ctx}

Tugasmu adalah membaca gambar ini dan mengekstrak semua ITEM INDIVIDUAL yang dibeli.

ATURAN EKSTRAKSI:
1. Ambil HANYA item produk/jasa (nama item dan harganya).
2. SKIP (Abaikan): Total, Subtotal, Pajak/PPN, Diskon total, Ongkir, Kembalian, dan Poin.
3. Qty: Jika tertulis "2 × Rp 5.000", maka harga = 10000 (total per baris item).
4. Diskon per item: pakai harga SETELAH didiskon.
5. Nama item: hapus kode SKU/barcode di awal ("123456 Aqua" → "Aqua").
6. Tanggal: ambil dari struk jika ada. Jika tidak ada di gambar, gunakan: {hari_ini}

KATEGORI (pilih SATU yang paling cocok untuk tiap item):
- pemasukan: gaji, bonus, refund, cashback, transfer masuk
- makan: makanan, minuman, kopi, warung, resto, kafe, snack
- transport: bensin, pertamax, solar, tol, parkir, grab, gojek, tiket
- belanja: supermarket, indomaret, alfamart, baju, elektronik, sabun
- kesehatan: obat, vitamin, apotek, klinik, dokter, rumah sakit
- hiburan: bioskop, game, streaming, konser, wisata
- lainnya: laundry, bengkel, tagihan, pulsa, atau yang tidak masuk kategori lain

CRITICAL INSTRUCTION:
You MUST output the result in a valid JSON format only. Do not include any markdown formatting (like ```json), conversational text, or explanation outside the JSON block.
Use this EXACT JSON structure:
{{"items": [{{"nama": "string", "harga": integer, "kategori": "string", "tanggal": "YYYY-MM-DD"}}]}}

If no items are found or the image is not a receipt, output exactly:
{{"items": []}}"""


# ─────────────────────────────────────────────────────────────
# IMAGE PREPARATION
# ─────────────────────────────────────────────────────────────

def _prepare_image(image_bytes: bytes) -> tuple[bytes, str]:
    """Buka, koreksi EXIF, resize, compress → return (bytes, media_type)."""
    try:
        img = PIL.Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        raise ValueError(f"Gambar tidak bisa dibuka: {e}") from e

    fmt        = (img.format or "JPEG").upper()
    media_type = "image/png" if fmt == "PNG" else "image/jpeg"

    # Koreksi rotasi EXIF
    try:
        exif = img._getexif()
        if exif:
            ori_key = next(
                (k for k, v in PIL.ExifTags.TAGS.items() if v == "Orientation"), None
            )
            if ori_key and ori_key in exif:
                ROTATE = {3: PIL.Image.ROTATE_180, 6: PIL.Image.ROTATE_270, 8: PIL.Image.ROTATE_90}
                if exif[ori_key] in ROTATE:
                    img = img.transpose(ROTATE[exif[ori_key]])
    except Exception:
        pass

    # Convert ke RGB
    if img.mode in ("RGBA", "P", "LA"):
        img        = img.convert("RGB")
        media_type = "image/jpeg"

    # Resize jika terlalu besar
    w, h = img.size
    if w > MAX_DIMENSION or h > MAX_DIMENSION:
        ratio = min(MAX_DIMENSION / w, MAX_DIMENSION / h)
        img   = img.resize((int(w * ratio), int(h * ratio)), PIL.Image.LANCZOS)
        logger.info(f"[vision] Resize: {w}×{h} → {img.size[0]}×{img.size[1]}")

    # Compress
    buf      = io.BytesIO()
    quality  = 85
    save_fmt = "PNG" if media_type == "image/png" else "JPEG"

    if save_fmt == "JPEG":
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        while buf.tell() > MAX_MB * 1024 * 1024 and quality > 40:
            quality -= 10
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
        if quality < 85:
            logger.info(f"[vision] Compressed quality → {quality}")
    else:
        img.save(buf, format="PNG", optimize=True)

    out_bytes = buf.getvalue()
    logger.info(
        f"[vision] Image siap: {img.size[0]}×{img.size[1]}px | "
        f"{media_type} | {len(out_bytes)/1024:.1f} KB"
    )
    return out_bytes, media_type


# ─────────────────────────────────────────────────────────────
# GROQ VISION CALL
# ─────────────────────────────────────────────────────────────

async def _call_groq_vision(
    img_bytes : bytes,
    media_type: str,
    caption   : str = "",
) -> str:
    """
    Kirim image ke Groq Vision dan return raw string response.

    PENTING — TIDAK pakai response_format=json_object:
      llama-3.2-11b-vision-preview tidak support response_format
      saat request mengandung image → error 400 "JSON validate failed".
      JSON output dijamin oleh prompt (CRITICAL INSTRUCTION) +
      di-parse oleh _parse_json_safe() dengan 3 fallback strategy.
    """
    client   = _get_client()
    prompt   = _build_prompt(caption)
    b64_data = base64.b64encode(img_bytes).decode("utf-8")
    data_uri = f"data:{media_type};base64,{b64_data}"

    try:
        response = await client.chat.completions.create(
            model       = VISION_MODEL,
            # ✅ TIDAK ada response_format di sini — ini yang fix error 400
            temperature = 0.1,
            max_tokens  = 4096,
            messages    = [
                {
                    "role"   : "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type"     : "image_url",
                            "image_url": {"url": data_uri},
                        },
                    ],
                }
            ],
        )
    except Exception as e:
        logger.error(f"[vision] Groq Vision error: {e}")
        raise RuntimeError(f"Gagal memanggil Groq Vision: {e}") from e

    raw = response.choices[0].message.content
    if not raw or not raw.strip():
        raise RuntimeError("Groq mengembalikan respons kosong.")

    logger.debug(f"[vision] Raw ({len(raw)} chars): {raw[:200]}")
    return raw.strip()


# ─────────────────────────────────────────────────────────────
# JSON PARSING — 3 fallback strategy
# ─────────────────────────────────────────────────────────────

def _parse_json_safe(raw: str) -> dict | list:
    """Parse JSON dengan 3 fallback — tahan terhadap output LLM yang tidak sempurna."""
    # 1. Parse langsung
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # 2. Strip markdown code fences (```json ... ```)
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # 3. Regex extract JSON object pertama
    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    logger.warning(f"[vision] Gagal parse JSON. Raw: {raw[:150]}")
    return {"items": []}


# ─────────────────────────────────────────────────────────────
# NORMALISASI
# ─────────────────────────────────────────────────────────────

def _normalize_items(raw_items: list) -> list[dict]:
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    result   = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        nama     = _norm_nama(item.get("nama", ""))
        harga    = _norm_harga(item.get("harga", 0))
        kategori = _norm_kategori(item.get("kategori", "belanja"))
        tanggal  = _norm_tanggal(item.get("tanggal", hari_ini))
        if not nama or nama.lower() in {"", "none", "null", "item", "-"}:
            continue
        result.append({"nama": nama, "harga": harga, "kategori": kategori, "tanggal": tanggal})
    return result


def _norm_nama(v) -> str:
    if not v: return ""
    s = str(v).strip()
    s = re.sub(r"^\d{4,13}\s+", "", s)   # hapus kode SKU di awal
    return s.title()


def _norm_harga(v) -> int:
    if isinstance(v, bool):  return 0
    if isinstance(v, int):   return max(0, v)
    if isinstance(v, float): return max(0, int(round(v)))
    if isinstance(v, str):
        s = str(v).lower().strip().replace("rp", "").replace(" ", "")
        if re.search(r"(jt|juta)", s):
            n = re.sub(r"[^\d.,]", "", s).replace(",", ".")
            try: return int(float(n) * 1_000_000)
            except ValueError: pass
        if re.search(r"(rb|ribu|k\b)", s):
            n = re.sub(r"[^\d.,]", "", s).replace(",", ".")
            try: return int(float(n) * 1_000)
            except ValueError: pass
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
    return 0


def _norm_kategori(v) -> str:
    k = str(v).lower().strip()
    if k in KATEGORI_VALID: return k
    FUZZY = {
        "food": "makan", "makanan": "makan", "minuman": "makan",
        "restoran": "makan", "kafe": "makan", "restaurant": "makan",
        "supermarket": "belanja", "minimarket": "belanja",
        "grocery": "belanja", "groceries": "belanja", "shopping": "belanja",
        "transportasi": "transport", "bensin": "transport", "bbm": "transport",
        "obat": "kesehatan", "dokter": "kesehatan", "apotek": "kesehatan",
        "entertainment": "hiburan", "nonton": "hiburan",
    }
    return FUZZY.get(k, "lainnya")


def _norm_tanggal(v) -> str:
    hari_ini = datetime.now().strftime("%Y-%m-%d")
    if not v or str(v).strip() in ("", "null", "none"): return hari_ini
    v = str(v).strip()
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return v
    except ValueError: pass
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try: return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError: continue
    return hari_ini


# ─────────────────────────────────────────────────────────────
# FUNGSI UTAMA
# ─────────────────────────────────────────────────────────────

async def ocr_struk(image_bytes: bytes, caption: str = "") -> list[dict]:
    """
    OCR foto struk → list item pengeluaran terstruktur.

    Args:
        image_bytes : bytes foto dari Telegram (JPEG/PNG)
        caption     : teks caption user — jadi konteks tambahan AI

    Returns:
        list[dict] : [{"nama", "harga", "kategori", "tanggal"}, ...]

    Raises:
        ValueError  : foto tidak valid / tidak ada item terbaca
        RuntimeError: Groq API error
    """
    if not image_bytes:
        raise ValueError("Image bytes kosong.")

    logger.info(f"[vision] OCR mulai — {len(image_bytes):,} bytes | caption='{caption}'")

    # 1. Prepare image
    try:
        ready_bytes, media_type = _prepare_image(image_bytes)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Gagal memproses gambar: {e}") from e

    # 2. Groq Vision (TANPA response_format)
    raw = await _call_groq_vision(ready_bytes, media_type, caption)

    # 3. Parse JSON
    data = _parse_json_safe(raw)
    if isinstance(data, dict):
        raw_items = data.get("items", [])
        if isinstance(raw_items, dict): raw_items = [raw_items]
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []

    # 4. Normalize
    items = _normalize_items(raw_items)

    if not items:
        raise ValueError(
            "Tidak ada item yang berhasil dibaca dari foto ini.\n\n"
            "Tips foto yang bagus:\n"
            "• Pencahayaan cukup terang\n"
            "• Struk lurus, tidak miring\n"
            "• Seluruh struk masuk dalam frame\n"
            "• Hindari bayangan & pantulan"
        )

    logger.info(f"[vision] ✅ {len(items)} item diekstrak.")
    return items


# ─────────────────────────────────────────────────────────────
# FORMAT PESAN TELEGRAM
# ─────────────────────────────────────────────────────────────

def rupiah(n: int) -> str:
    return f"Rp {n:,}".replace(",", ".")


def format_struk_summary(items: list[dict], toko: str = "") -> str:
    if not items: return "Tidak ada item yang berhasil dibaca."
    nama_toko = f"`{toko}` " if toko else ""
    baris     = [f"📷 *Struk {nama_toko}berhasil dibaca!*\n"]
    total     = 0
    for item in items:
        emoji  = EMOJI_KATEGORI.get(item["kategori"], "📌")
        total += item["harga"]
        baris.append(f"{emoji} {item['nama']}  —  {rupiah(item['harga'])}")
    baris.append(f"{'─' * 28}")
    baris.append(f"📦 *{len(items)} item*  |  💰 *Total: {rupiah(total)}*")
    baris.append("✅ _Tersimpan ke Google Sheets_")
    return "\n".join(baris)


# ─────────────────────────────────────────────────────────────
# TEST: python -m handlers.vision struk.jpg
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
            print("Data mentah:")
            for i, item in enumerate(items, 1):
                print(f"  [{i}] {item}")
        except ValueError as e:
            print(f"\nValueError: {e}")
        except RuntimeError as e:
            print(f"\nRuntimeError: {e}")

    if len(sys.argv) < 2:
        print("Usage: python -m handlers.vision <path_gambar>")
    else:
        asyncio.run(run_test(sys.argv[1]))