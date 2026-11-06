"""Animated 2×2 collages rendered locally from approved selections; no API calls."""

import math
from pathlib import Path

import numpy as np
from moviepy import VideoClip, VideoFileClip
from PIL import Image, ImageDraw, ImageOps

from .photo_selection import Box
from .video import FPS, end_card, photo_head_box, viewport

HOLD_START = 10.5
HOLD_END = 13.5
DURATION = 16.5
PHOTO_STARTS = (1.6, 2.5, 3.4)
VIDEO_STARTS = (4.8, 7.2)
FADE = .65


def ease(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3 - 2 * value)


def grid_layout(size: tuple[int, int], gutter: int = 8) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    """Four equal rectangles filling the frame with straight, equal gutters."""
    width, height = size
    if (width - gutter) % 2 or (height - gutter) % 2:
        raise ValueError("Frame dimensions must allow four equal integer-sized tiles.")
    tile = ((width - gutter) // 2, (height - gutter) // 2)
    return tile, [(0, 0), (tile[0] + gutter, 0), (0, tile[1] + gutter),
                  (tile[0] + gutter, tile[1] + gutter)]


def title_tile(title: str, size: tuple[int, int], t: float) -> Image.Image:
    """Slide the wave first, then reveal the approved text/logo without stretching."""
    from . import BLUE, ROOT, TILE, _title_card

    width, height = size
    if width != TILE:
        raise ValueError("Animated title uses the approved 536px tile width.")
    card = Image.new("RGB", size, "white")
    wave = Image.new("RGBA", size)
    points = [(x, round(height * (.52 - .05 * math.sin(2 * math.pi * x / width))))
              for x in range(width + 1)]
    ImageDraw.Draw(wave).polygon(points + [(width, height), (0, height)], fill=BLUE)
    card.paste(wave, (round(-width * (1 - ease(t / .85))), 0), wave)
    opacity = ease((t - .85) / .65)
    if opacity:
        # Keep font sizes, title slant and logo exactly as in the approved artwork.
        approved = _title_card(title, ROOT / "assets/logo.png")
        text_card = card.copy()
        text_card.paste(approved.crop((0, 0, width, 220)), (0, 0))
        text_card.paste(approved.crop((0, 400, width, 536)), (0, height - 136))
        card = Image.blend(card, text_card, opacity)
    return card


class AnimatedFrames:
    """Reusable local frame renderer, with explicit close for video readers."""

    def __init__(self, folder: Path, photos: dict, videos: dict, size: tuple[int, int], title: str):
        from . import BLUE

        self.size, self.blue = size, BLUE
        self.tile, self.positions = grid_layout(size)
        self.title = title
        self.final_title = title_tile(title, self.tile, 2)
        self.end = np.asarray(end_card(size))
        self.photos, self.crops, self.readers, self.clips = [], [], [], []
        self.hold = None
        selected = photos["selection"]["chosen"]
        if len(selected) != 3:
            raise ValueError("Animated collage requires three approved photos.")
        scores = {p["file"]: p for p in photos["scores"]["photos"]}
        for item in selected:
            with Image.open(folder / item["file"]) as source:
                photo = ImageOps.exif_transpose(source).convert("RGB")
            score = scores[item["file"]]
            if photo.size != (score["width"], score["height"]):
                raise ValueError("Source photo changed; update photo selection first.")
            body = Box.model_validate(score["dog_box"]).pixels(*photo.size)
            head = Box.model_validate(score["head_box"]).pixels(*photo.size)
            focus_box = item['crop'] if photos.get('approved_collage') else head if item["role"] == "face" else body
            focus = ((focus_box[0] + focus_box[2]) / 2, (focus_box[1] + focus_box[3]) / 2)
            start_zoom = 1.08 if item["role"] == "face" else 1
            self.photos.append(photo)
            try:
                crops=[viewport(*photo.size, photo_head_box(score), self.tile, focus, start_zoom + z)
                       for z in (0, .035)]
            except ValueError:
                if not item.get('manual'):
                    raise
                # An uncertain detector cannot veto volunteer-selected media.
                cx,cy=focus
                hint={'left':max(0,(cx-1)/photo.width),'top':max(0,(cy-1)/photo.height),
                      'right':min(1,(cx+1)/photo.width),'bottom':min(1,(cy+1)/photo.height)}
                crops=[viewport(*photo.size,hint,self.tile,focus,start_zoom+z) for z in (0,.035)]
            self.crops.append(crops)
        # Use the first two approved excerpts long enough for natural playback.
        self.clips = [clip for clip in videos["clips"] if clip["end"] - clip["start"] >= 2][:2]
        if not self.clips:
            self.clips = videos["clips"][:1]
        self.video_crops = []
        try:
            for clip in self.clips:
                reader = VideoFileClip(str(folder / clip["file"]), audio=False)
                self.readers.append(reader)
                self.video_crops.append([viewport(reader.w, reader.h, head, self.tile) for head in clip["heads"]])
        except Exception:
            self.close()
            raise

    def close(self):
        for reader in self.readers:
            reader.close()

    def _crop(self, image, boxes, fraction):
        box = tuple(a + (b - a) * fraction for a, b in zip(*boxes))
        return image.transform(self.tile, Image.Transform.EXTENT, box, Image.Resampling.BICUBIC)

    def collage_at(self, t: float) -> np.ndarray:
        canvas = Image.new("RGB", self.size, self.blue)
        canvas.paste(self.final_title if t >= 1.5 else title_tile(self.title, self.tile, t), self.positions[0])
        for index, (photo, boxes) in enumerate(zip(self.photos, self.crops)):
            start = PHOTO_STARTS[index]
            if t <= start:
                continue
            fraction = min(1.0, max(0.0, (t - start) / (HOLD_START - start)))
            tile = self._crop(photo, boxes, fraction)
            # Two different live excerpts temporarily replace the lower still tiles.
            clip_index = index - 1
            if 0 <= clip_index < len(self.clips):
                clip, reader = self.clips[clip_index], self.readers[clip_index]
                clip_start = VIDEO_STARTS[clip_index]
                play_duration = min(3.0, max(2.0, clip["end"] - clip["start"]))
                elapsed = t - clip_start
                if 0 <= elapsed < play_duration:
                    progress = elapsed / play_duration
                    timestamp = clip["start"] + progress * (clip["end"] - clip["start"])
                    timestamp = min(timestamp, clip["end"] - 1 / reader.fps)
                    frame = Image.fromarray(reader.get_frame(timestamp))
                    live = self._crop(frame, self.video_crops[clip_index], progress)
                    opacity = min(ease(elapsed / .2), ease((play_duration - elapsed) / .2))
                    tile = Image.blend(tile, live, opacity)
            opacity = ease((t - start) / FADE)
            if opacity < 1:
                tile = Image.blend(Image.new("RGB", self.tile, self.blue), tile, opacity)
            canvas.paste(tile, self.positions[index + 1])
        return np.asarray(canvas)

    def frame_at(self, t: float) -> np.ndarray:
        if t >= HOLD_END:
            return self.end
        if t >= HOLD_START:
            if self.hold is None:
                self.hold = self.collage_at(HOLD_START)
            return self.hold
        return self.collage_at(t)


def make_animated_collage(folder: Path, photo_data: dict, video_data: dict,
                          output_path: Path, size: tuple[int, int], title: str) -> dict:
    """Render a silent 16.5s animated grid, entirely from cached selections."""
    frames = AnimatedFrames(folder, photo_data, video_data, size, title)
    movie = VideoClip(frame_function=frames.frame_at, duration=DURATION)
    try:
        print(f"Rendering {output_path.name}: {size[0]}×{size[1]}, {DURATION}s; no AI calls...", flush=True)
        movie.write_videofile(str(output_path), fps=FPS, codec="libx264", audio=False,
                              preset="veryfast", threads=4, logger=None,
                              ffmpeg_params=["-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
    finally:
        movie.close()
        frames.close()
    return {"file": output_path.name, "size": list(size), "tile_size": list(frames.tile),
            "gutter": 8, "duration": DURATION, "audio": False,
            "photo_reveals": list(PHOTO_STARTS), "video_starts": list(VIDEO_STARTS[:len(frames.clips)]),
            "clips": [{"file": c["file"], "start": c["start"], "end": c["end"]} for c in frames.clips],
            "hold": [HOLD_START, HOLD_END], "end_card": [HOLD_END, DURATION], "api_cost_usd": 0}
