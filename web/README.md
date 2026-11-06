# Volunteer post maker

Plain HTML/CSS/JS, served by `../serve.py`. No frontend build, npm packages,
Streamlit or remote hosting are needed. The page uses only system fonts.

From the project root:

```sh
uv run serve.py --port 8787
```

Open http://127.0.0.1:8787. The server binds to loopback only. Keep its terminal
running. Start/stop the server independently of the CLI agent.

The visual language follows RescueAI_match: cream, ink, coral, sage, Georgia
headings, Arial small text, and the shared brand header. That project's files
are not imported or edited at runtime; this folder is self-contained.

## How it works

- The dog dropdown lists the 16 imported catalog dogs, with no photos yet,
  waiting for approval, or approved status. Their facts appear below it.
- Existing options load without making an AI request. Missing options can be
  generated when at least four source photos exist.
- Choosing an option saves the choice, without approving or rendering anything.
- Select a photo tile, then one of the dog's photos. All photos are shown; photos
  in the current collage are marked. Manual photo choices always apply, regardless of AI role/usability/variety scores.
  A friendly note may describe the usual tile role; only a possibly cropped head
  or face is warned about. Tail cuts are fine. AI review cannot veto a swap.
- The caption is editable. Save explicitly, or approval saves edits first.
- Approval requires the volunteer's name. It delegates to workflow.approve(),
  which calls make_video(), checks the result and writes the receipt last.
- Approved posts display the video and download links. Nothing is posted online.

Generation, swaps, captions and approval use the existing Python workflow
functions. They run in one background worker; the browser polls job status,
shows progress and avoids overlapping mutations. The page does not contain an
OpenAI key. The server uses the project's .env through the existing backend.

## API adapter

`GET /api/dogs` lists dogs; `GET /api/dogs/<id>` returns the current page state.

POST a JSON object to `/api/dogs/<id>/<action>`:

| action | body | backend |
|---|---|---|
| generate | `{}` | make_options |
| choose | `{"option": 2}` | persist pending choice |
| swap | `{"option": 2, "tile": "bottom-right", "photo": "file.jpg"}` | swap_photo |
| caption | `{"text": "..."}` | save_caption |
| approve | `{"option": 2, "name": "Reviewer", "caption": "..."}` | save_caption if changed, then approve/make_video |

The response is `{"job": "id"}`. Poll `GET /api/jobs/<id>` until status is
`done` or `error`. State can then be fetched again. Photos, draft images and
approved downloads use restricted `/photos/`, `/drafts/` and `/files/` routes.

To integrate with the colleague's app, retain the HTML/CSS/JS and map these
small API routes to the same workflow functions. Static assets currently use
root-relative URLs, which can be adjusted to the hosting app's route prefix.

The dropdown uses the imported 16-dog catalog, with no photos yet / waiting for approval / approved statuses. PHOTO_ROOT in .env may point to a synced Google Drive folder. Nested photos and videos are staged read-only through local aliases; see the main README for catalog import and batch generation.
