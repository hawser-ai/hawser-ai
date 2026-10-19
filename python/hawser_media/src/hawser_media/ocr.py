"""OCR engines behind one contract (plan WP7.2).

``OcrEngine.recognize(png)`` returns words with pixel boxes and confidences plus the lines
they came from. ``RapidOcrEngine`` is the local default (ONNX, bundled models, Apache-2.0);
``TesseractEngine`` is used when the binary is installed; ``AzureDocumentIntelligenceEngine``
is the reference hosted adapter (``external_read``). ``page_text_from_ocr`` turns a result into
the ``PageText`` shape the index and anchoring consume, with per-character boxes.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from hawser_media.errors import MediaError
from hawser_media.pdf import CharBox, Line, PageText, Word


class OcrWord(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = Field(default=1.0, ge=0, le=1)


class OcrLine(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    confidence: float = Field(default=1.0, ge=0, le=1)
    words: list[OcrWord] = Field(default_factory=list)


class OcrResult(BaseModel):
    engine: str
    width: int
    height: int
    lines: list[OcrLine] = Field(default_factory=list)
    duration_ms: int = 0

    @property
    def words(self) -> list[OcrWord]:
        return [w for ln in self.lines for w in ln.words]

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


class OcrEngine(Protocol):
    name: str

    def recognize(self, png: bytes) -> OcrResult: ...


# -- helpers ---------------------------------------------------------------------------------


def split_line_into_words(
    text: str, box: tuple[float, float, float, float], conf: float
) -> list[OcrWord]:
    """Engines that return lines only: give each word a proportional share of the line box."""
    tokens = text.split()
    if not tokens:
        return []
    x0, y0, x1, y1 = box
    total = sum(len(t) for t in tokens) + (len(tokens) - 1)
    width = x1 - x0
    out: list[OcrWord] = []
    cursor = 0
    for tok in tokens:
        start = x0 + width * (cursor / total) if total else x0
        end = x0 + width * ((cursor + len(tok)) / total) if total else x1
        out.append(OcrWord(text=tok, x0=start, y0=y0, x1=end, y1=y1, confidence=conf))
        cursor += len(tok) + 1
    return out


def _sort_reading_order(lines: list[OcrLine]) -> list[OcrLine]:
    """Top-to-bottom, left-to-right; lines whose vertical centres are within half a line
    height are treated as one row."""
    if not lines:
        return lines
    rows: list[list[OcrLine]] = []
    for ln in sorted(lines, key=lambda x: ((x.y0 + x.y1) / 2, x.x0)):
        h = max(1.0, ln.y1 - ln.y0)
        if rows and abs(((rows[-1][0].y0 + rows[-1][0].y1) / 2) - ((ln.y0 + ln.y1) / 2)) < h / 2:
            rows[-1].append(ln)
        else:
            rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda x: x.x0)]


def page_text_from_ocr(result: OcrResult, *, page: int = 1, scale: float = 1.0) -> PageText:
    """``PageText`` with one character box per glyph. ``scale`` converts pixels to the page's
    coordinate system (``72 / dpi`` for rendered PDF pages, ``1`` for images)."""
    text_parts: list[str] = []
    chars: list[CharBox] = []
    lines: list[Line] = []
    offset = 0
    for ln in _sort_reading_order(result.lines):
        words: list[Word] = []
        line_start = offset
        for wi, w in enumerate(ln.words):
            if wi > 0:
                text_parts.append(" ")
                chars.append(CharBox(char=" "))
                offset += 1
            n = max(len(w.text), 1)
            cw = (w.x1 - w.x0) / n
            start = offset
            for i, ch in enumerate(w.text):
                chars.append(
                    CharBox(
                        char=ch,
                        x0=round((w.x0 + i * cw) * scale, 2),
                        y0=round(w.y0 * scale, 2),
                        x1=round((w.x0 + (i + 1) * cw) * scale, 2),
                        y1=round(w.y1 * scale, 2),
                    )
                )
            text_parts.append(w.text)
            offset += len(w.text)
            words.append(
                Word(
                    text=w.text,
                    start=start,
                    end=offset,
                    x0=w.x0 * scale,
                    y0=w.y0 * scale,
                    x1=w.x1 * scale,
                    y1=w.y1 * scale,
                    confidence=w.confidence,
                )
            )
        if words:
            lines.append(
                Line(
                    text=" ".join(w.text for w in words),
                    start=line_start,
                    end=offset,
                    x0=min(w.x0 for w in words),
                    y0=min(w.y0 for w in words),
                    x1=max(w.x1 for w in words),
                    y1=max(w.y1 for w in words),
                    words=words,
                )
            )
        text_parts.append("\n")
        chars.append(CharBox(char="\n"))
        offset += 1
    return PageText(
        page=page,
        width_pt=result.width * scale,
        height_pt=result.height * scale,
        text="".join(text_parts),
        chars=chars,
        lines=lines,
    )


# -- RapidOCR (local default) ----------------------------------------------------------------


class RapidOcrEngine:
    """PaddleOCR v4 models through ONNX Runtime; models ship in the wheel.

    On Linux, OpenCV (a RapidOCR dependency) needs the system library ``libGL.so.1``
    (Debian/Ubuntu: ``apt-get install libgl1``); the Docker image includes it.
    """

    name = "rapidocr"
    _lock = threading.Lock()
    _instance: Any = None

    @classmethod
    def available(cls) -> bool:
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None

    @classmethod
    def _engine(cls) -> Any:
        with cls._lock:
            if cls._instance is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR  # noqa: PLC0415
                except ImportError as exc:
                    hint = " (install the system package libgl1)" if "libGL" in str(exc) else ""
                    raise MediaError("ocr_failed", f"rapidocr could not load: {exc}{hint}") from exc

                cls._instance = RapidOCR()
            return cls._instance

    def recognize(self, png: bytes) -> OcrResult:
        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        started = time.monotonic()
        img = Image.open(io.BytesIO(png)).convert("RGB")
        width, height = img.size
        raw, _ = self._engine()(np.asarray(img))
        lines: list[OcrLine] = []
        for item in raw or []:
            poly, text, conf = item[0], str(item[1]), float(item[2])
            xs = [float(p[0]) for p in poly]
            ys = [float(p[1]) for p in poly]
            box = (min(xs), min(ys), max(xs), max(ys))
            c = max(0.0, min(1.0, conf))
            lines.append(
                OcrLine(
                    text=text,
                    x0=box[0],
                    y0=box[1],
                    x1=box[2],
                    y1=box[3],
                    confidence=c,
                    words=split_line_into_words(text, box, c),
                )
            )
        return OcrResult(
            engine=self.name,
            width=width,
            height=height,
            lines=lines,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


# -- Tesseract (when the binary is installed) -------------------------------------------------


class TesseractEngine:
    name = "tesseract"

    def __init__(self, binary: str | None = None, *, psm: int = 3, lang: str = "eng") -> None:
        self.binary = binary or shutil.which("tesseract") or "tesseract"
        self.psm = psm
        self.lang = lang

    @classmethod
    def available(cls) -> bool:
        return shutil.which("tesseract") is not None

    def recognize(self, png: bytes) -> OcrResult:
        from PIL import Image  # noqa: PLC0415

        started = time.monotonic()
        width, height = Image.open(io.BytesIO(png)).size
        try:
            proc = subprocess.run(
                [self.binary, "stdin", "stdout", "--psm", str(self.psm), "-l", self.lang, "tsv"],
                input=png,
                capture_output=True,
                check=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise MediaError("ocr_failed", f"tesseract failed: {exc}") from exc
        rows = proc.stdout.decode("utf-8", errors="replace").splitlines()
        lines: dict[tuple[str, str, str, str], OcrLine] = {}
        for row in rows[1:]:
            cols = row.split("\t")
            if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
                continue
            key = (cols[1], cols[2], cols[3], cols[4])
            x, y, w, h = (float(cols[6]), float(cols[7]), float(cols[8]), float(cols[9]))
            conf = (
                max(0.0, min(1.0, float(cols[10]) / 100.0)) if cols[10] not in ("-1", "") else 0.0
            )
            word = OcrWord(text=cols[11].strip(), x0=x, y0=y, x1=x + w, y1=y + h, confidence=conf)
            ln = lines.get(key)
            if ln is None:
                lines[key] = OcrLine(
                    text=word.text,
                    x0=word.x0,
                    y0=word.y0,
                    x1=word.x1,
                    y1=word.y1,
                    confidence=conf,
                    words=[word],
                )
            else:
                ln.words.append(word)
                ln.text = " ".join(w.text for w in ln.words)
                ln.x0, ln.y0 = min(ln.x0, word.x0), min(ln.y0, word.y0)
                ln.x1, ln.y1 = max(ln.x1, word.x1), max(ln.y1, word.y1)
                ln.confidence = sum(w.confidence for w in ln.words) / len(ln.words)
        return OcrResult(
            engine=self.name,
            width=width,
            height=height,
            lines=list(lines.values()),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


# -- Azure Document Intelligence (reference hosted adapter) ------------------------------------


@dataclass
class AzureDocumentIntelligenceEngine:
    """``prebuilt-read`` over REST. ``external_read``: bytes leave the deployment. The region is
    whatever the endpoint lives in; policies gate it like any hosted provider."""

    endpoint: str
    api_key: str
    model: str = "prebuilt-read"
    api_version: str = "2024-11-30"
    poll_interval_s: float = 1.0
    timeout_s: float = 120.0
    client: Any = field(default=None, repr=False)
    name: str = "azure-document-intelligence"

    @classmethod
    def from_env(cls) -> AzureDocumentIntelligenceEngine | None:
        endpoint = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT")
        key = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY")
        return cls(endpoint=endpoint, api_key=key) if endpoint and key else None

    def recognize(self, png: bytes) -> OcrResult:
        import httpx  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        started = time.monotonic()
        width, height = Image.open(io.BytesIO(png)).size
        client = self.client or httpx.Client(timeout=self.timeout_s)
        url = (
            f"{self.endpoint.rstrip('/')}/documentintelligence/documentModels/{self.model}:analyze"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/octet-stream",
        }
        res = client.post(
            url, params={"api-version": self.api_version}, headers=headers, content=png
        )
        if res.status_code not in (200, 202):
            raise MediaError(
                "ocr_failed", f"azure analyze returned {res.status_code}: {res.text[:200]}"
            )
        op = res.headers.get("Operation-Location")
        if not op:
            raise MediaError("ocr_failed", "azure analyze returned no Operation-Location")
        deadline = time.monotonic() + self.timeout_s
        while True:
            poll = client.get(op, headers={"Ocp-Apim-Subscription-Key": self.api_key})
            body: dict[str, Any] = poll.json()
            status = str(body.get("status", ""))
            if status == "succeeded":
                break
            if status in ("failed", "canceled"):
                raise MediaError(
                    "ocr_failed",
                    f"azure analyze {status}: {json.dumps(body.get('error', {}))[:200]}",
                )
            if time.monotonic() > deadline:
                raise MediaError("ocr_failed", "azure analyze timed out")
            time.sleep(self.poll_interval_s)
        lines: list[OcrLine] = []
        for page in body.get("analyzeResult", {}).get("pages", []):
            unit = str(page.get("unit", "pixel"))
            factor = (
                1.0 if unit == "pixel" else float(width) / float(page.get("width", width) or width)
            )
            words = [
                OcrWord(
                    text=str(w.get("content", "")),
                    **_poly_box([float(v) * factor for v in w.get("polygon", [])]),
                    confidence=max(0.0, min(1.0, float(w.get("confidence", 1.0)))),
                )
                for w in page.get("words", [])
                if w.get("polygon")
            ]
            for ln in page.get("lines", []):
                box = _poly_box([float(v) * factor for v in ln.get("polygon", [])])
                inside = [
                    w
                    for w in words
                    if w.x0 >= box["x0"] - 2
                    and w.x1 <= box["x1"] + 2
                    and w.y0 >= box["y0"] - 2
                    and w.y1 <= box["y1"] + 2
                ]
                conf = sum(w.confidence for w in inside) / len(inside) if inside else 1.0
                lines.append(
                    OcrLine(
                        text=str(ln.get("content", "")),
                        confidence=conf,
                        words=inside
                        or split_line_into_words(
                            str(ln.get("content", "")),
                            (box["x0"], box["y0"], box["x1"], box["y1"]),
                            conf,
                        ),
                        **box,
                    )
                )
        return OcrResult(
            engine=self.name,
            width=width,
            height=height,
            lines=lines,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _poly_box(poly: list[float]) -> dict[str, float]:
    xs = poly[0::2]
    ys = poly[1::2]
    return {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys)}


# -- selection --------------------------------------------------------------------------------


def available_engines() -> list[str]:
    out: list[str] = []
    if RapidOcrEngine.available():
        out.append("rapidocr")
    if TesseractEngine.available():
        out.append("tesseract")
    if AzureDocumentIntelligenceEngine.from_env() is not None:
        out.append("azure-document-intelligence")
    return out


def get_engine(name: str = "auto", *, exclude: str | None = None) -> OcrEngine | None:
    """``auto`` picks the first available local engine; ``exclude`` asks for a different one
    (the ``reocr`` repair)."""
    order = (
        [name]
        if name not in ("auto", "")
        else ["rapidocr", "tesseract", "azure-document-intelligence"]
    )
    for candidate in order:
        if candidate == exclude:
            continue
        if candidate == "rapidocr" and RapidOcrEngine.available():
            return RapidOcrEngine()
        if candidate == "tesseract" and TesseractEngine.available():
            return TesseractEngine()
        if candidate == "azure-document-intelligence":
            engine = AzureDocumentIntelligenceEngine.from_env()
            if engine is not None:
                return engine
    return None


__all__ = [
    "AzureDocumentIntelligenceEngine",
    "OcrEngine",
    "OcrLine",
    "OcrResult",
    "OcrWord",
    "RapidOcrEngine",
    "TesseractEngine",
    "available_engines",
    "get_engine",
    "page_text_from_ocr",
    "split_line_into_words",
]
