# AI Social Agent

A daily content agent that:
- Pulls fresh stories on AI, blockchain, crypto, and AI agents from RSS feeds, plus any topics you add manually.
- Generates platform-specific drafts (X, LinkedIn, Facebook) using the Claude API.
- Generates one AI image per topic (via OpenAI's GPT Image models), stamped with an **Afrivance.ai** watermark in the footer, reused across all three platforms.
- Runs automated safety checks (blocklist, no financial advice, no links in X posts, dedup, rate limit).
- **Sends the article + image to you on WhatsApp for manual approval** — nothing posts to any platform until you tap Approve.
- On approval: posts to X (with image) and sends you the LinkedIn/Facebook drafts as a WhatsApp text message.
- On rejection: discards the post, nothing goes out anywhere.
- Logs every run and decision; tracks post history and dedup in Postgres.

**There is no auto-post path.** Every single post — X included — waits indefinitely for your WhatsApp approval. There's no timeout and no fallback that publishes without you.

## Architecture

This runs as **three pieces on Render**, defined together in `render.yaml`:

1. **`agent-daily-run`** (Render Cron Job) — runs `main.py` once a day. Generates content + image, runs safety checks, and sends you the WhatsApp approval request. Exits immediately after sending.
2. **`agent-webhook`** (Render Web Service, FastAPI) — runs `webhook_server.py`, always-on. Listens for your button tap (Approve/Reject) and finishes the job: posts to X and sends you the LinkedIn/Facebook drafts via WhatsApp text.
3. **`agent-db`** (Render Postgres) — shared database both services read/write. This exists because the cron job and the web service run in **separate containers with separate filesystems** on Render — they can't share local files, so all state (post history, the pending approval, and even the generated image bytes) lives in Postgres instead of on disk.

```
Daily cron job (main.py)              Always-on web service (webhook_server.py)
        │                                          │
        ├─ generate text + image ──────┐           │
        ├─ run safety checks           │           │
        ├─ save to Postgres ───────────┼──► agent-db (Postgres) ◄──┤
        ├─ send WhatsApp approval req  │           │  - history          │
        └─ exit                        │           │  - pending_approval │
                                        │           │  - images (bytes)  │
                                                     ├─ receive your button tap
                                                     ├─ read pending item from DB
                                                     ├─ post to X (if approved)
                                                     └─ send LinkedIn/FB drafts via WhatsApp
```

## 1. Setup

```bash
git clone <this repo>
cd ai-social-agent
pip install -r requirements.txt
cp .env.example .env   # for local testing only — Render deploy uses its own env var UI
```

### Getting X API credentials

Apply at [developer.x.com](https://developer.x.com), create an app with **Read and write** permissions, generate Consumer Keys + Access Token/Secret *after* setting that permission level. Add billing/credits in the Developer Console — X uses pay-per-use pricing with no free tier as of 2026.

### Getting an OpenAI API key (for image generation)

Create a key at [platform.openai.com](https://platform.openai.com/api-keys). GPT Image models may require **API Organization Verification** in your developer console before they'll work. Add billing — there's no free tier for image generation, but at `gpt-image-1-mini`/low quality/1 image per day, expect well under $1/month.

### Setting up the WhatsApp Cloud API (Meta)

Budget 30–60 minutes the first time, plus however long Meta takes to approve your template (usually minutes, occasionally up to 24h).

**a) Create the Meta app and WhatsApp product**
1. Go to [developers.facebook.com](https://developers.facebook.com), create an app, add the **WhatsApp** product.
2. In the WhatsApp > API Setup page, note your **Phone Number ID** and **WhatsApp Business Account ID**.
3. Generate a permanent access token (System User token via Meta Business Settings, to avoid a token that expires in 24 hours).
4. Add your own phone number as a recipient/tester number during development. This is `APPROVER_PHONE_NUMBER` — E.164 format without the `+`, e.g. `254712345678`.

**b) Create and submit the approval template**

In WhatsApp Manager → Message Templates, create a template named exactly `post_approval_request` with:
- **Category:** Utility
- **Header:** Image
- **Body:** `New post ready for review:\n\n{{1}}\n\nApprove to post to X now, or reject to discard.`
- **Buttons:** two Quick Reply buttons — "Approve" and "Reject"

Submit it and wait for Meta's approval before testing anything else.

**c) Set up the webhook (after deploying to Render — see below)**

- Choose a random secret string yourself for `WHATSAPP_VERIFY_TOKEN`.
- In the Meta App Dashboard → WhatsApp → Configuration, set the **Callback URL** to `{your Render web service URL}/webhook` and the **Verify Token** to the same string. Subscribe to the `messages` field.

### LinkedIn / Facebook

Still not auto-posted. Once you approve on WhatsApp, the LinkedIn and Facebook draft text is sent to you as a follow-up WhatsApp message — copy/paste from there to post or schedule manually.

## 2. Deploying to Render

This repo includes a `render.yaml` Blueprint that provisions everything in one step.

1. Push this repo to GitHub (or GitLab/Bitbucket).
2. In the Render Dashboard, choose **New > Blueprint** and point it at the repo.
3. Render reads `render.yaml` and provisions:
   - `agent-db` — a Postgres database (free tier)
   - `agent-webhook` — the FastAPI web service (**starter/paid plan**, so it never sleeps — see note below on why this matters)
   - `agent-daily-run` — the cron job, scheduled daily at 08:00 UTC (edit the `schedule` field in `render.yaml` for your timezone before deploying)
4. Render will prompt you to fill in every secret marked `sync: false` in the Blueprint — this is where your API keys go (Claude, OpenAI, X, WhatsApp). They're entered once and shared between both services via the `agent-secrets` environment group.
5. After the first deploy, copy the `agent-webhook` service's `*.onrender.com` URL from the Render dashboard, and set `PUBLIC_BASE_URL` to that value (also in the Render env var UI — find `agent-webhook` and `agent-daily-run`, both need this updated since the WhatsApp template image URL is built from it).
6. Go back to the Meta App Dashboard and finish the webhook setup (callback URL = `{that URL}/webhook`) — see the WhatsApp setup section above.

### Why the web service needs the paid tier, not free

Render's free web services spin down after 15 minutes with no incoming traffic, with a 30-second-to-2-minute cold start to wake back up. Since this service only receives traffic when you tap a WhatsApp button — which could be hours after the daily request goes out — it would likely be asleep when you tap, delaying your approval from registering. The starter/paid plan (~$7/month) keeps it always-on with no delay. The cron job (`agent-daily-run`) doesn't have this problem — it runs once, does its work, and exits, so the free/starter cron pricing (~$1/month minimum) is fine there.

### Testing before going live

Locally, with `ENABLED=false` in `.env`, `main.py` exits immediately without calling any API. On Render, you can trigger the cron job manually from the dashboard ("Trigger Run") to test without waiting for the schedule.

## 3. The approval flow, step by step

1. The Render cron job runs `main.py` once a day.
2. It generates the X/LinkedIn/Facebook drafts and one watermarked image (stored in Postgres), and runs safety checks on the X draft.
3. If safety checks fail, it tries the next candidate topic — a blocked draft never reaches you for approval.
4. If a candidate passes, it's saved to the `pending_approval` table and a WhatsApp template message goes out: the image, a caption, and **Approve**/**Reject** buttons.
5. `main.py` exits. Nothing has posted anywhere yet.
6. Whenever you tap a button — five minutes later or five days later, no timeout — Meta sends a webhook event to `agent-webhook`.
7. **Approve** → posts to X (with the image attached) and sends you the LinkedIn/Facebook drafts as a WhatsApp message. You get a confirmation.
8. **Reject** → nothing posts anywhere. You get a WhatsApp confirmation that it was discarded.
9. While a request is pending, the next day's cron run won't send a second one — it skips and tries again the following day.

## 4. The kill switch

Set `ENABLED=false` in the cron job's environment variables (Render dashboard) to stop `main.py` from generating or sending new approval requests. This does **not** stop `agent-webhook` from processing a decision on an *already-sent* approval request — if something is already in your WhatsApp awaiting a decision, tapping Approve/Reject still works even with the kill switch on. The kill switch only stops new candidates from being generated and sent.

## 5. Tuning

All the knobs live in `config.py`:
- `RSS_SOURCES` / `TOPIC_KEYWORDS` — content sources and relevance filter
- `BLOCKLIST_KEYWORDS` / `FINANCIAL_ADVICE_PATTERNS` — safety gates
- `MAX_X_POSTS_PER_DAY`, `MAX_X_POST_LENGTH` — rate/length limits
- `DEDUP_LOOKBACK_DAYS` — how far back to check for repeat content
- `IMAGE_MODEL` / `IMAGE_QUALITY` — image generation cost/quality tradeoff
- `BRAND_NAME` — the watermark text stamped on every image (default: `Afrivance.ai`)

The cron schedule lives in `render.yaml` (`schedule:` field under `agent-daily-run`), not in `config.py` — edit and redeploy to change it.

## 6. Where state lives now

Everything that used to be local files now lives in Postgres (`agent-db`), accessed through `db.py`:
- **`history`** table — decided-post history (posted/rejected/blocked), used for dedup and rate limiting.
- **`images`** table — generated image bytes, served back out by `agent-webhook` at `/images/{image_id}` so Meta can fetch them for the WhatsApp template.
- **`pending_approval`** table — the single in-flight item awaiting your WhatsApp decision (a one-row table; a new candidate overwrites it once the previous one is resolved).

`run_log.txt` is the only thing still written locally, and it's per-service (the cron job and the web service each have their own copy, not shared) — treat it as a debugging convenience. Render's own dashboard logs capture the same stdout output regardless and persist across deploys, so that's the more reliable place to check history of what happened.

## Known limitations / things to watch

- **Render's free web service tier will delay your approval taps.** Use the starter/paid plan for `agent-webhook` (see above) unless you're fine with occasional 30s–2min delays.
- **One pending approval at a time.** If you don't respond for several days, no new candidates get generated in the meantime — clear the pending one (approve or reject) to resume the daily flow.
- **Template approval is a one-time but real dependency.** You cannot send the very first approval request until Meta approves the `post_approval_request` template.
- **Images are not safety-checked the way text is.** The generation prompt steers away from logos, real faces, and chart-like imagery, but the real backstop is you, reviewing the image on WhatsApp before approving.
- **X posts contain no links, by design.** X charges substantially more per post containing a URL (~13x the plain-text rate as of 2026). `safety.py` enforces this with a backstop check even if Claude's draft includes one by mistake.
- **No retry/backoff on transient API failures.** If Claude, OpenAI, or X's API has a momentary outage, that step fails and gets logged; a failure mid-approval (e.g. X is down right when you tap Approve) gets reported back to you on WhatsApp rather than silently disappearing.
- **Free Postgres tier limits.** Render's free Postgres tier has a storage cap and (depending on current Render policy) may expire after a fixed period of inactivity or age — fine for this project's tiny data volume, but worth checking Render's current free-tier database policy if you're relying on this long-term.
