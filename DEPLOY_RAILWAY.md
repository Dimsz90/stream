# Deploying to Railway

This file documents the minimal steps and environment variables to deploy this project to Railway, including how to install Playwright browsers which are required for the browser-based `/api/proxy-browser` fallback.

## Required environment variables
- `PORT` - Railway sets this automatically
- `DRAMANOVA_PIN` - (optional) PIN to gate access to Dramanova UI
- `DRAMANOVA_SECRET` - HMAC secret used to sign Dramanova tokens
- `TMDB_API_KEY` - (optional) for TMDB lookups
- `OPENSUBTITLES_KEY` or `OPENSUBTITLES_API_KEY` - (optional) OpenSubtitles API key
- `REQUIRE_SUBSCRIPTION` - set to `true` to require username/PIN + active subscription for premium APIs
- `SUPABASE_URL` - Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` - server-only key used to check subscription rows
- `SUBSCRIPTION_SECRET` - server-only secret used to sign local subscription sessions
- `USE_STREAM_API_REMOTE` - set to `true` when this app should relay stream APIs to a remote worker
- `STREAM_API_REMOTE` - server-only remote worker base URL, for example `https://your-worker.up.railway.app`
- `CAPTAIN_TOKEN` or `DRACIN_TOKEN` - server-only token for Captain/Dracin upstream APIs
- `OMDB_KEYS` - (optional) comma-separated OMDb provider keys
- `SECRET_KEY` - (optional) fallback secret for other signing

## Supabase subscription table
Create a `subscriptions` table. The backend grants access when `username` + `pin` match a row with `status` of `active`, `trialing`, or `paid`, and `valid_until` is empty or still in the future.

```sql
create table public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  username text not null unique,
  pin text not null,
  status text not null default 'active',
  plan text,
  valid_until timestamptz,
  created_at timestamptz not null default now()
);

alter table public.subscriptions enable row level security;

-- Tidak perlu policy read publik. Backend membaca tabel memakai service role.
-- Jangan expose SUPABASE_SERVICE_ROLE_KEY ke browser.
```

Example subscriber valid for 30 days:

```sql
insert into public.subscriptions (username, pin, status, plan, valid_until)
values ('dimas', '1234', 'active', 'monthly', now() + interval '30 days');
```

When a user pays, update the same row:

```sql
update public.subscriptions
set status = 'active',
    plan = 'monthly',
    valid_until = now() + interval '30 days'
where username = 'dimas';
```

## Railway build steps
Railway will run `pip install -r requirements.txt` by default if you set the project to a Python service. Playwright requires an extra install step to download browser binaries.

Add the following to Railway build or post-deploy commands (Railway dashboard > Settings > Build Command / Post Deploy Command):

```bash
# install python deps
pip install -r requirements.txt
# install Playwright browsers (chromium is sufficient for the fallback)
playwright install --with-deps chromium
```

If your Railway environment does not allow `--with-deps`, try:

```bash
playwright install chromium
```

Note: using headless browsers in serverless containers may require `--no-sandbox` at launch (the server already passes `--no-sandbox`).

## Runtime command
Railway will typically run the project using `python server.py` or using `gunicorn`. Example `Start Command`:

```bash
python server.py
# or with gunicorn (for production):
# gunicorn -w 4 -b 0.0.0.0:$PORT server:app
```

## Testing proxy fallback on Railway
1. Deploy with the above build steps.
2. Premium stream APIs now return signed `/api/proxy` URLs automatically.
3. If you need to sign a raw stream URL from the browser, call `/api/proxy/sign?url=<ENCODED_URL>` with `X-Subscription-Token`.
4. If Playwright is missing on the host, `/api/proxy-browser` will return a structured error pointing to the missing dependency.

Signed proxy URLs expire after about 4 hours. When `REQUIRE_SUBSCRIPTION=true`, unsigned `/api/proxy?url=...` requests are rejected.

## Security & Costs
- Rendering pages with Playwright consumes more CPU and memory — monitor your Railway usage.
- Make sure you comply with target site Terms of Service when scraping or rendering pages.

## Troubleshooting
- If you see `playwright not installed` in responses, ensure the build step ran and the browsers were installed.
- If Chromium fails to start, confirm `--no-sandbox` usage and available container permissions.

---
Created by the project maintainer automation.
