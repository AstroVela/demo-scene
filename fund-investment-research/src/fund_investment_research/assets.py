"""Deterministic synthetic source generation for the fixture command only."""

from __future__ import annotations

import hashlib
import io
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


FONT_PATH = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
MONO_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")


@dataclass(frozen=True)
class SyntheticAsset:
    source_id: str
    title: str
    source_role: str
    trust_tier: int
    media_type: str
    object_key: str
    content: bytes
    observed_at: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _font(size: int, *, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = MONO_FONT_PATH if mono else FONT_PATH
    if not path.exists():
        raise RuntimeError(f"required fixture font is missing: {path}")
    return ImageFont.truetype(str(path), size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _document_image(
    *,
    title: str,
    document_id: str,
    sections: Iterable[tuple[str, list[str]]],
    accent: tuple[int, int, int],
) -> Image.Image:
    image = Image.new("RGB", (1800, 1250), "white")
    draw = ImageDraw.Draw(image)
    title_font = _font(54)
    section_font = _font(31)
    body_font = _font(27)
    meta_font = _font(22, mono=True)
    draw.rectangle((0, 0, 1800, 150), fill=accent)
    draw.text((80, 38), title, font=title_font, fill="white")
    draw.text((82, 172), f"SYNTHETIC DOCUMENT • {document_id}", font=meta_font, fill=(75, 85, 99))
    y = 235
    for heading, paragraphs in sections:
        draw.rounded_rectangle((70, y - 8, 1730, y + 52), radius=12, fill=(240, 244, 249))
        draw.text((92, y), heading, font=section_font, fill=accent)
        y += 78
        for paragraph in paragraphs:
            for line in _wrap(draw, paragraph, body_font, 1570):
                draw.text((105, y), line, font=body_font, fill=(28, 33, 42))
                y += 43
            y += 18
        y += 18
    draw.line((80, 1170, 1720, 1170), fill=(190, 198, 208), width=2)
    draw.text(
        (82, 1190),
        "Fictional company and values. Demonstration data only; not investment advice.",
        font=_font(20),
        fill=(90, 98, 108),
    )
    return image


def _png_bytes(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=9)
    return output.getvalue()


def _deterministic_pdf(image: Image.Image) -> bytes:
    """Embed one JPEG page in a minimal PDF with no timestamps or random IDs."""

    jpeg = io.BytesIO()
    image.save(jpeg, format="JPEG", quality=94, subsampling=0, optimize=False)
    image_bytes = jpeg.getvalue()
    width, height = image.size
    content = f"q\n{width} 0 0 {height} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            "/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        (
            f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
            f"/Length {len(image_bytes)} >>\nstream\n"
        ).encode("ascii")
        + image_bytes
        + b"\nendstream",
        f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"endstream",
    ]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii"))
        result.extend(body)
        result.extend(b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    result.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(result)


def _chat_screenshot() -> bytes:
    image = Image.new("RGB", (1200, 1500), (238, 241, 245))
    draw = ImageDraw.Draw(image)
    title_font = _font(34)
    body_font = _font(31)
    meta_font = _font(22)
    draw.rectangle((0, 0, 1200, 105), fill=(44, 55, 70))
    draw.text((55, 30), "Synthetic Enterprise Chat — Biotech Watch", font=title_font, fill="white")
    draw.text((60, 140), "2026-07-15 09:42", font=meta_font, fill=(110, 120, 132))
    bubbles = [
        (
            (70, 210, 1030, 460),
            (255, 255, 255),
            "MarketWatcher-7",
            "Heard from a friend that the LX-101 trial has been halted. No link yet. Can anyone confirm?",
        ),
        (
            (170, 535, 1130, 770),
            (205, 240, 211),
            "ResearchOps",
            "Please do not treat this as confirmed. We need an original filing or a trusted source.",
        ),
    ]
    for box, color, author, message in bubbles:
        draw.rounded_rectangle(box, radius=28, fill=color)
        draw.text((box[0] + 35, box[1] + 28), author, font=meta_font, fill=(70, 78, 90))
        y = box[1] + 82
        for line in _wrap(draw, message, body_font, box[2] - box[0] - 75):
            draw.text((box[0] + 35, y), line, font=body_font, fill=(24, 29, 36))
            y += 48
    draw.text(
        (60, 1380),
        "LOW-TRUST SYNTHETIC SCREENSHOT • no original announcement attached",
        font=meta_font,
        fill=(145, 60, 60),
    )
    return _png_bytes(image)


def _audio_bytes() -> bytes:
    """Generate consent-free synthetic speech through the installed eSpeak engine."""

    text = (
        "Lanxing Biotech core product is L X one oh one, a Nectin four antibody "
        "drug conjugate. We discussed O R R, T R A E, D O R, P F S, and the B L A "
        "path. Our phase two trial reported objective response rate of 29%, and "
        "grade three or higher treatment related adverse events of 43%. Cash "
        "runway is 24 months. The duration of response number is unclear, either "
        "six or sixteen months, and requires analyst verification."
    )
    try:
        import pyttsx3
    except ImportError as exc:
        raise RuntimeError("pyttsx3 is required to generate the synthetic audio fixture") from exc
    with tempfile.TemporaryDirectory(prefix="fund-research-tts-") as root:
        raw_path = Path(root) / "meeting-22k.wav"
        output_path = Path(root) / "meeting-16k.wav"
        engine = pyttsx3.init()
        engine.setProperty("voice", "gmw/en-us")
        engine.setProperty("rate", 155)
        engine.save_to_file(text, str(raw_path))
        engine.runAndWait()
        for _ in range(20):
            if raw_path.exists() and raw_path.stat().st_size > 44:
                break
            time.sleep(0.05)
        if not raw_path.exists() or raw_path.stat().st_size <= 44:
            raise RuntimeError("eSpeak/pyttsx3 did not produce the synthetic WAV")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(raw_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
        )
        return output_path.read_bytes()


def build_assets() -> list[SyntheticAsset]:
    """Build all seven source assets; no asset contains real company data."""

    historical = _document_image(
        title="Lanxing Biotech — Approved Research Baseline",
        document_id="SRC-HISTORICAL",
        accent=(45, 83, 135),
        sections=[
            (
                "Product and research scope",
                [
                    "LX-101 is a fictional Nectin-4 antibody-drug conjugate (ADC).",
                    "The research team tracks ORR, Grade 3+ TRAE, DOR, PFS, cash runway, and the planned BLA filing.",
                ],
            ),
            (
                "Approved thesis conditions — version 3",
                [
                    "Overall ORR must be at least 40%. Grade 3 or higher TRAE must be at most 35%.",
                    "Cash runway must be at least 18 months. A trusted conflict about BLA timing requires analyst review.",
                ],
            ),
        ],
    )
    clinical = _document_image(
        title="LX-101 Phase II Topline Results",
        document_id="SRC-CLINICAL",
        accent=(147, 50, 64),
        sections=[
            (
                "Primary population",
                [
                    "Overall objective response rate (ORR): 29%.",
                    "Grade 3 or higher treatment-related adverse events (TRAE): 43%.",
                ],
            ),
            (
                "Exploratory counter-evidence",
                [
                    "Biomarker-positive subgroup: n=8; ORR: 62.5%.",
                    "The subgroup is exploratory and too small to replace the primary-population result. DOR and PFS remain immature.",
                ],
            ),
        ],
    )
    financial = _document_image(
        title="Lanxing Biotech Interim Financial Update",
        document_id="SRC-FINANCIAL",
        accent=(38, 112, 91),
        sections=[
            (
                "Liquidity",
                [
                    "Cash and committed deposits support an estimated cash runway of 24 months.",
                    "The estimate uses the current clinical and manufacturing plan and excludes uncommitted financing.",
                ],
            )
        ],
    )
    regulatory = _document_image(
        title="LX-101 Registration Program Update",
        document_id="SRC-REG-OFFICIAL",
        accent=(58, 83, 158),
        sections=[
            (
                "Company statement",
                [
                    "The LX-101 BLA filing remains on schedule for Q4 2026.",
                    "CMC validation activities are reported as progressing to plan.",
                ],
            )
        ],
    )
    expert = _document_image(
        title="Synthetic Expert Discussion Notes",
        document_id="SRC-REG-EXPERT",
        accent=(132, 91, 37),
        sections=[
            (
                "Expert statement requiring reconciliation",
                [
                    "The expert reports that the LX-101 BLA filing has shifted to Q2 2027 because an additional CMC validation cycle is required.",
                    "This account conflicts with the company statement and must not be silently merged into a single status.",
                ],
            )
        ],
    )
    return [
        SyntheticAsset(
            source_id="SRC-AUDIO",
            title="Internal research meeting",
            source_role="internal_meeting",
            trust_tier=1,
            media_type="audio/wav",
            object_key="sources/internal-research-meeting.wav",
            content=_audio_bytes(),
            observed_at="2026-04-10T09:00:00+08:00",
        ),
        SyntheticAsset(
            source_id="SRC-HISTORICAL",
            title="Approved research baseline",
            source_role="approved_research",
            trust_tier=1,
            media_type="application/pdf",
            object_key="sources/approved-research-baseline.pdf",
            content=_deterministic_pdf(historical),
            observed_at="2026-04-12T18:00:00+08:00",
        ),
        SyntheticAsset(
            source_id="SRC-CLINICAL",
            title="LX-101 Phase II topline results",
            source_role="company_clinical_announcement",
            trust_tier=1,
            media_type="application/pdf",
            object_key="sources/lx101-phase2-results.pdf",
            content=_deterministic_pdf(clinical),
            observed_at="2026-07-10T08:30:00+08:00",
        ),
        SyntheticAsset(
            source_id="SRC-FINANCIAL",
            title="Interim financial update",
            source_role="audited_financial_update",
            trust_tier=1,
            media_type="application/pdf",
            object_key="sources/interim-financial-update.pdf",
            content=_deterministic_pdf(financial),
            observed_at="2026-07-11T17:30:00+08:00",
        ),
        SyntheticAsset(
            source_id="SRC-REG-OFFICIAL",
            title="Registration program update",
            source_role="company_regulatory_update",
            trust_tier=1,
            media_type="application/pdf",
            object_key="sources/registration-program-update.pdf",
            content=_deterministic_pdf(regulatory),
            observed_at="2026-07-12T09:15:00+08:00",
        ),
        SyntheticAsset(
            source_id="SRC-REG-EXPERT",
            title="Expert discussion notes",
            source_role="expert_interview",
            trust_tier=2,
            media_type="application/pdf",
            object_key="sources/expert-discussion-notes.pdf",
            content=_deterministic_pdf(expert),
            observed_at="2026-07-13T15:00:00+08:00",
        ),
        SyntheticAsset(
            source_id="SRC-RUMOR",
            title="Unverified enterprise chat screenshot",
            source_role="chat_screenshot",
            trust_tier=3,
            media_type="image/png",
            object_key="sources/unverified-chat-rumor.png",
            content=_chat_screenshot(),
            observed_at="2026-07-15T09:42:00+08:00",
        ),
    ]
