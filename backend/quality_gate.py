"""Small, independent image-quality gate. No UI or file writes.

This is a sample-tuned technical-quality heuristic, not a trained jewellery model.
Reject means suspected poor quality; callers should allow human correction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


@dataclass(frozen=True)
class QualitySettings:
    blur_threshold: float = 155.0
    minimum_short_side: int = 240
    extreme_pixel_fraction: float = 0.85
    bright_pixel_fraction: float = 0.80

    def __post_init__(self):
        if self.blur_threshold <= 0 or self.minimum_short_side < 1:
            raise ValueError('Blur threshold and minimum short side must be positive.')
        if not 0 < self.extreme_pixel_fraction <= 1:
            raise ValueError('Extreme pixel fraction must be between 0 and 1.')
        if not 0 < self.bright_pixel_fraction <= 1:
            raise ValueError('Bright pixel fraction must be between 0 and 1.')


@dataclass(frozen=True)
class QualityDecision:
    reject: bool
    reasons: tuple[str, ...]
    metrics: dict

    def to_dict(self):
        return asdict(self)


def assess_image(path: str | Path, settings: QualitySettings | None = None) -> QualityDecision:
    """Return one reject bucket for blur, exposure, low resolution or decode failure.

    Focus is measured at a fixed 640-pixel long side (without upscaling).
    This cannot establish jewellery presence, authenticity or inspection suitability.
    """
    settings = settings or QualitySettings()
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source).convert('RGB')
            # Composite transparency so invisible RGB data cannot appear sharp.
            if 'A' in source.getbands() or 'transparency' in source.info:
                rgba = ImageOps.exif_transpose(source).convert('RGBA')
                background = Image.new('RGBA', rgba.size, 'white')
                image = Image.alpha_composite(background, rgba).convert('RGB')
            width, height = image.size
            image.thumbnail((640, 640), Image.Resampling.LANCZOS)
            gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        return QualityDecision(True, ('Unreadable image',), {'error': str(exc)})

    focus = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    dark = float((gray < 25).mean())
    bright = float((gray > 245).mean())
    reasons = []
    if min(width, height) < settings.minimum_short_side:
        reasons.append('Low resolution')
    if dark >= settings.extreme_pixel_fraction:
        reasons.append('Too dark')
    if bright >= settings.bright_pixel_fraction:
        reasons.append('Overexposed / nearly blank')
    if focus < settings.blur_threshold:
        reasons.append('Blur / low pixel detail')
    return QualityDecision(bool(reasons), tuple(reasons), {
        'width': width, 'height': height, 'focus_score': round(focus, 3),
        'dark_fraction': round(dark, 4), 'bright_fraction': round(bright, 4),
    })
