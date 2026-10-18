"""Office document conversion through LibreOffice (``soffice --headless``), if installed."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from hawser.errors import ToolError
from hawser.models import SideEffect
from hawser.tools.registry import tool

_EXT = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-powerpoint": ".ppt",
}


def soffice_path() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return mac if Path(mac).exists() else None


@tool(
    "convert.office_to_pdf",
    description="Convert an Office document (docx/xlsx/pptx/doc/xls/ppt) to PDF with LibreOffice.",
    side_effect=SideEffect.local_artifact,
    modalities=["document"],
)
def office_to_pdf(data: bytes, media_type: str, *, timeout_s: float = 120.0) -> bytes:
    exe = soffice_path()
    if exe is None:
        raise ToolError(
            "LibreOffice (soffice) is not installed; cannot convert Office documents",
            code="office_conversion_unavailable",
            retryable=False,
        )
    ext = _EXT.get(media_type, ".bin")
    with tempfile.TemporaryDirectory(prefix="hawser-office-") as tmp:
        src = Path(tmp) / f"input{ext}"
        src.write_bytes(data)
        try:
            subprocess.run(
                [
                    exe,
                    "--headless",
                    "--norestore",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmp,
                    str(src),
                ],
                check=True,
                capture_output=True,
                timeout=timeout_s,
                env={"HOME": tmp, "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolError(
                "LibreOffice conversion timed out",
                code="office_conversion_timeout",
                retryable=False,
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise ToolError(
                f"LibreOffice conversion failed: {exc.stderr.decode(errors='replace')[:300]}",
                code="office_conversion_failed",
                retryable=False,
            ) from exc
        out = src.with_suffix(".pdf")
        if not out.exists():
            raise ToolError(
                "LibreOffice produced no PDF", code="office_conversion_failed", retryable=False
            )
        return out.read_bytes()


__all__ = ["office_to_pdf", "soffice_path"]
