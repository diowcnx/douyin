#!/usr/bin/env python3
"""Enlarge a baked-in yellow heading while keeping it inside its black banner.

The utility is intentionally content-aware: it measures the yellow pixels in
every frame and chooses the largest proportional scale that fits both the
configured width and height.  Short headings therefore grow more than long
ones, and no caption can spill over the banner border.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont


RGB = Tuple[int, int, int]

DEFAULT_REGION = (80, 1435, 920, 80)
DEFAULT_BACKGROUND = (12, 18, 31)
DEFAULT_YELLOW = (253, 233, 79)

_BBOX_ALPHA_LUT = [0 if value <= 16 else 255 for value in range(256)]
_EDGE_ALPHA_LUT = [0 if value <= 5 else min(255, (value - 5) * 2) for value in range(256)]


def _yellow_alpha(image: Image.Image) -> Image.Image:
    """Return an antialiased mask for yellow pixels on a dark blue-black field."""
    red, _green, blue = image.convert("RGB").split()
    yellow_strength = ImageChops.subtract(red, blue)
    return yellow_strength.point(_EDGE_ALPHA_LUT)


def resize_yellow_heading(
    strip: Image.Image,
    *,
    background: RGB = DEFAULT_BACKGROUND,
    yellow: RGB = DEFAULT_YELLOW,
    max_scale: float = 1.32,
    max_width: int = 860,
    target_height: int = 60,
) -> Image.Image:
    """Erase and proportionally enlarge the yellow heading in one banner strip.

    The returned image has the same dimensions as ``strip``.  Scaling is capped
    by ``max_scale``, ``max_width`` and ``target_height`` so a long heading is
    never enlarged past the banner's safe area.
    """
    strip = strip.convert("RGB")
    alpha = _yellow_alpha(strip)
    bbox = alpha.point(_BBOX_ALPHA_LUT).getbbox()
    output = Image.new("RGB", strip.size, background)
    if bbox is None:
        return output

    source_width = bbox[2] - bbox[0]
    source_height = bbox[3] - bbox[1]
    if source_width <= 0 or source_height <= 0:
        return output

    scale = min(
        max_scale,
        float(max_width) / float(source_width),
        float(target_height) / float(source_height),
    )
    scale = max(1.0, scale)
    target_size = (
        max(1, round(source_width * scale)),
        max(1, round(source_height * scale)),
    )

    heading_alpha = alpha.crop(bbox).resize(target_size, Image.Resampling.LANCZOS)
    heading = Image.new("RGB", target_size, yellow)
    position = (
        (strip.width - target_size[0]) // 2,
        (strip.height - target_size[1]) // 2,
    )
    output.paste(heading, position, heading_alpha)
    return output


def fit_font(
    text: str,
    font_path: Path,
    *,
    max_width: int,
    target_height: int,
    max_size: int = 68,
    min_size: int = 30,
) -> Tuple[ImageFont.FreeTypeFont, Tuple[int, int, int, int]]:
    """Choose the largest font whose actual glyph bounds fit the title slot."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(str(font_path), size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= target_height:
            return font, bbox
    raise ValueError(f"Caption cannot fit the title slot: {text}")


def render_text_heading(
    size: Tuple[int, int],
    text: str,
    font_path: Path,
    *,
    background: RGB = DEFAULT_BACKGROUND,
    yellow: RGB = DEFAULT_YELLOW,
    max_width: int = 860,
    target_height: int = 60,
) -> Image.Image:
    """Render a crisp, centered title rather than enlarging compressed pixels."""
    output = Image.new("RGB", size, background)
    font, bbox = fit_font(
        text,
        font_path,
        max_width=max_width,
        target_height=target_height,
    )
    center_x = size[0] / 2.0
    center_y = size[1] / 2.0
    position = (
        center_x - (bbox[0] + bbox[2]) / 2.0,
        center_y - (bbox[1] + bbox[3]) / 2.0,
    )
    ImageDraw.Draw(output).text(position, text, font=font, fill=yellow)
    return output


