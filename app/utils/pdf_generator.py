"""Renders the single predefined certificate template with ReportLab."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen.canvas import Canvas

PAGE_WIDTH, PAGE_HEIGHT = landscape(A4)
NAVY = HexColor("#1F3A5F")
GOLD = HexColor("#B08D2E")
GRAY = HexColor("#555555")
MAX_TEXT_WIDTH = PAGE_WIDTH - 160

# ReportLab's built-in fonts use WinAnsi (cp1252) encoding.
SUPPORTED_ENCODING = "cp1252"


class CertificateGenerationError(Exception):
    """Raised when a certificate cannot be rendered for a predictable reason."""


@dataclass(frozen=True)
class CertificateData:
    recipient_name: str
    course_name: str
    event_name: str
    certificate_date: date
    certificate_number: str
    organization_name: str


def is_text_supported(text: str) -> bool:
    """Built-in PDF fonts silently draw black boxes for unsupported glyphs, so check first."""
    try:
        text.encode(SUPPORTED_ENCODING)
    except UnicodeEncodeError:
        return False
    return True


def _fit_font_size(text: str, font: str, max_size: float, min_size: float = 8) -> float:
    size = max_size
    while size > min_size and stringWidth(text, font, size) > MAX_TEXT_WIDTH:
        size -= 1
    return size


def _draw_fitted(canvas: Canvas, text: str, font: str, max_size: float, y: float) -> None:
    canvas.setFont(font, _fit_font_size(text, font, max_size))
    canvas.drawCentredString(PAGE_WIDTH / 2, y, text)


def _draw_template(canvas: Canvas, data: CertificateData) -> None:
    center = PAGE_WIDTH / 2

    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(4)
    canvas.rect(25, 25, PAGE_WIDTH - 50, PAGE_HEIGHT - 50)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(1.2)
    canvas.rect(36, 36, PAGE_WIDTH - 72, PAGE_HEIGHT - 72)

    canvas.setFillColor(GOLD)
    _draw_fitted(canvas, data.organization_name.upper(), "Helvetica-Bold", 14, PAGE_HEIGHT - 85)

    canvas.setFillColor(NAVY)
    _draw_fitted(canvas, "CERTIFICATE OF COMPLETION", "Times-Bold", 40, PAGE_HEIGHT - 140)

    canvas.setFillColor(GRAY)
    _draw_fitted(canvas, "This certificate is proudly presented to", "Helvetica", 15, PAGE_HEIGHT - 190)

    canvas.setFillColor(NAVY)
    _draw_fitted(canvas, data.recipient_name, "Times-BoldItalic", 38, PAGE_HEIGHT - 250)
    canvas.setStrokeColor(GOLD)
    canvas.setLineWidth(1)
    canvas.line(center - 220, PAGE_HEIGHT - 264, center + 220, PAGE_HEIGHT - 264)

    canvas.setFillColor(GRAY)
    _draw_fitted(canvas, "for successfully completing", "Helvetica", 15, PAGE_HEIGHT - 305)

    canvas.setFillColor(NAVY)
    _draw_fitted(canvas, data.course_name, "Helvetica-Bold", 26, PAGE_HEIGHT - 345)
    canvas.setFillColor(GRAY)
    _draw_fitted(canvas, data.event_name, "Helvetica", 17, PAGE_HEIGHT - 375)

    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica", 12)
    canvas.drawString(90, 100, f"Date: {data.certificate_date.strftime('%d %B %Y')}")

    canvas.setStrokeColor(GRAY)
    canvas.setLineWidth(0.8)
    canvas.line(PAGE_WIDTH - 280, 105, PAGE_WIDTH - 90, 105)
    canvas.setFillColor(GRAY)
    canvas.setFont("Helvetica", 10)
    canvas.drawCentredString(PAGE_WIDTH - 185, 90, "Authorized Signatory")

    canvas.setFont("Helvetica", 10)
    canvas.drawCentredString(center, 62, f"Certificate ID: {data.certificate_number}")


def render_certificate_pdf(data: CertificateData, output_path: Path) -> None:
    """Write the certificate PDF to ``output_path`` (atomically, via a temp file)."""
    for label, value in (
        ("Recipient name", data.recipient_name),
        ("Course name", data.course_name),
        ("Event name", data.event_name),
        ("Organization name", data.organization_name),
    ):
        if not is_text_supported(value):
            raise CertificateGenerationError(
                f"{label} contains characters that the certificate font cannot render"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        canvas = Canvas(str(temp_path), pagesize=landscape(A4))
        canvas.setTitle(f"Certificate {data.certificate_number}")
        canvas.setAuthor(data.organization_name)
        canvas.setSubject(f"{data.course_name} - {data.event_name}")
        _draw_template(canvas, data)
        canvas.save()
        os.replace(temp_path, output_path)
    finally:
        temp_path.unlink(missing_ok=True)
