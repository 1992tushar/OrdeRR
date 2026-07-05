"""
ocr_engine.py
-------------
Swappable OCR engine interface.

Two distinct extraction strategies live here:

  1. Line-based OCR (legacy, for generic text extraction)
     - Provider selected via OCR_PROVIDER in .env
     - Supported: 'google_vision' (recommended), 'tesseract' (local dev only)
     - Interface: OCREngine.extract_lines() -> List[Tuple[str, confidence]]

  2. Structured production-report extraction (primary, Claude-based)
     - Sends the report photo to Claude Haiku with a strict JSON schema
     - Returns hotel-wise rows: {hotel_name, items: [{product_name, ordered_quantity,
       delivered_quantity, unit, confidence, raw_notes}]}
     - Handles handwriting + multi-column layout natively -- no regex stitching needed
     - Interface: ClaudeProductionReportEngine.extract_rows() -> List[HotelBlock]

The two strategies are independent.  ocr_actuals_parser.py uses (2).
The legacy (1) path remains for any other callers that still need raw lines.

CLAUDE SETUP (one-time):
  1. Add to your .env:
       ANTHROPIC_API_KEY=sk-ant-...yourkey...
  2. No extra pip install beyond 'httpx' and 'Pillow' (already in requirements.txt).
  3. Model used: claude-haiku-4-5-20251001  (fast, cheap, excellent vision)
     Override with CLAUDE_MODEL env var if needed.

GOOGLE VISION SETUP (legacy path):
  1. Add to your .env:
       OCR_PROVIDER=google_vision
       GOOGLE_VISION_API_KEY=AIza...yourkey...
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple, TypedDict

import httpx

logger = logging.getLogger(__name__)


class OCREngineError(Exception):
    """Raised when the configured OCR provider fails to extract text."""


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

class ProductRow(TypedDict, total=False):
    """A single product row within a hotel block."""
    product_name: str
    ordered_quantity: Optional[float]
    delivered_quantity: Optional[float]
    unit: str
    confidence: float
    raw_notes: str


class HotelBlock(TypedDict):
    """
    One hotel's section from the Hotel-wise Orders part of the production report.

    Fields
    ------
    hotel_name  : str            -- hotel name exactly as written on the sheet
    items       : List[ProductRow] -- products ordered + delivered for this hotel
    """
    hotel_name: str
    items: List[ProductRow]


# Kept for backward compatibility with any code still referencing GeminiRow
GeminiRow = ProductRow


# ---------------------------------------------------------------------------
# Extraction prompt — hotel-wise structure (photo upload path)
# ---------------------------------------------------------------------------

_EXTRACTION_SYSTEM_PROMPT = """\
You are a data-extraction assistant for a poultry supply business.
You will receive a photo of a printed Daily Production Report with handwritten
annotations. Drivers have physically written the delivered quantities in the
"Delivered Qty" column throughout the day as they returned from their routes.
NOT every hotel will have delivered quantities written — some boxes will still
be blank because those drivers have not returned yet.

The report has TWO sections:
  1. PRODUCT SUMMARY — TOTAL QUANTITIES  (aggregated totals — IGNORE THIS ENTIRELY)
  2. HOTEL-WISE ORDERS  (per-hotel breakdown — extract ONLY this section)

The Hotel-wise Orders section lists each hotel by name (e.g. "1. Hotel Amrai"),
followed by a table of products with columns:
  Product Name | Ordered Quantity | Delivered Quantity

The "Ordered Quantity" column is PRINTED (computer-generated text).
The "Delivered Quantity" column contains HANDWRITTEN numbers — or is blank.

═══════════════════════════════════════════════════════════════════════
CRITICAL RULES — READ CAREFULLY:
═══════════════════════════════════════════════════════════════════════

1. ONLY include a hotel in your response if the manager has PHYSICALLY
   WRITTEN at least one delivered quantity number in that hotel's
   "Delivered Qty" column boxes or lines.

2. If ALL of a hotel's delivered qty boxes are blank or empty — even if
   you can read the ordered quantities perfectly — DO NOT include that
   hotel in your response at all. Omit it entirely.

3. For a hotel where SOME products have a handwritten delivered qty and
   SOME are blank: include the hotel, but return ONLY the product rows
   that have a delivered qty written. Omit blank product rows for that hotel.

