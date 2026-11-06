## Agent 1 · Post Maker (Sunish)

Turns a dog's rescue photos and videos into a ready-to-post Instagram package for Dogs on a Journey: three collage options in the foundation's style, an animated collage video and a Dutch caption. A volunteer chooses one option, can swap any photo, edits the caption and approves. The video is made only after approval, and nothing is posted automatically.

**How it works**

1. The AI (OpenAI vision model) scores every photo for sharpness, a visible face and light, and picks varied shots: a close-up, a full body and a side or action shot.
2. It builds three different 2×2 collage options (1080 × 1080) and crops each photo around the dog.
3. The agent checks its own collages (head cut off, photos too similar, unreadable text) and fixes problems itself.
4. On the volunteer page, the volunteer picks an option, swaps photos from all of the dog's photos, and checks the caption.
5. After a named approval, it makes a 16-second animated collage video (1080 × 1350) and saves an approval record (name, date and time, chosen option, checksums).

The caption uses only facts from the dog's profile. Anything unknown is written as "nog niet bekend", never guessed. About 2 cents per post.

## Run locally

    cd dog-post-maker
    uv sync
    cp .env.example .env        # set OPENAI_API_KEY, OPENAI_MODEL and PHOTO_ROOT
    uv run python serve.py

Then open http://127.0.0.1:8787.

PHOTO_ROOT is the folder with one photo folder per dog (for example "Azra (Mira)/Foto_s/"). It can be a Google Drive folder synced with Google Drive for desktop, so new photos from rescuers appear automatically.

## Verify

    uv run python -m unittest discover -s tests

69 automated tests: agent tool calling, missing-info questions, photo selection, crop safety, option variety, manual swaps, per-dog caption facts and rejection/retry, approval record, video checks and reading the foundation's photo folders.

## Public demo

Try https://john1919.github.io/dog-post-maker/ for a static demonstration of the
volunteer page. It uses prepared, compressed photos and collage options. Swaps
redraw in the browser; caption edits, names and demo approvals are never saved.
Approval shows Azra's example video, rather than generating a new video.
The demo makes no OpenAI calls and contains no API key or Python server.

The published files are in `docs/`, served by GitHub Pages from `main /docs`.
To refresh the public demo from prepared local input/output, run
`uv run export_demo.py` (requires ffmpeg). This exports only the catalog dogs,
strips image metadata, and checks that the demo stays under 50 MB. Original
source files, local approvals and technical logs remain excluded from Git.

## How it connects

- Dog facts: read from the Match app's dog list (app.py, DOGS), so both tools use the same dossier data.
- Rules: follows the Match app's PHOTO_SELECTION_GUIDE.md, IMAGE_CROPPING_GUIDE.md and CONTENT_WRITING_GUIDE.md.
- Look: the volunteer page uses the same style as the Match app's Editor view (styles.css, curate.html).
- Not included: API keys (.env), dog photos and generated outputs.
