"""Compose native, stock and candidate captures with aligned FLIP detail views."""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageStat

COLUMN_WIDTH = 500
GAP = 14
FULL_HEIGHT = 281
DETAIL_WIDTH = (COLUMN_WIDTH - GAP) // 2
DETAIL_HEIGHT = 137
PADDING = 22
SCENE_HEIGHT = 541
PANEL_ROLES = ("native", "stock", "candidate")
FOCUS_COLOR = "#67e8f9"


def _rgb(path: Path) -> Image.Image:
    """Load an evidence PNG and detach from its file handle."""
    with Image.open(path) as image:
        return image.convert("RGB")


def _focus_region(stock: Image.Image, candidate: Image.Image) -> tuple[tuple[int, int, int, int] | None, str]:
    """Find a localized hotspot using full-resolution heatmap channel sums."""
    width, height = stock.size
    tiles = [
        (width * column // 8, height * row // 8, width * (column + 1) // 8, height * (row + 1) // 8)
        for row in range(8)
        for column in range(8)
    ]
    changes = ImageChops.difference(stock, candidate)
    for image, caption in ((changes, "Largest heatmap change"), (candidate, "Strongest reference error")):
        # Summing channels before pooling preserves sparse changes near the quantization step.
        scores = [sum(ImageStat.Stat(image.crop(tile)).sum) for tile in tiles]
        peak = max(scores)
        if peak > min(scores):
            return tiles[scores.index(peak)], caption
    return None, "No localized FLIP hotspot"


def write_comparison_sheet(previews: Path, scenes: dict, output: Path) -> None:
    """Export one inspection sheet with matched crops and per-frame FLIP scores.

    Args:
        previews: Directory with original PNGs and the two FLIP heatmaps per scene.
        scenes: Per-scene FLIP report including the preview frame's scores.
        output: Destination JPEG artifact.
    """
    width = 2 * PADDING + 3 * COLUMN_WIDTH + 2 * GAP
    canvas = Image.new("RGB", (width, 78 + len(scenes) * SCENE_HEIGHT), "#101820")
    draw = ImageDraw.Draw(canvas)
    title = ImageFont.load_default(size=30)
    heading = ImageFont.load_default(size=22)
    label = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=15)
    draw.text((PADDING, 15), "Filament FSR1  /  LDR-FLIP", font=title, fill="#f5f9fc")
    draw.text((PADDING, 53), "Native 1080p reference    /    Frame 060 in each scene", font=small, fill="#a9bac6")

    for row, (scene, summary) in enumerate(scenes.items()):
        images = {role: _rgb(previews / f"{scene}-{role}.png") for role in PANEL_ROLES}
        maps = {role: _rgb(previews / f"{scene}-{role}-flip.png") for role in ("stock", "candidate")}
        crop, focus = _focus_region(maps["stock"], maps["candidate"])
        frame = next(value for value in summary["frames"] if value["frame"] == "frame-060")
        delta = frame["improvement"]
        delta_color = "#86efac" if delta > 0 else "#fda4af" if delta < 0 else "#c5d3dd"
        y = 78 + row * SCENE_HEIGHT
        draw.line((PADDING, y, width - PADDING, y), fill="#526675")
        draw.text((PADDING, y + 10), scene, font=heading, fill="#f5f9fc")
        draw.text((width - PADDING, y + 14), f"FLIP gain {delta:+.6f}", anchor="ra", font=label, fill=delta_color)
        titles = (
            "Native reference",
            f"Official baseline  /  {frame['stock_mean_error']:.6f}",
            f"Candidate  /  {frame['candidate_mean_error']:.6f}",
        )
        for col, (role, caption) in enumerate(zip(PANEL_ROLES, titles, strict=True)):
            x = PADDING + col * (COLUMN_WIDTH + GAP)
            draw.text((x, y + 41), caption, font=label, fill="#f5f9fc")
            image = images[role]
            full_y = y + 66
            canvas.paste(image.resize((COLUMN_WIDTH, FULL_HEIGHT), Image.Resampling.LANCZOS), (x, full_y))
            if crop is None:
                if role == "native":
                    draw.text((x, full_y + FULL_HEIGHT + 16), focus, font=small, fill="#a9bac6")
                continue
            left, top, right, bottom = crop
            draw.rectangle(
                (
                    x + left * COLUMN_WIDTH // image.width,
                    full_y + top * FULL_HEIGHT // image.height,
                    x + right * COLUMN_WIDTH // image.width - 1,
                    full_y + bottom * FULL_HEIGHT // image.height - 1,
                ),
                outline=FOCUS_COLOR,
                width=2,
            )
            detail_y = full_y + FULL_HEIGHT + 29
            draw.text((x, detail_y - 22), "Same region", font=small, fill=FOCUS_COLOR)
            canvas.paste(image.crop(crop).resize((DETAIL_WIDTH, DETAIL_HEIGHT), Image.Resampling.LANCZOS), (x, detail_y))
            map_x = x + DETAIL_WIDTH + GAP
            if role in maps:
                draw.text((map_x, detail_y - 22), "FLIP error  /  bright = higher", font=small, fill="#a9bac6")
                canvas.paste(
                    maps[role].crop(crop).resize((DETAIL_WIDTH, DETAIL_HEIGHT), Image.Resampling.NEAREST), (map_x, detail_y)
                )
            else:
                draw.text((map_x, detail_y + 13), focus, font=small, fill="#a9bac6")
                draw.text((map_x, detail_y + 41), f"Region ({left}, {top})", font=small, fill="#a9bac6")
                draw.text((map_x, detail_y + 69), "Positive FLIP gain = better", font=small, fill="#a9bac6")
    canvas.save(output, format="JPEG", quality=92, subsampling=0, optimize=True)
