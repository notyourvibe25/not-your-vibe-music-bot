# NOT YOUR VIBE — Telegram Mini App deployment

## App URL

`https://YOUR-DOMAIN/mini-app`

## Required environment

Keep the existing production variables:

- `BOT_TOKEN`
- `DATABASE_URL`
- `RENDER_EXTERNAL_URL` (or `RENDER_EXTERNAL_HOSTNAME`)
- existing Telethon variables
- existing webhook variables

Optional:

`MINI_APP_URL=https://YOUR-DOMAIN/mini-app`

`MINI_AUDIO_CACHE_ITEMS=4` keeps recently requested Telegram audio in the
same service process so a replay or queue prefetch does not need to download
the file from Telegram again. Increase cautiously because the cache stores
audio bytes in memory.

If `MINI_APP_URL` is not set, the backend automatically uses:

`RENDER_EXTERNAL_URL + /mini-app`

## Render

Deploy the updated `main.py` and `templates/index.html` to the SAME Render Web
Service that already runs NOT YOUR VIBE.

No second service is needed.

The Flask service now serves:

- `/webhook`
- `/mini-app`
- `/api/me`
- `/api/home`
- `/api/discover`
- `/api/action`
- `/api/liked`
- `/api/track/<id>`
- `/api/track/<id>/feedback`
- `/api/track/<id>/audio`

The Mini App now presents the existing recommendations and liked tracks in a
Spotify-inspired dark/green interface. Playback uses a client-side queue,
automatically advances to the next track when a song ends, prefetches the next
track in the background, and keeps an in-process server cache for recent audio.
Like and “not for me” feedback are available directly on queue and liked-track
rows through the existing feedback endpoint.

The Mini App and bot use the same PostgreSQL tables. Mini App Likes and Not for
Me actions are written through `track_feedback`, so they are immediately
available to the bot. Mini App mood chips persist through `user_state`, so the
bot and Mini App use the same selected mood. `/api/discover` reads the bot's
existing Trending and Top 10 Liked calculations and returns them to Home.

Mini App Radio calls the bot's existing `radio_track()` engine directly. This
keeps the same feedback-weighted mood ratios, 70/20/10 fresh/liked/special
pool mix, listening-history guards, BPM continuity, harmonic-key continuity,
time-of-day context, and Trending/Top-10-Liked inputs.

The Mini App action controls also call the bot's own selection helpers through
`/api/action`: `radio`, `next`, `daily_vibe`, `for_you`, `surprise_me`, and
`track_of_day`. These actions share the bot's `user_state`, `user_history`,
special-mode memory, feedback tables, and reservation logic rather than using
an independent Mini App recommendation implementation.

## BotFather — Menu Button

Open `@BotFather`:

1. `/mybots`
2. Select your bot
3. `Bot Settings`
4. `Menu Button`
5. `Configure menu button`
6. Button text: `🎧 NOT YOUR VIBE`
7. URL: `https://YOUR-DOMAIN/mini-app`

The backend also calls Telegram's `setChatMenuButton` during startup, so the
button is configured automatically when the service starts.

## BotFather — Main Mini App / Launch App

For the bot profile's Launch App button:

1. Open `@BotFather`
2. `/mybots`
3. Select your bot
4. `Bot Settings`
5. `Configure Mini App`
6. Enable/configure the Main Mini App
7. Set the same HTTPS URL:

`https://YOUR-DOMAIN/mini-app`

This is separate from the chat Menu Button.

## /start

The existing `/start` menu now includes:

`🎧 OPEN NOT YOUR VIBE`

It is a Telegram `web_app` button, so the Mini App opens directly inside
Telegram.

## Security

The Mini App API validates Telegram's signed `initData` using `BOT_TOKEN`.

Do NOT trust `initDataUnsafe` on the backend.

Keep `MINI_APP_DEV_MODE` disabled/unset in production.

## Verification

After deployment:

1. Send `/start` to the bot.
2. Tap `🎧 OPEN NOT YOUR VIBE`.
3. Open the bot's chat menu.
4. Tap `🎧 NOT YOUR VIBE`.
5. Open `/status` and confirm `mini_app` contains the HTTPS URL.
6. Test Like / playback / Daily Vibe / For You inside Telegram.

## Telegram documentation

https://core.telegram.org/bots/webapps
https://core.telegram.org/bots/api


Playback fix: Mini App audio is authenticated with Telegram initData and fetched from the connected Telethon account before playback.

Playback fix V2: channel_id is passed to Telethon as an integer instead of a string.
This avoids username/contact entity resolution (GetContactsRequest) and the resulting FloodWait during Mini App playback.
