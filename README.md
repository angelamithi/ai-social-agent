# AI Social Agent

A content agent for Afrivance.ai that:
- Finds topics two ways: automatically from RSS feeds (AI is the main focus; blockchain/crypto only as a subtopic), or from a topic you text it directly on WhatsApp, any time.
- For each topic, generates **3 genuinely distinct draft options** (different angles — a bold claim, a question, a relatable scenario) using the Claude API.
- **Stage 1:** sends you the 3 options on WhatsApp (short previews + angle labels) for you to pick one — auto-picks option 1 if you don't respond within `SELECTION_TIMEOUT_HOURS` (default 8h).
- Once a topic/draft is picked, generates one AI infographic-style image (via OpenAI's GPT Image models) for that option, stamped with an **Afrivance.ai** watermark.
- **Stage 2:** sends you the image + final post text on WhatsApp for a final Approve/Reject — this stage has **no timeout**, it waits indefinitely.
- On approval: posts to X (with image) and sends you the LinkedIn/Facebook drafts as a WhatsApp text message.
- On rejection: discards the post, nothing goes out anywhere.
- Runs automated safety checks on every option (blocklist, no financial advice, no links in X posts, dedup, rate limit) before it's ever shown to you.
- Logs every run and decision; tracks post history and dedup in Postgres.

**Nothing posts without you tapping Approve at stage 2.** Stage 1 (picking which topic/angle) has an 8-hour auto-pick fallback by design, since it's just choosing a starting point, not publishing anything — but the final stage-2 post approval always waits indefinitely with no fallback that publishes without you.

## Architecture

This runs as **three pieces on Render**, defined together in `render.yaml`:

1. **`agent-daily-run`** (Render Cron Job) — runs `main.py` once a day. Handles **Flow A only**: picks an RSS-sourced topic, generates 3 draft options, runs safety checks on each, and sends the stage-1 selection request to WhatsApp. Exits immediately after sending. Does **not** generate any image — that happens later, in the web service, after a pick is made.
2. **`agent-webhook`** (Render Web Service, FastAPI) — runs `webhook_server.py`, always-on. This is where most of the real work happens now:
   - Handles **Flow B**: any inbound WhatsApp text message (when nothing's pending) is treated as a new topic — generates 3 options and sends the same stage-1 selection request.
   - Handles your **stage-1 pick** (Option 1/2/3 button tap): generates the image for your chosen option, then sends the stage-2 final-approval request.
   - Handles your **stage-2 decision** (Approve/Reject): posts to X and sends LinkedIn/Facebook drafts, or discards.
   - Runs a background scheduler (checks every 15 minutes) that auto-picks option 1 if a stage-1 selection has been pending longer than `SELECTION_TIMEOUT_HOURS`.
3. **`agent-db`** (Render Postgres) — shared database both services read/write. This exists because the cron job and the web service run in **separate containers with separate filesystems** on Render — they can't share local files, so all state (post history, pending selections, pending approvals, and generated image bytes) lives in Postgres instead of on disk.

```
Cron job (main.py) — Flow A only       Always-on web service (webhook_server.py)
        │                                          │
        ├─ find RSS topic                          ├─ Flow B: handle inbound text
        ├─ generate 3 draft options                │    → generate 3 draft options
        ├─ run safety checks per option             │    → send stage-1 request
        ├─ save to pending_selection ───┐           │
        ├─ send stage-1 WhatsApp req    │           ├─ handle stage-1 pick (button or timeout)
        └─ exit                        │           │    → generate image for chosen option
                                        │           │    → save to pending_approval
                                        ▼           │    → send stage-2 WhatsApp req
                                  agent-db (Postgres)│
                                  - history          ├─ handle stage-2 decision (button)
                                  - pending_selection │    → post to X (if approved)
                                  - pending_approval  │    → send LinkedIn/FB drafts
                                  - images (bytes)   │
                                        ▲             ├─ background scheduler (every 15 min)
                                        └─────────────┘    → auto-pick option 1 if stage-1
                                                              has been pending > timeout
```

## 1. Setup

```bash
git clone <this repo>
cd ai-social-agent
pip install -r requirements.txt
cp .env.example .env   # for local testing only — Render deploy uses its own env var UI
```

### Getting an Anthropic API key (for text generation — required)

This is what actually writes the post drafts. `generate.py` calls the Claude API once per topic to produce the X, LinkedIn, and Facebook text in a single request — it's the core "agent" part of this agent; without it, there's no post content to review or publish.

1. Create an account and a key at [console.anthropic.com](https://console.anthropic.com) — click **Get API key** / **API Keys** in the left sidebar, then **Create Key**.
2. This is a separate product from a normal Claude.ai subscription — a Claude.ai Pro/Max plan does **not** give you API access or an API key. The API is pay-as-you-go, billed by usage (tokens in/out), not a flat subscription.
3. Add a payment method under **Billing** in the console — new accounts typically need to load a small amount of credit before the key will work (a few dollars is enough to cover a very long time at 1 post/day; this task uses a trivial number of tokens per run).
4. Copy the key (starts with `sk-ant-...`) into `ANTHROPIC_API_KEY` in `.env` (or the Render env var UI). Keys are only shown once at creation — store it somewhere safe, you'll need to generate a new one if you lose it.
5. The model used is set in `config.py` as `CLAUDE_MODEL` (currently `claude-sonnet-4-6`) — no need to touch this unless you want to swap models later.

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

## 3. The two-stage approval flow, step by step

As of the latest update, there are now **two separate approval gates**, and **two ways a topic can enter the pipeline**:

**Flow A — autonomous (RSS-sourced):**
1. The Render cron job runs `main.py` once a day.
2. It picks a candidate topic (manual queue first, then RSS) and asks Claude for **3 genuinely distinct draft options** (not just one) — varied angles, e.g. a bold claim, a question, a relatable scenario.
3. Each option's X draft goes through safety checks individually; if any of the 3 fails, that whole candidate topic is skipped and the next one is tried (you always get a clean set of exactly 3 to choose from, never a partial/blocked set).
4. The 3 surviving options are saved to `pending_selection` and sent to WhatsApp as **stage-1**: one template message showing a short preview + angle label for each option, with three buttons — **Option 1 / Option 2 / Option 3**.
5. `main.py` exits. Nothing has been generated as an image or posted yet.

**Flow B — you-initiated (text the agent a topic):**
1. Send the agent any WhatsApp message containing a topic, any time — e.g. *"How do AI agents actually decide what to do next?"*
2. **You can include a URL** (a news article, a shortener/redirect link, etc.) right in the same message — e.g. *"Tech giant Oracle cuts 21,000 jobs as it embraces AI https://share.google/abc123"*. The agent (`url_extract.py`) fetches the link, follows any redirects to the real article, and extracts the clean article text (stripping nav/ads/footers) using `trafilatura`. That real article text becomes the source material Claude writes from — not a guess based on the headline alone.
3. If extraction fails (paywall, bot-blocked page, or a shortener that uses a JavaScript-only redirect rather than a standard HTTP one — `trafilatura`/`requests` don't execute JavaScript), the agent falls back to the headline text alone and explicitly tells Claude not to invent specific facts, figures, or quotes it can't verify, rather than silently hallucinating article details.
4. As long as nothing is currently pending, `webhook_server.py` treats this as a new topic, generates 3 options the same way as Flow A, and sends you the same stage-1 selection message.
5. If something *is* already pending, your message is just logged and ignored as chatter — use the buttons to act on what's already pending first.

**Both flows converge here — stage 1 → stage 2:**
6. Whenever you tap **Option 1/2/3** — or if **`SELECTION_TIMEOUT_HOURS`** (default 8) passes with no response, in which case option 1 is auto-picked — the webhook generates one watermarked image for your chosen option only.
7. That becomes a new `pending_approval` entry, and the existing **stage-2** message goes out: the image, a caption, and **Approve**/**Reject** buttons. This stage has **no timeout** — it waits indefinitely, exactly as before.
8. **Approve** → posts to X (with the image attached) and sends you the LinkedIn/Facebook drafts as a WhatsApp message.
9. **Reject** → nothing posts anywhere. You get a confirmation that it was discarded.
10. While either stage is pending, the next cron run won't send a competing request — it skips and tries again later. (Flow B requests aren't affected by the cron schedule at all — you can text a topic any time, as long as nothing's currently pending.)

### Setting up the second WhatsApp template

Stage 1 needs its own approved template, separate from the stage-2 one:
- **Name:** `draft_options_request` (or set `WHATSAPP_OPTIONS_TEMPLATE_NAME` to whatever you use)
- **Category:** Utility
- **Language:** must match `WHATSAPP_TEMPLATE_LANGUAGE` exactly (check via the API if unsure — see the note on `en` vs `en_US` elsewhere in this file)
- **Header:** none needed
- **Body**, using exactly 3 named variables — **not** one variable per field. An earlier version with 7 separate variables (topic title + angle + preview × 3) was rejected by Meta with *"This template contains too many variables for its length. Reduce the number of variables or increase the message length"* — WhatsApp checks the ratio of variables to fixed text, so each option's angle label and preview are combined into one variable here:
  ```
  New draft options are ready for your review!

  1️⃣ {{option_1}}

  2️⃣ {{option_2}}

  3️⃣ {{option_3}}

  Tap a button below to pick the one you like best.
  ```
- **Buttons:** three Quick Reply buttons — "Option 1", "Option 2", "Option 3"

When sent, each `{{option_N}}` variable is filled with a combined string like `"Bold claim — AI agents can now pay each other in crypto without..."` (angle label + a truncated preview of the X draft, joined with an em dash) — so the fixed template text plus the variable content together still convey the full picture, just packed into 3 variables instead of 7.

If you still hit a variables-too-many error after this, the fix is the same in either direction: add more fixed/surrounding text to the body, or further reduce the variable count (e.g. drop to a single combined variable holding all 3 previews, newline-separated) — see `whatsapp_client.py`'s `send_options_request()` docstring for where to adjust if you change the template shape.

Submit and wait for approval (same process as the first template) before this flow will work end to end.

## 4. The kill switch

Set `ENABLED=false` in the cron job's environment variables (Render dashboard) to stop `main.py` (Flow A only) from generating or sending new requests. This does **not** stop Flow B (texting a topic) or stop `agent-webhook` from processing a decision on anything *already pending* — those keep working even with the kill switch on. The kill switch only stops the cron job from starting new Flow A candidates.

## 5. Tuning

All the knobs live in `config.py`:
- `RSS_SOURCES` / `TOPIC_KEYWORDS` — content sources and relevance filter
- `BLOCKLIST_KEYWORDS` / `FINANCIAL_ADVICE_PATTERNS` — safety gates
- `MAX_X_POSTS_PER_DAY`, `MAX_X_POST_LENGTH` — rate/length limits
- `DEDUP_LOOKBACK_DAYS` — how far back to check for repeat content
- `SELECTION_TIMEOUT_HOURS` — how long to wait for a stage-1 topic/draft pick before auto-choosing option 1
- `IMAGE_MODEL` / `IMAGE_QUALITY` — image generation cost/quality tradeoff
- `BRAND_NAME` — the watermark text stamped on every image (default: `Afrivance.ai`)

The cron schedule lives in `render.yaml` (`schedule:` field under `agent-daily-run`), not in `config.py` — edit and redeploy to change it.

## 6. Where state lives now

Everything that used to be local files now lives in Postgres (`agent-db`), accessed through `db.py`:
- **`history`** table — decided-post history (posted/rejected/blocked), used for dedup and rate limiting.
- **`images`** table — generated image bytes, served back out by `agent-webhook` at `/images/{image_id}` so Meta can fetch them for the WhatsApp template.
- **`pending_selection`** table — the stage-1 in-flight item: 3 draft options awaiting your pick (a one-row table; a new selection overwrites it once the previous one is resolved). Tracks `source` (`rss`/`manual`/`whatsapp`) so you can tell Flow A from Flow B in the data if needed.
- **`pending_approval`** table — the stage-2 in-flight item awaiting your final Approve/Reject (same one-row pattern).

`run_log.txt` is the only thing still written locally, and it's per-service (the cron job and the web service each have their own copy, not shared) — treat it as a debugging convenience. Render's own dashboard logs capture the same stdout output regardless and persist across deploys, so that's the more reliable place to check history of what happened.

## Known limitations / things to watch

- **Render's free web service tier will delay your approval taps.** Use the starter/paid plan for `agent-webhook` (see above) unless you're fine with occasional 30s–2min delays.
- **One selection and one approval at a time, each.** If you don't respond to a stage-1 selection, no new Flow A candidates get generated in the meantime (stage-1 auto-resolves after `SELECTION_TIMEOUT_HOURS` though, so this self-clears). If you don't respond to a stage-2 approval, that one waits indefinitely with no auto-clear — clear it manually (approve or reject) to unblock the next run.
- **Two WhatsApp templates required, not one.** `post_approval_request` (stage 2) and `draft_options_request` (stage 1) both need separate Meta approval before the full flow works end to end.
- **Images are not safety-checked the way text is.** The generation prompt steers away from logos, real faces, and chart-like imagery, but the real backstop is you, reviewing the image on WhatsApp before approving.
- **X posts contain no links, by design.** X charges substantially more per post containing a URL (~13x the plain-text rate as of 2026). `safety.py` enforces this with a backstop check even if Claude's draft includes one by mistake.
- **No retry/backoff on transient API failures.** If Claude, OpenAI, or X's API has a momentary outage, that step fails and gets logged; a failure mid-approval (e.g. X is down right when you tap Approve) gets reported back to you on WhatsApp rather than silently disappearing.
- **Free Postgres tier limits.** Render's free Postgres tier has a storage cap and (depending on current Render policy) may expire after a fixed period of inactivity or age — fine for this project's tiny data volume, but worth checking Render's current free-tier database policy if you're relying on this long-term.
- **Flow B has no topic validation beyond what Claude itself applies.** If you text something with genuinely no AI angle, Claude will say so (and you'll get a WhatsApp message explaining why) rather than silently failing.