4. The ordered qty is PRINTED text (easy to read).
   The delivered qty is HANDWRITTEN (may be harder to read — that is OK).
   Your job is to read the HANDWRITTEN numbers. Focus on those.

5. Never copy the ordered quantity into the delivered_quantity field.
   If delivered_quantity is blank, set it to null. Do NOT guess or invent.

6. A zero ("0") written by hand IS a valid delivered quantity — include it.
   Only truly blank/empty boxes should be null.

═══════════════════════════════════════════════════════════════════════

Return ONLY a JSON array (no markdown fences, no explanation).
Each element represents one hotel that has at least one handwritten
delivered quantity, and must have exactly these keys:
  "hotel_name" : string  -- hotel name as written, strip any leading "N. " prefix
  "items"      : array   -- ONLY the product rows with a delivered qty written

Each item in "items" must have exactly these keys:
  "product_name"       : string  -- product name exactly as printed
  "ordered_quantity"   : number or null  -- printed ordered qty (null if missing)
  "delivered_quantity" : number          -- the handwritten delivered qty
                                           (MUST be a number, not null, because
                                            you only include rows that have one)
  "unit"               : string  -- "kg", "nos", or "" if not determinable
  "confidence"         : number  -- your reading confidence 0.0-1.0
                                    (< 0.7 if handwriting was difficult,
                                     0.5 if you had to guess,
                                     0.3 if very uncertain)
  "raw_notes"          : string  -- brief note about anything unusual (empty if none)

