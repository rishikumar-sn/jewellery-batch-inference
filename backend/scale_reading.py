"""Read and confirm a digital scale display independently of jewellery assessment."""
import base64
import io
import re
import time
from decimal import Decimal

from PIL import Image

READING = re.compile(r'\s*([+-]?\d+(?:[.,]\d+)?)\s*(kg|mg|g|oz|ct)?\s*', re.I)
PROMPT = (
    'Read only the number on the electronic weighing scale screen. Transcribe each digit and '
    'decimal point exactly. Include a unit only if legible on the screen. Ignore labels, stickers '
    'and lights. If any digit is unclear answer unreadable. If there is no screen answer not_visible. '
    'Output only the reading or status.'
)
DISPLAY_BANDS = {'top': (0, .6), 'middle': (.2, .8), 'bottom': (.5, 1)}
GRAMS_PER_UNIT = {'kg': Decimal('1000'), 'mg': Decimal('.001'), 'oz': Decimal('28.349523125'), 'ct': Decimal('.2')}


def read_scale(reporter, original, area, attempts):
    width, height = original.size
    start, end = DISPLAY_BANDS.get(area, DISPLAY_BANDS['bottom'])
    crop = original.crop((0, int(start * height), width, int(end * height)))

    def read(source, side, stage):
        image = source.copy()
        image.thumbnail((side, side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        uri = 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()
        tick = time.perf_counter()
        attempt = dict(stage=stage, input_size=image.size)
        try:
            response = reporter.request('/v1/chat/completions', {
                'messages': [{'role': 'user', 'content': [
                    {'type': 'image_url', 'image_url': {'url': uri}}, {'type': 'text', 'text': PROMPT}]}],
                'temperature': 0, 'max_tokens': 35, 'chat_template_kwargs': {'enable_thinking': False}})
            choice = response['choices'][0]
            raw = (choice['message'].get('content') or '').strip()
            attempt.update(raw=raw, finish_reason=choice.get('finish_reason'),
                           usage=response.get('usage'), timings=response.get('timings'))
            if choice.get('finish_reason') == 'stop':
                if raw in ('not_visible', 'unreadable'):
                    return raw
                match = READING.fullmatch(raw)
                if match:
                    return match[1], match[2].lower() if match[2] else None
        except Exception as exc:
            attempt['error'] = str(exc)
        finally:
            attempt['seconds'] = round(time.perf_counter() - tick, 3)
            attempts.append(attempt)
        return 'unknown'

    first = read(crop, 800, 'scale_reading')
    source = crop
    if not isinstance(first, tuple):
        source = original
        second = read(source, 800, 'scale_full_frame')
        if not isinstance(second, tuple):
            display = 'unreadable' if 'unreadable' in (first, second) else second
            return dict(presence='present', display=display)
        first = second
    # Keep source pixels for the decimal point. A slightly tighter view changes
    # the model's visual sampling without downscaling the display a second time.
    inset = source.width // 20
    closeup = source.crop((inset, 0, source.width - inset, max(1, round(source.height * .85))))
    second = read(closeup, 800, 'scale_confirmation')
    if not isinstance(second, tuple) or first[0] != second[0] or (first[1] and second[1] and first[1] != second[1]):
        return dict(presence='present', display='unreadable')
    digits, unit = first[0], first[1] or second[1]
    grams = digits
    if unit in GRAMS_PER_UNIT:
        grams = format(Decimal(digits.replace(',', '.')) * GRAMS_PER_UNIT[unit], 'f')
    return dict(presence='present', display='readable', weight_text=grams + ' g',
                weight_value=grams, weight_unit='g', display_reading=digits + (' ' + unit if unit else ''))