def load_captions(path: Path) -> List[Dict[str, object]]:
    """Load and validate timed captions from a small JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Caption file must contain a non-empty JSON array")
    captions = []
    previous_end = 0.0
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("Each caption must be a JSON object")
        start = float(item.get("start", -1))
        end_value = item.get("end")
        end = float(end_value) if end_value is not None else float("inf")
        text = str(item.get("text", "")).strip()
        if start < previous_end or end <= start or not text:
            raise ValueError("Captions must be non-empty, ordered and non-overlapping")
        captions.append({"start": start, "end": end, "text": text})
        previous_end = end
    return captions


def _caption_at(captions: List[Dict[str, object]], timestamp: float) -> Optional[str]:
    for caption in captions:
        if float(caption["start"]) <= timestamp < float(caption["end"]):
            return str(caption["text"])
    return None


def _probe_video(path: Path, ffprobe: str) -> Dict[str, object]:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found in {path}")
    return streams[0]


def _build_strip_video(
    input_path: Path,
    strip_path: Path,
    *,
    ffmpeg: str,
    fps: str,
    region: Tuple[int, int, int, int],
    background: RGB,
    yellow: RGB,
    max_scale: float,
    max_width: int,
    target_height: int,
    captions: Optional[List[Dict[str, object]]] = None,
    font_path: Optional[Path] = None,
) -> int:
    x, y, width, height = region
    decoder = subprocess.Popen(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(input_path),
            "-an",
            "-sn",
            "-dn",
            "-vf",
            f"crop={width}:{height}:{x}:{y}",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    encoder = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-video_size",
            f"{width}x{height}",
            "-framerate",
            fps,
            "-i",
            "-",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            str(strip_path),
        ],
        stdin=subprocess.PIPE,
    )

    if decoder.stdout is None or encoder.stdin is None:
        raise RuntimeError("Could not open the FFmpeg frame pipeline")

    frame_size = width * height * 3
    frame_count = 0
    rendered_headings: Dict[str, Image.Image] = {}
    fps_value = float(Fraction(fps))
    try:
        while True:
            raw_frame = decoder.stdout.read(frame_size)
            if not raw_frame:
                break
            if len(raw_frame) != frame_size:
                raise RuntimeError("FFmpeg returned an incomplete video frame")
            strip = Image.frombytes("RGB", (width, height), raw_frame)
            caption_text = _caption_at(captions, frame_count / fps_value) if captions else None
            if caption_text is not None:
                if font_path is None:
                    raise ValueError("A font path is required when using timed captions")
                if caption_text not in rendered_headings:
                    rendered_headings[caption_text] = render_text_heading(
                        (width, height),
                        caption_text,
                        font_path,
                        background=background,
                        yellow=yellow,
                        max_width=max_width,
                        target_height=target_height,
                    )
                resized = rendered_headings[caption_text]
            else:
                resized = resize_yellow_heading(
                    strip,
                    background=background,
                    yellow=yellow,
                    max_scale=max_scale,
                    max_width=max_width,
                    target_height=target_height,
                )
            encoder.stdin.write(resized.tobytes())
            frame_count += 1
    except Exception:
        decoder.kill()
        encoder.kill()
        raise
    finally:
        decoder.stdout.close()
        encoder.stdin.close()

    decoder_status = decoder.wait()
    encoder_status = encoder.wait()
    if decoder_status != 0 or encoder_status != 0:
        raise RuntimeError(
            f"FFmpeg frame pipeline failed (decoder={decoder_status}, encoder={encoder_status})"
        )
    return frame_count


def process_video(
    input_path: Path,
    output_path: Path,
    *,
    region: Tuple[int, int, int, int] = DEFAULT_REGION,
    background: RGB = DEFAULT_BACKGROUND,
    yellow: RGB = DEFAULT_YELLOW,
    max_scale: float = 1.32,
    max_width: int = 860,
    target_height: int = 60,
    ffmpeg: Optional[str] = None,
    ffprobe: Optional[str] = None,
    captions: Optional[List[Dict[str, object]]] = None,
    font_path: Optional[Path] = None,
) -> int:
    """Resize the yellow heading and write a new video, preserving source audio."""
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if input_path == output_path:
        raise ValueError("Input and output paths must be different")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if captions and (font_path is None or not font_path.is_file()):
        raise FileNotFoundError(font_path or "caption font")

    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    ffprobe = ffprobe or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe must be installed and available on PATH")

    info = _probe_video(input_path, ffprobe)
    video_width = int(info["width"])
    video_height = int(info["height"])
    x, y, width, height = region
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValueError("Caption region must have positive dimensions and coordinates")
    if x + width > video_width or y + height > video_height:
        raise ValueError(
            f"Caption region {region} is outside the {video_width}x{video_height} video"
        )
    if max_width > width or target_height > height:
        raise ValueError("Caption limits must fit inside the selected region")

    fps_fraction = Fraction(str(info.get("avg_frame_rate", "0/1")))
    if fps_fraction <= 0:
        raise RuntimeError("Could not determine the source frame rate")
    fps = f"{fps_fraction.numerator}/{fps_fraction.denominator}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="yellow_caption_") as temp_dir:
        strip_path = Path(temp_dir) / "caption-strip.mkv"
        frame_count = _build_strip_video(
            input_path,
            strip_path,
            ffmpeg=ffmpeg,
            fps=fps,
            region=region,
            background=background,
            yellow=yellow,
            max_scale=max_scale,
            max_width=max_width,
            target_height=target_height,
            captions=captions,
            font_path=font_path,
        )
        command = [
            ffmpeg,
            "-y",
            "-v",
            "error",
            "-i",
            str(input_path),
            "-i",
            str(strip_path),
            "-filter_complex",
            f"[0:v][1:v]overlay={x}:{y}:shortest=1[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-map_metadata",
            "0",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "16",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        subprocess.run(command, check=True)
    return frame_count


def _parse_rgb(value: str) -> RGB:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise argparse.ArgumentTypeError("Color must be a six-digit RGB hex value")
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Color must be a six-digit RGB hex value") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enlarge a baked-in yellow heading so it balances with its black banner."
    )
    parser.add_argument("input", type=Path, help="Source MP4/MOV file")
    parser.add_argument("output", type=Path, help="Output video path")
    parser.add_argument("--x", type=int, default=DEFAULT_REGION[0])
    parser.add_argument("--y", type=int, default=DEFAULT_REGION[1])
    parser.add_argument("--width", type=int, default=DEFAULT_REGION[2])
    parser.add_argument("--height", type=int, default=DEFAULT_REGION[3])
    parser.add_argument("--max-scale", type=float, default=1.32)
    parser.add_argument("--max-width", type=int, default=860)
    parser.add_argument("--target-height", type=int, default=60)
    parser.add_argument("--background", type=_parse_rgb, default=DEFAULT_BACKGROUND)
    parser.add_argument("--yellow", type=_parse_rgb, default=DEFAULT_YELLOW)
    parser.add_argument(
        "--captions",
        type=Path,
        help="JSON timeline used to redraw crisp text instead of scaling baked pixels",
    )
    parser.add_argument("--font", type=Path, help="TTF/OTF font used with --captions")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        captions = load_captions(args.captions) if args.captions else None
        frames = process_video(
            args.input,
            args.output,
            region=(args.x, args.y, args.width, args.height),
            background=args.background,
            yellow=args.yellow,
            max_scale=args.max_scale,
            max_width=args.max_width,
            target_height=args.target_height,
            captions=captions,
            font_path=args.font,
        )
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {args.output} ({frames} frames processed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