Summary:
- Hotels with NO handwritten delivered qty → OMIT entirely
- Hotels with SOME handwritten delivered qty → include, but only filled rows
- Hotels with ALL delivered qty filled → include all rows
- Never invent quantities. Never copy ordered_qty into delivered_qty.
- Strip number prefixes from hotel names (e.g. "1. Hotel Amrai" → "Hotel Amrai")
"""

_EXTRACTION_USER_TEXT = (
    "This is a photo of today's Daily Production Report. "
    "Some hotels have had their delivered quantities written in by the manager; "
    "others are still blank (those drivers haven't returned yet). "
    "Extract ONLY the hotels where at least one delivered quantity has been "
    "handwritten. Return a JSON array — no markdown, no explanation."
)

_IMAGE_MAX_DIM = 1600


# ---------------------------------------------------------------------------
# Claude production-report engine  (PRIMARY — default)
# ---------------------------------------------------------------------------

_CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
_CLAUDE_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class ClaudeProductionReportEngine:
    """
    Extracts hotel-wise structured rows from a production-report photo
    using Claude Haiku.

    Usage
    -----
    engine = ClaudeProductionReportEngine(api_key="sk-ant-...")
    hotel_blocks = engine.extract_rows(image_bytes)
    # hotel_blocks: List[HotelBlock]

    The raw Claude response text is stored on engine.last_raw_response
    after every call, for audit logging by the route handler.
    """

    def __init__(self, api_key: str, model: Optional[str] = None):
        if not api_key:
            raise OCREngineError(
                "ANTHROPIC_API_KEY is not set in your .env file. "
                "Add: ANTHROPIC_API_KEY=sk-ant-...yourkey..."
            )
        self._api_key = api_key
        self._model = model or os.getenv("CLAUDE_MODEL", _CLAUDE_DEFAULT_MODEL)
        self.last_raw_response: str = ""

    def extract_rows(self, image_bytes: bytes) -> List[HotelBlock]:
        """
        Send the report image to Claude and return hotel-wise structured blocks.

        Raises OCREngineError on network failures or non-200 API responses.
        Returns [] if Claude finds no hotel sections with filled delivered qty.
        """
        b64_image, mime_type = self._preprocess_image(image_bytes)

        payload = {
            "model": self._model,
            "max_tokens": 2048,
            "system": _EXTRACTION_SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": _EXTRACTION_USER_TEXT,
                        },
                    ],
                }
            ],
        }

        try:
            response = httpx.post(
                _CLAUDE_API_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OCREngineError(
                f"Claude API returned HTTP {e.response.status_code}: "
                f"{e.response.text[:400]}"
            ) from e
        except httpx.RequestError as e:
            raise OCREngineError(f"Network error calling Claude API: {e}") from e

        data = response.json()
        return self._parse_claude_response(data)

    def _preprocess_image(self, image_bytes: bytes) -> Tuple[str, str]:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image_bytes)) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if max(img.size) > _IMAGE_MAX_DIM:
                    logger.info(
                        "Resizing image from %s to max %dpx before Claude upload.",
                        img.size, _IMAGE_MAX_DIM,
                    )
                    img.thumbnail((_IMAGE_MAX_DIM, _IMAGE_MAX_DIM), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
            return encoded, "image/jpeg"

        except ImportError:
            logger.warning(
                "Pillow not installed -- sending raw image bytes to Claude. "
                "Install Pillow for automatic image resizing: pip install Pillow"
            )
            mime_type = _detect_mime_type(image_bytes)
            encoded = base64.b64encode(image_bytes).decode("utf-8")
            return encoded, mime_type

    def _parse_claude_response(self, data: Dict[str, Any]) -> List[HotelBlock]:
        """Unwrap the Claude Messages API response and parse the hotel-wise JSON array."""
        try:
            raw_text = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            error_detail = data.get("error") or data
            raise OCREngineError(
                f"Unexpected Claude response structure: {e}. "
                f"Response snippet: {str(error_detail)[:300]}"
            )

        self.last_raw_response = raw_text
        logger.debug("Claude raw response:\n%s", raw_text)

        # Strip accidental markdown fences
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise OCREngineError(
                f"Claude returned non-JSON content: {e}. "
                f"Raw text snippet: {text[:300]}"
            ) from e

        if not isinstance(parsed, list):
            if isinstance(parsed, dict):
                for key in ("hotels", "hotel_blocks", "orders", "data", "results"):
                    if isinstance(parsed.get(key), list):
                        parsed = parsed[key]
                        break
                else:
                    raise OCREngineError(
                        f"Claude returned a JSON object instead of an array. "
                        f"Keys: {list(parsed.keys())}"
                    )
            else:
                raise OCREngineError(
                    f"Claude returned unexpected JSON type: {type(parsed).__name__}"
                )

        hotel_blocks: List[HotelBlock] = []
        for idx, hotel in enumerate(parsed):
            if not isinstance(hotel, dict):
                logger.warning("Claude hotel block %d is not a dict, skipping.", idx)
                continue

            hotel_name = str(hotel.get("hotel_name") or "").strip()
            if not hotel_name:
                logger.warning("Claude hotel block %d has no hotel_name, skipping.", idx)
                continue

            raw_items = hotel.get("items") or []
            if not isinstance(raw_items, list):
                logger.warning(
                    "Hotel %r items is not a list (%r), skipping.", hotel_name, type(raw_items)
                )
                continue

            items: List[ProductRow] = []
            for item_idx, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    continue
                row: ProductRow = {
                    "product_name":       str(item.get("product_name") or "").strip(),
                    "ordered_quantity":   _coerce_float(item.get("ordered_quantity")),
                    "delivered_quantity": _coerce_float(item.get("delivered_quantity")),
                    "unit":               str(item.get("unit") or "").strip().lower(),
                    "confidence":         _coerce_float(item.get("confidence")) or 0.0,
                    "raw_notes":          str(item.get("raw_notes") or "").strip(),
                }
                if not row["product_name"]:
                    logger.warning(
                        "Hotel %r item %d has no product_name, skipping.", hotel_name, item_idx
                    )
                    continue

                # Per the updated prompt, Claude should only return rows with a
                # delivered_quantity.  But defensively filter here too: skip any
                # row where delivered_quantity is null (blank box).
                if row["delivered_quantity"] is None:
                    logger.debug(
                        "Hotel %r item %r has null delivered_quantity — skipping row.",
                        hotel_name, row["product_name"],
                    )
                    continue

                row["confidence"] = max(0.0, min(1.0, row["confidence"]))
                items.append(row)

            # Only include hotels that ended up with at least one filled row
            if not items:
                logger.debug(
                    "Hotel %r has no rows with delivered_quantity — omitting from output.",
                    hotel_name,
                )
                continue

            hotel_blocks.append({"hotel_name": hotel_name, "items": items})

        logger.info(
            "Claude extraction complete: %d hotel blocks with delivered qty, "
            "%d total product rows.",
            len(hotel_blocks),
            sum(len(h["items"]) for h in hotel_blocks),
        )
        return hotel_blocks


# ---------------------------------------------------------------------------
# Gemini engine (kept as fallback — set PRODUCTION_REPORT_ENGINE=gemini in .env)
# ---------------------------------------------------------------------------

_GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models"
    "/{model}:generateContent"
)


class GeminiProductionReportEngine:
    """
    Fallback engine using Gemini Flash.
    Set PRODUCTION_REPORT_ENGINE=gemini in .env to use.
    Returns List[HotelBlock] — same contract as ClaudeProductionReportEngine.
    """

    def __init__(self, api_key: str, model: Optional[str] = None):
        if not api_key:
            raise OCREngineError(
                "GEMINI_API_KEY is not set in your .env file. "
                "Add: GEMINI_API_KEY=AIza...yourkey..."
            )
        self._api_key = api_key
        self._model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.last_raw_response: str = ""

    def extract_rows(self, image_bytes: bytes) -> List[HotelBlock]:
        b64_image, mime_type = self._preprocess_image(image_bytes)

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": b64_image}},
                        {"text": _EXTRACTION_USER_TEXT},
                    ],
                }
            ],
            "systemInstruction": {"parts": [{"text": _EXTRACTION_SYSTEM_PROMPT}]},
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }

        url = _GEMINI_GENERATE_URL.format(model=self._model)
        try:
            response = httpx.post(
                url,
                params={"key": self._api_key},
                json=payload,
                timeout=60.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OCREngineError(
                f"Gemini API returned HTTP {e.response.status_code}: {e.response.text[:400]}"
            ) from e
        except httpx.RequestError as e:
            raise OCREngineError(f"Network error calling Gemini API: {e}") from e

        data = response.json()
        try:
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise OCREngineError(
                f"Unexpected Gemini response structure: {e}. "
                f"Snippet: {str(data.get('error') or data)[:300]}"
            )

        self.last_raw_response = raw_text
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise OCREngineError(f"Gemini returned non-JSON: {e}. Raw: {text[:300]}")

        # Reuse Claude's parser since the schema is identical
        engine = ClaudeProductionReportEngine.__new__(ClaudeProductionReportEngine)
        engine.last_raw_response = raw_text
        return engine._parse_claude_response({"content": [{"text": text}]})

    def _preprocess_image(self, image_bytes: bytes) -> Tuple[str, str]:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(image_bytes)) as img:
                if img.mode != "RGB":
                    img = img.convert("RGB")
                if max(img.size) > _IMAGE_MAX_DIM:
                    img.thumbnail((_IMAGE_MAX_DIM, _IMAGE_MAX_DIM), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                img.save(buffer, format="JPEG", quality=85)
                return base64.b64encode(buffer.getvalue()).decode("utf-8"), "image/jpeg"
        except ImportError:
            mime_type = _detect_mime_type(image_bytes)
            return base64.b64encode(image_bytes).decode("utf-8"), mime_type


# ---------------------------------------------------------------------------
# Legacy line-based OCR engines  (kept for any non-actuals callers)
# ---------------------------------------------------------------------------

class OCREngine(ABC):
    @abstractmethod
    def extract_lines(self, image_bytes: bytes) -> List[Tuple[str, Optional[float]]]:
        raise NotImplementedError


class GoogleVisionOCREngine(OCREngine):
    VISION_API_URL = "https://vision.googleapis.com/v1/images:annotate"

    def __init__(self, api_key: str):
        if not api_key:
            raise OCREngineError(
                "GOOGLE_VISION_API_KEY is not set in your .env file."
            )
        self._api_key = api_key

    def extract_lines(self, image_bytes: bytes) -> List[Tuple[str, Optional[float]]]:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        payload = {
            "requests": [
                {
                    "image": {"content": b64_image},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
                }
            ]
        }
        try:
            response = httpx.post(
                self.VISION_API_URL,
                params={"key": self._api_key},
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise OCREngineError(
                f"Google Vision API returned HTTP {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        except httpx.RequestError as e:
            raise OCREngineError(f"Network error calling Google Vision API: {e}") from e

        data = response.json()
        responses = data.get("responses", [])
        if not responses:
            raise OCREngineError("Google Vision API returned an empty response.")
        api_error = responses[0].get("error")
        if api_error:
            raise OCREngineError(
                f"Google Vision API error {api_error.get('code')}: {api_error.get('message')}"
            )
        full_text_annotation = responses[0].get("fullTextAnnotation")
        if not full_text_annotation:
            return []

        lines: List[Tuple[str, Optional[float]]] = []
        for page in full_text_annotation.get("pages", []):
            for block in page.get("blocks", []):
                for paragraph in block.get("paragraphs", []):
                    para_words = []
                    for word in paragraph.get("words", []):
                        word_text = "".join(
                            symbol.get("text", "") for symbol in word.get("symbols", [])
                        )
                        if word_text:
                            para_words.append(word_text)
                    para_text = " ".join(para_words).strip()
                    if not para_text:
                        continue
                    conf_raw = paragraph.get("confidence")
                    confidence = (conf_raw * 100.0) if conf_raw is not None else None
                    lines.append((para_text, confidence))
        return lines


class TesseractOCREngine(OCREngine):
    def __init__(self):
        try:
            import pytesseract
            self._pytesseract = pytesseract
        except ImportError:
            raise OCREngineError(
                "pytesseract is not installed. Run: pip install pytesseract"
            )
        tesseract_cmd = os.getenv("TESSERACT_CMD")
        if tesseract_cmd:
            self._pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def extract_lines(self, image_bytes: bytes) -> List[Tuple[str, Optional[float]]]:
        from PIL import Image
        from pytesseract import Output
        try:
            image = Image.open(io.BytesIO(image_bytes))
            if image.mode != "RGB":
                image = image.convert("RGB")
        except Exception as e:
            raise OCREngineError(f"Could not open uploaded image: {e}") from e
        try:
            data = self._pytesseract.image_to_data(image, output_type=Output.DICT)
        except Exception as e:
            raise OCREngineError(f"Tesseract OCR failed: {e}") from e

        line_buckets: dict = {}
        n = len(data.get("text", []))
        for i in range(n):
            word = (data["text"][i] or "").strip()
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                continue
            if conf < 0:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            bucket = line_buckets.setdefault(key, {"words": [], "confs": []})
            bucket["words"].append(word)
            bucket["confs"].append(conf)

        result: List[Tuple[str, Optional[float]]] = []
        for key in sorted(line_buckets.keys()):
            words = line_buckets[key]["words"]
            confs = line_buckets[key]["confs"]
            text = " ".join(words)
            avg_conf = (sum(confs) / len(confs)) if confs else 0.0
            result.append((text, avg_conf))
        return result


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def get_ocr_engine() -> OCREngine:
    """Returns the configured line-based OCR engine (legacy path)."""
    provider = os.getenv("OCR_PROVIDER", "google_vision").strip().lower()
    if provider == "google_vision":
        return GoogleVisionOCREngine(api_key=os.getenv("GOOGLE_VISION_API_KEY", ""))
    if provider == "tesseract":
        return TesseractOCREngine()
    raise OCREngineError(
        f"Unknown OCR_PROVIDER: {provider!r}. Valid options: 'google_vision', 'tesseract'"
    )


def get_production_report_engine():
    """
    Returns the structured extraction engine for production report photos.

    Default: ClaudeProductionReportEngine (claude-haiku-4-5-20251001)
      Requires ANTHROPIC_API_KEY in .env

    Override: set PRODUCTION_REPORT_ENGINE=gemini in .env
      Requires GEMINI_API_KEY in .env
    """
    engine_choice = os.getenv("PRODUCTION_REPORT_ENGINE", "claude").strip().lower()
    if engine_choice == "gemini":
        return GeminiProductionReportEngine(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=os.getenv("GEMINI_MODEL"),
        )
    return ClaudeProductionReportEngine(
        api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model=os.getenv("CLAUDE_MODEL"),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detect_mime_type(image_bytes: bytes) -> str:
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if image_bytes[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if image_bytes[:4] == b"RIFF" and image_bytes[8:12] == b"WEBP":
        return "image/webp"
    if image_bytes[:4] in (b"MM\x00*", b"II*\x00"):
        return "image/tiff"
    return "image/jpeg"


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return f
    except (TypeError, ValueError):
        return None