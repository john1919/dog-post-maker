"""Photo selection, caption and video tools with an interactive review agent."""

import argparse
import math
from pathlib import Path

import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

from .photo_selection import choose_photos, score_photos, write_selection_report
from .caption import write_caption

ROOT = Path(__file__).resolve().parents[2]
BLUE = "#7AA3B8"
SIZE = 1080
GUTTER = 8
TILE = (SIZE - GUTTER) // 2
FONTS = ROOT / "assets" / "fonts"
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


def crop_photo(
    photo_path: str | Path,
    crop_box: tuple[int, int, int, int] | None = None,
    size: tuple[int, int] = (TILE, TILE),
) -> Image.Image:
    """Return an RGB tile; boxes use pixels after EXIF orientation correction.

    Always fill the tile without padding. An explicit box selects a region
    before a centered aspect-ratio crop. In manual mode, choose a box that
    keeps the head and face safely inside the final square.
    """
    with Image.open(photo_path) as source:
        photo = ImageOps.exif_transpose(source).convert("RGB")
    if crop_box is not None:
        left, top, right, bottom = crop_box
        if not (0 <= left < right <= photo.width and 0 <= top < bottom <= photo.height):
            raise ValueError(f"Crop box is outside {photo_path}: {crop_box}")
        photo = photo.crop(crop_box)
    return ImageOps.fit(photo, size, method=Image.Resampling.LANCZOS)


def _font(filename: str, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(FONTS / filename), size)
    font.set_variation_by_axes([800 if bold else 400])
    return font


def _fit_text(text: str, filename: str, size: int, width: int, bold: bool = False):
    while size >= 12:
        font = _font(filename, size, bold)
        if font.getlength(text) <= width:
            return font
        size -= 1
    raise ValueError("Title is too long to fit legibly; shorten dog.yaml's title.")


def _placeholder_logo(path: Path) -> None:
    """Make a simple paw placeholder; replace assets/logo.png with the rescue logo."""
    path.parent.mkdir(parents=True, exist_ok=True)
    logo = Image.new("RGBA", (240, 240))
    draw = ImageDraw.Draw(logo)
    draw.ellipse((65, 110, 175, 207), fill="black")
    for box in [(24, 72, 76, 133), (65, 25, 115, 91),
                (125, 25, 175, 91), (166, 72, 218, 133)]:
        draw.ellipse(box, fill="black")
    logo.save(path)


def _title_card(title: str, logo_path: Path) -> Image.Image:
    # Draw at double resolution to smooth the wave, logo and text edges.
    scale = 2
    side = TILE * scale
    card = Image.new("RGB", (side, side), "white")
    draw = ImageDraw.Draw(card)
    wave = [(x, round(side * (0.52 - 0.05 * math.sin(2 * math.pi * x / side))))
            for x in range(side + 1)]
    draw.polygon(wave + [(side, side), (0, side)], fill=BLUE)
    title_font = _fit_text(title, "Baloo2.ttf", 58 * scale, side - 128 * scale, True)
    # Baloo 2 has no italic face: give only the title a subtle 8-degree slant.
    title_layer = Image.new("RGBA", (side, 90 * scale))
    ImageDraw.Draw(title_layer).text(
        (2, 2), title, font=title_font, fill=BLUE,
        anchor="lt", stroke_width=1, stroke_fill=BLUE,
    )
    bounds = title_layer.getbbox()
    title_layer = title_layer.crop(bounds)
    slant = math.tan(math.radians(8))
    shift = math.ceil(title_layer.height * slant)
    title_layer = title_layer.transform(
        (title_layer.width + shift, title_layer.height), Image.Transform.AFFINE,
        (1, slant, -shift, 0, 1, 0), resample=Image.Resampling.BICUBIC,
    )
    card.paste(title_layer, (64 * scale, 77 * scale), title_layer)
    draw.text((64 * scale, 145 * scale), "zoekt een mand",
              font=_font("Nunito.ttf", 38 * scale), fill="black", anchor="lt")
    if not logo_path.exists():
        _placeholder_logo(logo_path)
    with Image.open(logo_path) as source:
        logo = source.convert("RGBA")
        # Remove transparent margins so the visible logo shares the text alignment.
        bounds = logo.getbbox()
        if bounds:
            logo = logo.crop(bounds)
        logo = ImageOps.contain(logo, (84 * scale, 84 * scale))
    card.paste(logo, (64 * scale, 430 * scale), logo)
    info_font = _font("Nunito.ttf", 26 * scale)
    draw.text((165 * scale, 445 * scale), "meer informatie op",
              font=info_font, fill="black", anchor="lt")
    draw.text((165 * scale, 477 * scale), "dogsonajourney.com",
              font=info_font, fill="black", anchor="lt")
    return card.resize((TILE, TILE), Image.Resampling.LANCZOS)


def make_collage(
    name: str,
    photos: list[Image.Image],
    output_path: str | Path,
    title: str | None = None,
    logo_path: str | Path = ROOT / "assets" / "logo.png",
) -> Path:
    """Render a fixed 1080px 2×2 collage from exactly three prepared photo tiles."""
    if len(photos) != 3 or any(photo.size != (TILE, TILE) for photo in photos):
        raise ValueError(f"Provide exactly three {TILE}×{TILE} tiles from crop_photo.")
    collage = Image.new("RGB", (SIZE, SIZE), BLUE)
    cards = [_title_card(title if title is not None else f"{name} in NL", Path(logo_path)), *photos]
    for card, position in zip(cards, [(0, 0), (TILE + GUTTER, 0),
                                     (0, TILE + GUTTER), (TILE + GUTTER, TILE + GUTTER)]):
        collage.paste(card, position)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    collage.save(output_path, quality=95, subsampling=0)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Choose a collage, then approve one animated video.")
    parser.add_argument("folder", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--options-only", action="store_true", help="Make draft options/caption, then stop before approval.")
    mode.add_argument("--caption-only", action="store_true", help="Generate a draft caption in details/.")
    mode.add_argument("--agent", action="store_true", help="Interactive option/approval loop (default).")
    args = parser.parse_args()
    from .workflow import DraftAgent, _caption_draft, make_options, run_terminal
    try:
        if args.options_only:
            result = make_options(args.folder)
            print("Overview:", result["overview"])
            print(result["caption"])
            print("Stopped before approval; no final assets or video generated.")
        elif args.caption_only:
            print(_caption_draft(DraftAgent(args.folder)))
        else:
            run_terminal(args.folder)
    except (OSError, ValueError, yaml.YAMLError) as error:
        parser.exit(1, f"Cannot prepare post: {error}\n")
    except (EOFError, KeyboardInterrupt):
        print("Stopped without approval. Drafts remain in details/; nothing posted.")
