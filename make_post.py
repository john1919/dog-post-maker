"""Choose a collage and approve one animated video: uv run make_post.py input/azra."""
import sys
from pathlib import Path

# Run directly from the checkout, including when editable .pth files are disabled.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from dog_post_maker import main

if __name__ == "__main__":
    main()
