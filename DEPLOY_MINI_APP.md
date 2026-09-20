NOT YOUR VIBE — Telegram Mini App Deployment

Architecture

NOT YOUR VIBE is separated into two Render services:

1. Main Bot Server

The Main Bot Server runs:

- "main.py"
- Telegram Bot API
- Telegram webhook
- Telethon
- PostgreSQL
- Radio / recommendation engine
- User feedback
- User history
- User state
- Track database
- Telegram audio delivery
- Cover image delivery
- Mini App API backend

2. Mini App Server

The Mini App Server runs:

- "mini-app/server.py"
- "mini-app/index.html"
- Static Mini App files
- "/api/*" proxy
- "/cover/*" proxy

The Mini App Server does NOT connect directly to:

- PostgreSQL
- Telethon
- Telegram Bot API

It only acts as a lightweight gateway between the Telegram Mini App and the Main Bot Server.

Telegram User
      |
      v
Telegram Mini App
mini-app/index.html
      |
      v
Mini App Server
mini-app/server.py
      |
      |  /api/*
      |  /cover/*
      v
Main Bot Server
main.py
      |
      +-------------------+
      |         |         |
      v         v         v
 PostgreSQL  Telethon  Telegram

---

Main Bot Server

Current Main Bot Server:

https://not-your-vibe-music-bot.onrender.com

The Main Bot Server continues to run:

main.py

Build Command

pip install -r requirements.txt

Start Command

python main.py

Required Environment Variables

Keep the existing production environment variables:

BOT_TOKEN
DATABASE_URL
TELETHON_API_ID
TELETHON_API_HASH
TELETHON_SESSION

Also keep the existing Render / webhook environment variables already used by the bot.

---

MINI_APP_URL

The Main Bot Server needs to know the public URL of the separate Mini App Server.

Set:

MINI_APP_URL=https://YOUR-MINI-APP-RENDER-URL

Example:

MINI_APP_URL=https://example-mini-app.onrender.com

Do NOT add "/mini-app" to this URL.

The separate Mini App Server itself is the Mini App URL.

---

Mini App Server

The Mini App Server uses:

mini-app/server.py

It serves the Mini App frontend and proxies Mini App requests to the Main Bot Server.

Build Command

pip install -r mini-app/requirements.txt

Start Command

gunicorn --chdir mini-app server:app

Required Environment Variable

Set:

BOT_SERVER=https://not-your-vibe-music-bot.onrender.com

This tells the Mini App Server where the Main Bot Server is located.

---

Mini App Server Environment

The Mini App Server only needs:

BOT_SERVER=https://not-your-vibe-music-bot.onrender.com

Do NOT copy the Main Bot Server's private environment variables to the Mini App Server.

In particular, the Mini App Server does not need:

DATABASE_URL
TELEGRAM_BOT_TOKEN
TELETHON_API_ID
TELETHON_API_HASH
TELETHON_SESSION

The Mini App Server should not access PostgreSQL or Telethon directly.

---

Mini App Frontend

The Mini App frontend is:

mini-app/index.html

The browser opens the Mini App from the Mini App Server.

The frontend should use relative API paths:

/api/...

and cover paths:

/cover/...

The Mini App Server receives those requests and forwards them to the Main Bot Server.

For example:

Mini App:

/api/me

        ↓

Mini App Server

        ↓

Main Bot Server:

https://not-your-vibe-music-bot.onrender.com/api/me

Another example:

Mini App:

/cover/123

        ↓

Mini App Server

        ↓

Main Bot Server:

https://not-your-vibe-music-bot.onrender.com/cover/123

---

API Ownership

The Main Bot Server remains the owner of the Mini App API.

Do NOT move the API/database/recommendation logic into:

mini-app/server.py

The Main Bot Server continues to handle endpoints such as:

/api/me
/api/home
/api/discover
/api/action
/api/liked
/api/track/<id>
/api/track/<id>/feedback
/api/track/<id>/audio

The exact API routes already implemented in "main.py" remain on the Main Bot Server.

The Mini App Server only proxies the requests.

---

Radio

Radio remains completely controlled by the Main Bot Server.

The Mini App Server does not contain the Radio recommendation engine.

Radio continues to use the Main Bot Server's:

- PostgreSQL data
- User feedback
- Like history
- Not-for-me history
- User state
- User listening history
- Track database
- Recommendation logic
- Telegram / Telethon delivery

This keeps the Telegram Bot and Mini App using the same recommendation data.

---

Authentication

Telegram WebApp authentication is forwarded from the Mini App Server to the Main Bot Server.

The Mini App sends:

X-Telegram-Init-Data

The Mini App Server forwards this header unchanged.

The Main Bot Server is responsible for validating Telegram's signed "initData".

Do not trust:

initDataUnsafe

for backend authentication.

Production should keep:

MINI_APP_DEV_MODE

disabled or unset.

---

Telegram Webhook

The Telegram webhook belongs ONLY to the Main Bot Server.

Use:

https://not-your-vibe-music-bot.onrender.com/webhook

The Mini App Server does NOT proxy:

/webhook

This is intentional.

Telegram updates should go directly to the Main Bot Server.

Telegram
   |
   v
Main Bot Server
   |
   v
main.py

Not:

Telegram
   |
   v
Mini App Server
   |
   v
Main Bot Server

---

Mini App URL

The Telegram Mini App should use the public URL of the separate Mini App Render service.

Example:

https://YOUR-MINI-APP-RENDER-URL

Replace "YOUR-MINI-APP-RENDER-URL" with the actual Render URL of the Mini App Server.

Do not use:

https://not-your-vibe-music-bot.onrender.com/mini-app

for the new two-server architecture.

The old "/mini-app" deployment was part of the previous single-server architecture.

---

BotFather — Menu Button

Open:

@BotFather

Then:

1. "/mybots"
2. Select NOT YOUR VIBE Music Bot
3. "Bot Settings"
4. "Menu Button"
5. "Configure menu button"
6. Button text:

🎧 NOT YOUR VIBE

7. URL:

https://YOUR-MINI-APP-RENDER-URL

Use the actual Mini App Server URL.

---

BotFather — Main Mini App

For the bot profile's Launch App button:

1. Open "@BotFather"
2. "/mybots"
3. Select the bot
4. "Bot Settings"
5. "Configure Mini App"
6. Configure the Main Mini App
7. Use the same Mini App Server HTTPS URL:

https://YOUR-MINI-APP-RENDER-URL

The Main Mini App URL and Menu Button URL should point to the separate Mini App Server.

---

/start

The bot can continue showing:

🎧 OPEN NOT YOUR VIBE

as a Telegram Web App button.

That button should open:

https://YOUR-MINI-APP-RENDER-URL

The bot itself remains on the Main Bot Server.

---

Cover Images

Cover requests are handled like this:

Telegram Mini App
      |
      v
Mini App Server
      |
      v
Main Bot Server
      |
      v
Telegram / Telethon

The Mini App Server does not download or cache Telegram media itself.

The Main Bot Server remains responsible for Telegram cover retrieval.

---

Audio Playback

Audio playback remains on the Main Bot Server.

The Mini App requests:

/api/track/<id>/audio

The request passes through:

Mini App Server
        ↓
Main Bot Server
        ↓
Telethon
        ↓
Telegram

The Mini App Server does not need a Telegram session.

This also means the Telegram / Telethon connection remains centralized on the Main Bot Server.

---

Database

PostgreSQL remains connected only to the Main Bot Server.

The Mini App Server does not run PostgreSQL queries.

The flow is:

Mini App
   ↓
Mini App Server
   ↓
Main Bot Server
   ↓
PostgreSQL

This prevents duplicate database connections and keeps the Mini App service lightweight.

---

Render Services

Service 1 — Main Bot

Repository:

notyourvibe25/not-your-vibe-music-bot

Start:

python main.py

Main URL:

https://not-your-vibe-music-bot.onrender.com

---

Service 2 — Mini App

Repository:

notyourvibe25/not-your-vibe-music-bot

Root directory:



Build:

pip install -r mini-app/requirements.txt

Start:

gunicorn --chdir mini-app server:app

Environment:

BOT_SERVER=https://not-your-vibe-music-bot.onrender.com

The Mini App service uses the same repository but runs a different entry point.

---

Important Render Configuration

For the Mini App Render service, do NOT use:

python main.py

Use:

gunicorn --chdir mini-app server:app

The Mini App service must run:

mini-app/server.py

not:

main.py

For the Main Bot Render service, continue using:

python main.py

---

Health Checks

Main Bot:

https://not-your-vibe-music-bot.onrender.com/health

Mini App:

https://YOUR-MINI-APP-RENDER-URL/health

The Mini App health response should contain:

{
  "ok": true,
  "service": "NOT YOUR VIBE Mini App Server"
}

---

Deployment Order

Deploy in this order:

1. Main Bot

Make sure the existing Main Bot Server is running normally.

Check:

https://not-your-vibe-music-bot.onrender.com/health

2. Mini App Server

Deploy the Mini App service with:

pip install -r mini-app/requirements.txt

and:

gunicorn --chdir mini-app server:app

Set:

BOT_SERVER=https://not-your-vibe-music-bot.onrender.com

3. Check Mini App

Open:

https://YOUR-MINI-APP-RENDER-URL

The Mini App should load.

4. Configure Main Bot

Set:

MINI_APP_URL=https://YOUR-MINI-APP-RENDER-URL

Restart/redeploy the Main Bot Server.

5. Telegram

Configure BotFather's:

- Menu Button
- Main Mini App

to use:

https://YOUR-MINI-APP-RENDER-URL

---

Verification Checklist

After deployment, test:

- [ ] Main Bot "/health"
- [ ] Mini App "/health"
- [ ] Mini App homepage
- [ ] Telegram "/start"
- [ ] Mini App opens inside Telegram
- [ ] Home loads
- [ ] Profile loads
- [ ] Track information loads
- [ ] Cover image loads
- [ ] Audio playback works
- [ ] Like works
- [ ] Not for me works
- [ ] Radio works
- [ ] Next works
- [ ] Daily Vibe works
- [ ] For You works
- [ ] Main Bot webhook still works
- [ ] PostgreSQL remains connected to Main Bot
- [ ] Telethon remains connected to Main Bot

---

Final Architecture

The final production structure should be:

                    TELEGRAM
                       |
             +---------+---------+
             |                   |
             v                   v
        Main Bot Server     Mini App Server
        main.py             mini-app/server.py
             |                   |
             |              /api/* /cover/*
             |                   |
             +---------<---------+
                       |
                       v
                  PostgreSQL
                       |
                    Telethon
                       |
                    Telegram

The important separation is:

MAIN BOT SERVER
    ↓
Bot + Webhook + PostgreSQL + Telethon + Radio + API

MINI APP SERVER
    ↓
Frontend + Proxy only

The Mini App Server should remain lightweight and should not duplicate the Main Bot's database, Telegram, Telethon, or recommendation logic.
