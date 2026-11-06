"""Keep posting assets separate from diagnostics and saved selections."""

from pathlib import Path

POST_FILES = {"collage.jpg", "video.mp4", "caption.txt", "approval.txt"}


def tidy_output(output_dir: Path) -> Path:
    details = output_dir / "details"
    details.mkdir(parents=True, exist_ok=True)
    for source in output_dir.iterdir():
        if source.is_file() and source.name not in POST_FILES:
            target = details / source.name
            if target.exists():
                raise ValueError(f"Both {source} and {target} exist; refusing to overwrite diagnostics.")
            source.rename(target)
    return details
