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
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# Konfigurasi Model
VISION_MODEL    = "gpt-4o-mini" # Model GPT yang cepat, murah, dan pintar baca gambar
MAX_IMAGE_BYTES = 3_500_000   
MAX_DIMENSION   = 2048        

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

_client: Optional[AsyncOpenAI] = None

def _get_client() -> AsyncOpenAI:
    """Inisialisasi client OpenAI yang mengarah ke AgentRouter."""
    global _client
    if _client is None:
        api_key = os.getenv("AGENTROUTER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "AGENTROUTER_API_KEY tidak ditemukan!\n"
                "Tambahkan ke .env: AGENTROUTER_API_KEY=sk-..."
            )
        _client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.agentrouter.org/v1"
        )
        logger.info(f"[vision] AgentRouter Client siap (Model: {VISION_MODEL}).")
    return _client

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
"""

def _prepare_image(image_bytes: bytes) -> PIL.Image.Image:
    """
    Buka image, koreksi rotasi EXIF, resize jika terlalu besar.
    """
    try:
        img = PIL.Image.open(io.BytesIO(image_bytes))
    except Exception as e:
        raise ValueError(f"File gambar tidak bisa dibuka: {e}") from e

    try:
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
        pass

    if img.mode in ("RGBA", "P", "LA"):
        img = img.convert("RGB")

    w, h = img.size
    if w > MAX_DIMENSION or h > MAX_DIMENSION:
        ratio     = min(MAX_DIMENSION / w, MAX_DIMENSION / h)
        new_w     = int(w * ratio)
        new_h     = int(h * ratio)
        img       = img.resize((new_w, new_h), PIL.Image.LANCZOS)
        logger.info(f"[vision] Resize: {w}×{h} → {new_w}×{new_h}")

    return img

async def _call_vision_api(img: PIL.Image.Image, caption: str = "") -> str:
    """
    Kirim image ke API (AgentRouter), return raw JSON string.
    """
    client = _get_client()
    prompt_text = _build_prompt(caption)

    # Convert PIL Image kembali ke bytes lalu ke base64 agar bisa dibaca AI
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG")
    img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

    try:
        response = await client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt_text},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}"
                            }
                        }
                    ]
                }
            ],
            temperature=0.1,
            # Kita paksa AI agar menghasilkan format JSON
            response_format={ "type": "json_object" }
        )
        
        raw = response.choices[0].message.content
        if not raw or not raw.strip():
            raise RuntimeError("AI mengembalikan respons kosong.")
            
        logger.debug(f"[vision] Raw response ({len(raw)} chars): {raw[:200]}...")
        return raw.strip()
        
    except Exception as e:
        logger.error(f"[vision] API error: {e}")
        raise RuntimeError(f"Gagal memanggil AI Vision: {e}") from e

def _parse_json_safe(raw: str) -> dict | list:
    """
    Parse JSON dari AI dengan aman.
    """
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?\s*", "", raw)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", raw)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    logger.warning(f"[vision] Gagal parse JSON. Raw: {raw[:150]}")
    return {"items": []}

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
    nama = re.sub(r"^\d{4,13}\s+", "", nama)
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
        if re.search(r"(jt|juta)", s):
            n = re.sub(r"[^\d.,]", "", s).replace(",", ".")
            try:
                return int(float(n) * 1_000_000)
            except ValueError:
                pass
        if re.search(r"(rb|ribu|k\b)", s):
            n = re.sub(r"[^\d.,]", "", s).replace(",", ".")
            try:
                return int(float(n) * 1_000)
            except ValueError:
                pass
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
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(v, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return hari_ini

async def ocr_struk(image_bytes: bytes, caption: str = "") -> list[dict]:
    """
    OCR foto struk belanja → list item pengeluaran terstruktur.
    """
    if not image_bytes:
        raise ValueError("Image bytes kosong.")

    logger.info(
        f"[vision] OCR dimulai — "
        f"size={len(image_bytes):,} bytes, caption='{caption}'"
    )

    try:
        img = _prepare_image(image_bytes)
        logger.info(f"[vision] Image siap: {img.size[0]}×{img.size[1]} px, mode={img.mode}")
    except ValueError as e:
        raise
    except Exception as e:
        raise ValueError(f"Gagal memproses gambar: {e}") from e

    # ── Panggil fungsi API yang sudah disesuaikan ─────────────
    raw = await _call_vision_api(img, caption)

    data = _parse_json_safe(raw)

    if isinstance(data, dict):
        raw_items = data.get("items", [])
        if isinstance(raw_items, dict):        
            raw_items = [raw_items]
    elif isinstance(data, list):
        raw_items = data
    else:
        raw_items = []

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

def rupiah(angka: int) -> str:
    return f"Rp {angka:,}".replace(",", ".")

def format_struk_summary(items: list[dict], toko: str = "") -> str:
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
        baris.append(f"{emoji} {nama}  —  {rupiah(hrg)}")

    baris.append(f"{'─' * 28}")
    baris.append(f"📦 *{len(items)} item*  |  💰 *Total: {rupiah(total)}*")
    baris.append("✅ _Tersimpan ke Google Sheets_")

    return "\n".join(baris)

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
        print("Contoh: python -m handlers.vision struk_test.jpg")
    else:
        asyncio.run(run_test(sys.argv[1]))