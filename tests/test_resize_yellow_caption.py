from pathlib import Path

import pytest
from PIL import Image

from tools.resize_yellow_caption import (
    fit_font,
    load_captions,
    render_text_heading,
    resize_yellow_heading,
)


BACKGROUND = (12, 18, 31)
YELLOW = (253, 233, 79)


def _yellow_bbox(image):
    red, _green, blue = image.split()
    mask = red.point(lambda value: 255 if value > 200 else 0)
    dark_blue = blue.point(lambda value: 255 if value < 150 else 0)
    return mask.getbbox() if dark_blue.getbbox() else None


def test_short_heading_grows_to_scale_cap_and_stays_centered():
    strip = Image.new("RGB", (920, 80), BACKGROUND)
    strip.paste(YELLOW, (360, 25, 560, 55))

    result = resize_yellow_heading(strip)
    bbox = _yellow_bbox(result)

    assert bbox is not None
    assert bbox[2] - bbox[0] == 264
    assert bbox[3] - bbox[1] == 40
    assert abs(((bbox[0] + bbox[2]) / 2) - 460) <= 1


def test_long_heading_is_limited_by_safe_width():
    strip = Image.new("RGB", (920, 80), BACKGROUND)
    strip.paste(YELLOW, (60, 20, 860, 60))

    result = resize_yellow_heading(strip)
    bbox = _yellow_bbox(result)

    assert bbox is not None
    assert bbox[2] - bbox[0] == 860
    assert bbox[0] == 30
    assert bbox[2] == 890


def test_empty_strip_becomes_clean_banner_background():
    noisy_background = Image.new("RGB", (920, 80), (13, 19, 32))

    result = resize_yellow_heading(noisy_background)

    assert result.getpixel((0, 0)) == BACKGROUND
    assert result.getbbox() == (0, 0, 920, 80)


def test_rendered_heading_uses_largest_font_that_fits():
    candidates = (
        Path("/Users/diowcnx/Library/Fonts/Prompt-ExtraBold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    font_path = next((path for path in candidates if path.is_file()), None)
    if font_path is None:
        pytest.skip("No TrueType test font is available")
    font, bbox = fit_font(
        "Yellow heading fits the banner",
        font_path,
        max_width=860,
        target_height=60,
    )

    assert font.size > 40
    assert bbox[2] - bbox[0] <= 860
    assert bbox[3] - bbox[1] <= 60

    rendered = render_text_heading(
        (920, 80),
        "A short heading",
        font_path,
    )
    assert _yellow_bbox(rendered) is not None


def test_load_captions_accepts_open_ended_last_segment(tmp_path):
    path = tmp_path / "captions.json"
    path.write_text(
        '[{"start": 0, "end": 4, "text": "หนึ่ง"},'
        '{"start": 4, "end": null, "text": "สอง"}]',
        encoding="utf-8",
    )

    captions = load_captions(path)

    assert captions[0]["text"] == "หนึ่ง"
    assert captions[1]["end"] == float("inf")
