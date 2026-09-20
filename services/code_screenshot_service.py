"""
تسليم الكود كصورة منسقة.

بدل إرسال الكود كنص فقط، يولّد خدمةً صورة PNG أنيقة تحمل
الرقم/الكود/الخدمة — سهلة الحفظ والمشاركة. تعتمد على Pillow
مع fallback آمن لإرسال النص العادي إذا تعذر الرسم.
"""

from __future__ import annotations

import io
import logging
import textwrap
from decimal import Decimal

from services.feature_service import FeatureService

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont

    _HAS_PIL = True
except Exception:  # pragma: no cover
    _HAS_PIL = False
    logger.warning("Pillow غير متوفر — سيُرسل الكود نصاً.")


class CodeScreenshotService:
    @staticmethod
    async def enabled() -> bool:
        return await FeatureService.enabled("code_screenshot_delivery")

    @staticmethod
    async def build_image(
        service_name: str,
        country_name: str,
        phone_number: str,
        code: str,
        extra_lines: list[str] | None = None,
        brand: str = "رقمتي",
    ) -> bytes | None:
        """يرسم البطاقة ويعيد محتوى PNG. يُرجع None إذا العجز عن الرسم."""
        if not _HAS_PIL:
            return None
        try:
            width, height = 900, 420
            img = Image.new("RGB", (width, height), "#0f172a")
            draw = ImageDraw.Draw(img)

            font_path = None
            try:
                from matplotlib import font_manager  # pragma: no cover

                for f in font_manager.findSystemFonts():
                    if "Cairo" in f or "DejaVuSans" in f:
                        font_path = f
                        break
            except Exception:
                pass

            def _font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont | None:
                if font_path:
                    try:
                        return ImageFont.truetype(font_path, size)
                    except Exception:
                        pass
                try:
                    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
                except Exception:
                    return ImageFont.load_default()

            header_font = _font(30, bold=True)
            label_font = _font(18)
            value_font = _font(34, bold=True)
            code_font = _font(40, bold=True)

            draw.rounded_rectangle((30, 30, width - 30, height - 30), radius=24, fill="#1e293b", outline="#334155", width=2)

            draw.text((60, 55), f"{brand} — {service_name}", font=header_font, fill="#f59e0b")
            draw.text((60, 105), f"{country_name} · +{phone_number}", font=label_font, fill="#94a3b8")

            draw.rounded_rectangle((60, 150, width - 60, 260), radius=16, fill="#0f172a", outline="#334155", width=1)
            draw.text((80, 172), "الكود (Code)", font=label_font, fill="#64748b")
            code_text = code if len(code) <= 20 else textwrap.shorten(code, width=20, placeholder="…")
            draw.text((80, 205), code_text, font=code_font, fill="#4ade80")

            y = 285
            for line in (extra_lines or []):
                safe = textwrap.shorten(line, width=52, placeholder="…")
                draw.text((60, y), safe, font=label_font, fill="#cbd5e1")
                y += 30

            draw.text((60, height - 85), "✨ هذا الكود جاهز للاستخدام الآن", font=label_font, fill="#f472b6")

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            logger.exception("فشل رسم بطاقة الكود")
            return None

    @staticmethod
    def fallback_text(
        service_name: str, phone_number: str, code: str, extra_text: str | None = None
    ) -> str:
        parts = [
            f"📱 <b>{service_name}</b> · {phone_number}",
            "",
            f"🧾 الكود: <code>{code}</code>",
        ]
        if extra_text:
            parts.append("")
            parts.append(extra_text)
        return "\n".join(parts)