from __future__ import annotations
import asyncio, logging, os, random, threading, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Optional
import requests
from flask import Flask, request
from psycopg2 import InterfaceError, OperationalError
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool
from telethon import TelegramClient, events
from telethon.sessions import StringSession

app=Flask(__name__)
logging.basicConfig(level=(os.getenv('LOG_LEVEL') or 'INFO').upper(),format='%(asctime)s | %(levelname)s | %(threadName)s | %(message)s')
log=logging.getLogger('nyv')

def env(n,d=''): return (os.getenv(n,d) or '').strip()
def geti(n,d,lo,hi):
    try:
        v=int(env(n,str(d)))
    except (TypeError,ValueError):
        return d
    return v if lo<=v<=hi else d

def norm_db(u): return 'postgresql://'+u[11:] if u.startswith('postgres://') else u

BOT_TOKEN=env('BOT_TOKEN'); ADMIN_USER_ID=env('ADMIN_USER_ID'); DATABASE_URL=env('DATABASE_URL')
RENDER_EXTERNAL_URL=env('RENDER_EXTERNAL_URL') or (('https://'+env('RENDER_EXTERNAL_HOSTNAME')) if env('RENDER_EXTERNAL_HOSTNAME') else '')
WEBHOOK_SECRET=env('TELEGRAM_WEBHOOK_SECRET')
API_ID=env('TELETHON_API_ID') or env('API_ID'); API_HASH=env('TELETHON_API_HASH') or env('API_HASH'); SESSION=env('TELETHON_SESSION')
HTTP_TIMEOUT=geti('TELEGRAM_HTTP_TIMEOUT',20,5,120); WORKERS=geti('MUSIC_WORKER_COUNT',4,1,12); POOL_MAX=geti('DB_POOL_MAX_CONNECTIONS',8,2,30)
SCAN_INTERVAL=geti('AUTO_SCAN_INTERVAL',300,60,3600); RECONNECT=geti('TELETHON_RECONNECT_DELAY',10,3,120); HISTORY_LIMIT=geti('RADIO_HISTORY_LIMIT',100,10,1000)
MOODS=('sad','love','chill','hype','dark','energetic','night','melodic')
INFO={
'sad':('😢 SAD',"Stay with the feeling.\nLet the music say what words can't."),
'love':('❤️ LOVE','For the moments that make your heart beats a little faster.'),
'chill':('🌙 CHILL','Slow down, breathe in,\nand let the world fade away.'),
'hype':('🔥 HYPE','Turn it up.\nYour energy starts here.'),
'dark':('🖤 DARK','Enter the darker side.\nHeavy bass. Brutal drops. No mercy.'),
'energetic':('⚡ ENERGETIC','No limits. No brakes.\nJust pure energy.'),
'night':('🚗 NIGHT DRIVE','Lights outside.\nMusic inside. Keep moving.'),
'melodic':('🌌 MELODIC','Close your eyes and let the melody take you somewhere else.')}
CHANNELS={m:env(m.upper()+'_CHANNEL') for m in MOODS}
CHANNELS['hype']=env('HYPE_CHANNEL','-1004427220481'); CHANNELS['melodic']=env('MELODIC_CHANNEL','-1004446996297')
AUDIO=('.mp3','.m4a','.flac','.wav','.aac','.ogg','.opus','.mp4','.mkv','.webm')

db_pool=None; db_lock=threading.Lock(); client=None; tele_loop=None; ready=threading.Event(); tele_thread=None; tele_lock=threading.Lock(); executor=ThreadPoolExecutor(max_workers=WORKERS,thread_name_prefix='music'); pending=set(); pending_lock=threading.Lock(); channel_map={}; last_scan=0
http_local=threading.local()

@contextmanager
def db():
    global db_pool
    if db_pool is None:
        with db_lock:
            if db_pool is None: db_pool=ThreadedConnectionPool(1,POOL_MAX,dsn=norm_db(DATABASE_URL),connect_timeout=10,application_name='not-your-vibe')
    c=None
    try:
        c=db_pool.getconn(); c.autocommit=False
        try:
            with c.cursor() as x:x.execute('SELECT 1')
        except (OperationalError,InterfaceError):
            db_pool.putconn(c,close=True); c=db_pool.getconn(); c.autocommit=False
        yield c;c.commit()
    except Exception:
        if c:
            try:c.rollback()
            except:pass
        raise
    finally:
        if c:
            try:db_pool.putconn(c)
            except (PoolError,OperationalError,InterfaceError):pass

@contextmanager
def cur(c):
    x=c.cursor(cursor_factory=RealDictCursor)
    try:yield x
    finally:x.close()

def init_db():
    schema='''CREATE TABLE IF NOT EXISTS users(user_id BIGINT PRIMARY KEY,username TEXT,first_name TEXT,last_name TEXT,first_seen BIGINT NOT NULL,last_seen BIGINT NOT NULL,total_requests BIGINT NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS tracks(id BIGSERIAL PRIMARY KEY,mood TEXT NOT NULL,channel_id TEXT NOT NULL,message_id BIGINT NOT NULL,created_at BIGINT NOT NULL,title TEXT,UNIQUE(channel_id,message_id));
    CREATE TABLE IF NOT EXISTS user_history(id BIGSERIAL PRIMARY KEY,user_id BIGINT NOT NULL,mood TEXT NOT NULL,channel_id TEXT NOT NULL,message_id BIGINT NOT NULL,action TEXT NOT NULL DEFAULT 'served',sent_at BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS user_state(user_id BIGINT PRIMARY KEY,mood TEXT,radio_enabled BOOLEAN NOT NULL DEFAULT FALSE,updated_at BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS track_feedback(id BIGSERIAL PRIMARY KEY,user_id BIGINT NOT NULL,channel_id TEXT NOT NULL,message_id BIGINT NOT NULL,mood TEXT NOT NULL,feedback TEXT NOT NULL,created_at BIGINT NOT NULL,UNIQUE(user_id,channel_id,message_id));
    CREATE TABLE IF NOT EXISTS daily_activity(user_id BIGINT NOT NULL,day DATE NOT NULL,PRIMARY KEY(user_id,day));
    CREATE TABLE IF NOT EXISTS broadcasts(id BIGSERIAL PRIMARY KEY,admin_id BIGINT NOT NULL,text TEXT NOT NULL,created_at BIGINT NOT NULL,sent_count BIGINT NOT NULL DEFAULT 0,failed_count BIGINT NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS broadcast_reactions(id BIGSERIAL PRIMARY KEY,broadcast_id BIGINT NOT NULL,user_id BIGINT NOT NULL,reaction TEXT NOT NULL,created_at BIGINT NOT NULL,UNIQUE(broadcast_id,user_id));
    CREATE TABLE IF NOT EXISTS broadcast_comments(id BIGSERIAL PRIMARY KEY,broadcast_id BIGINT NOT NULL,user_id BIGINT NOT NULL,comment TEXT NOT NULL,created_at BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS pending_comments(user_id BIGINT PRIMARY KEY,broadcast_id BIGINT NOT NULL,created_at BIGINT NOT NULL);
    CREATE TABLE IF NOT EXISTS pending_broadcasts(user_id BIGINT PRIMARY KEY,chat_id BIGINT NOT NULL,created_at BIGINT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(mood); CREATE INDEX IF NOT EXISTS idx_hist_user ON user_history(user_id,sent_at DESC); CREATE INDEX IF NOT EXISTS idx_hist_track ON user_history(user_id,channel_id,message_id,sent_at DESC); CREATE INDEX IF NOT EXISTS idx_fb_user ON track_feedback(user_id); CREATE INDEX IF NOT EXISTS idx_fb_track_feedback ON track_feedback(channel_id,message_id,feedback); CREATE INDEX IF NOT EXISTS idx_daily_day ON daily_activity(day); CREATE INDEX IF NOT EXISTS idx_bc_broadcast ON broadcast_comments(broadcast_id);
    ALTER TABLE tracks ADD COLUMN IF NOT EXISTS title TEXT;
    ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS source_chat_id BIGINT;
    ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS source_message_id BIGINT;
    ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS content_type TEXT;
    ALTER TABLE broadcasts ALTER COLUMN text DROP NOT NULL;'''
    with db() as c:
        with cur(c) as x:x.execute(schema)

def register(u):
    uid=u.get('id');
    if not isinstance(uid,int):return
    now=int(time.time())
    with db() as c:
        with cur(c) as x:
            x.execute('''INSERT INTO users(user_id,username,first_name,last_name,first_seen,last_seen,total_requests) VALUES(%s,%s,%s,%s,%s,%s,1) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name,last_seen=EXCLUDED.last_seen,total_requests=users.total_requests+1''',(uid,u.get('username'),u.get('first_name'),u.get('last_name'),now,now))
            day=datetime.now(ZoneInfo('Asia/Yangon')).date()
            x.execute('INSERT INTO daily_activity(user_id,day) VALUES(%s,%s) ON CONFLICT DO NOTHING',(uid,day))

def set_mood(uid,mood):
    if mood not in MOODS:return False
    with db() as c:
        with cur(c) as x:x.execute('''INSERT INTO user_state(user_id,mood,radio_enabled,updated_at) VALUES(%s,%s,FALSE,%s) ON CONFLICT(user_id) DO UPDATE SET mood=EXCLUDED.mood,radio_enabled=FALSE,updated_at=EXCLUDED.updated_at''',(uid,mood,int(time.time())))
    return True

def get_mood(uid):
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT mood FROM user_state WHERE user_id=%s',(uid,));r=x.fetchone();return r['mood'] if r and r['mood'] in MOODS else None

def set_radio(uid,on=True):
    with db() as c:
        with cur(c) as x:x.execute('''INSERT INTO user_state(user_id,mood,radio_enabled,updated_at) VALUES(%s,NULL,%s,%s) ON CONFLICT(user_id) DO UPDATE SET radio_enabled=EXCLUDED.radio_enabled,updated_at=EXCLUDED.updated_at''',(uid,on,int(time.time())))

def save_track(mood,ch,msg,title=None):
    if mood not in MOODS or not ch or not msg:return False
    with db() as c:
        with cur(c) as x:
            x.execute('''INSERT INTO tracks(mood,channel_id,message_id,created_at,title) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(channel_id,message_id) DO UPDATE SET mood=EXCLUDED.mood,title=COALESCE(EXCLUDED.title,tracks.title) RETURNING id''',(mood,str(ch),int(msg),int(time.time()),title))
            return x.fetchone() is not None

def counts():
    r={m:0 for m in MOODS}
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT mood,COUNT(*) count FROM tracks GROUP BY mood')
            for a in x.fetchall():
                if a['mood'] in r:r[a['mood']]=int(a['count'])
    return r

def feedback_map(uid):
    r={}
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT channel_id,message_id,feedback FROM track_feedback WHERE user_id=%s',(uid,))
            for a in x.fetchall():r[(str(a['channel_id']),int(a['message_id']))]=a['feedback']
    return r

def feedback(uid,ch,msg):
    with db() as c:
        with cur(c) as x:x.execute('SELECT feedback FROM track_feedback WHERE user_id=%s AND channel_id=%s AND message_id=%s',(uid,str(ch),int(msg)));r=x.fetchone();return r['feedback'] if r else None

def save_feedback(uid,ch,msg,mood,fb):
    if fb not in ('like','not_for_me') or mood not in MOODS:
        return False
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT 1 FROM tracks WHERE channel_id=%s AND message_id=%s',(str(ch),int(msg)))
            if not x.fetchone():
                return False
            x.execute('''INSERT INTO track_feedback(user_id,channel_id,message_id,mood,feedback,created_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id,channel_id,message_id) DO UPDATE SET mood=EXCLUDED.mood,feedback=EXCLUDED.feedback,created_at=EXCLUDED.created_at''',(uid,str(ch),int(msg),mood,fb,int(time.time())))
            return True

def clear_feedback(uid,ch,msg):
    with db() as c:
        with cur(c) as x:x.execute('DELETE FROM track_feedback WHERE user_id=%s AND channel_id=%s AND message_id=%s',(uid,str(ch),int(msg)))

def history(uid):
    r=set()
    with db() as c:
        with cur(c) as x:
            x.execute("SELECT channel_id,message_id FROM user_history WHERE user_id=%s AND action='served' ORDER BY sent_at DESC,id DESC LIMIT %s",(uid,HISTORY_LIMIT))
            for a in x.fetchall():r.add((str(a['channel_id']),int(a['message_id'])))
    return r

def record(uid,mood,ch,msg):
    with db() as c:
        with cur(c) as x:x.execute("INSERT INTO user_history(user_id,mood,channel_id,message_id,action,sent_at) VALUES(%s,%s,%s,%s,'served',%s)",(uid,mood,str(ch),int(msg),int(time.time())))

def candidates(mood,limit=250):
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT message_id,channel_id FROM tracks WHERE mood=%s ORDER BY RANDOM() LIMIT %s',(mood,limit));return [(int(a['message_id']),str(a['channel_id'])) for a in x.fetchall()]

def ratios(uid):
    out={m:{'like':0,'not':0} for m in MOODS}
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT mood,feedback,COUNT(*) count FROM track_feedback WHERE user_id=%s GROUP BY mood,feedback',(uid,))
            for a in x.fetchall():
                if a['mood'] in out:
                    out[a['mood']]['like' if a['feedback']=='like' else 'not']=int(a['count'])
    return out

def radio_weights(uid):
    r=ratios(uid); w={}
    for m in MOODS:
        total=r[m]['like']+r[m]['not']
        # Bayesian smoothing: unseen moods remain available but do not beat a strong favorite.
        ratio=(r[m]['like']+1)/(total+2)
        volume=1+min(r[m]['like'],20)*0.35
        w[m]=max(0.05,ratio*volume)
    return w

def radio_track(uid):
    w=radio_weights(uid); avail=[m for m,c in counts().items() if c>0]
    if not avail:return None
    fm=feedback_map(uid); hist=history(uid)
    # First radio track: strongest like-ratio mood, not current selected mood.
    ranked=sorted(avail,key=lambda m:w[m],reverse=True)
    top=ranked[0]
    # With actual likes, start deterministically from the best mood; otherwise weighted random.
    if sum(ratios(uid)[m]['like'] for m in MOODS)>0: chosen=top
    else: chosen=random.choice(avail)
    cs=candidates(chosen); allowed=[t for t in cs if fm.get((t[1],t[0]))!='not_for_me']
    unseen=[t for t in allowed if (t[1],t[0]) not in hist]
    if allowed:
        t=random.choice(unseen or allowed)
        return chosen,t[0],t[1]
    # fallback other moods
    for m in ranked[1:]:
        cs=candidates(m);allowed=[t for t in cs if fm.get((t[1],t[0]))!='not_for_me'];unseen=[t for t in allowed if (t[1],t[0]) not in hist]
        if allowed:
            t=random.choice(unseen or allowed);return m,t[0],t[1]
    return None

def normal_track(uid,mood):
    fm=feedback_map(uid);h=history(uid);a=[t for t in candidates(mood) if fm.get((t[1],t[0]))!='not_for_me'];u=[t for t in a if (t[1],t[0]) not in h]
    if not (u or a):return None
    t=random.choice(u or a);return mood,t[0],t[1]

def reserve(uid,ch):
    if not ch:return None
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT pg_advisory_xact_lock(%s)',(uid,));x.execute("INSERT INTO user_history(user_id,mood,channel_id,message_id,action,sent_at) VALUES(%s,%s,%s,%s,'served',%s)",(uid,ch[0],ch[2],ch[1],int(time.time())))
    return ch

def session():
    s=getattr(http_local,'s',None)
    if not s:s=requests.Session();http_local.s=s
    return s

def tg(method,data=None,timeout=20):
    try:
        r=session().post(f'https://api.telegram.org/bot{BOT_TOKEN}/{method}',json=data or {},timeout=timeout)
        try: payload=r.json()
        except ValueError: payload={'ok':False,'description':f'HTTP {r.status_code}: non-JSON response'}
        if r.status_code >= 400 and payload.get('ok',True): payload={'ok':False,'description':f'HTTP {r.status_code}'}
        return payload
    except requests.RequestException as e:
        log.warning('Telegram %s: %s',method,e); return {'ok':False,'description':str(e)}
    except Exception as e:
        log.exception('Telegram %s unexpected error',method); return {'ok':False,'description':str(e)}

def send(chat,text,k=None):
    d={'chat_id':chat,'text':text,'disable_web_page_preview':True}
    if k:d['reply_markup']=k
    return tg('sendMessage',d,15)
def answer(cid,text=''):tg('answerCallbackQuery',{'callback_query_id':cid,'text':text},8)
def edit_k(chat,msg,k):tg('editMessageReplyMarkup',{'chat_id':chat,'message_id':msg,'reply_markup':k},10)
def copy_music(chat,ch,msg):return tg('copyMessage',{'chat_id':chat,'from_chat_id':ch,'message_id':msg},30)

def broadcast_buttons(bid):
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT reaction,COUNT(*) count FROM broadcast_reactions WHERE broadcast_id=%s GROUP BY reaction',(bid,));r={a['reaction']:int(a['count']) for a in x.fetchall()}
    return {'inline_keyboard':[[{'text':f"❤️ {r.get('love',0)}",'callback_data':f'br:{bid}:love'},{'text':f"🔥 {r.get('fire',0)}",'callback_data':f'br:{bid}:fire'},{'text':f"👍 {r.get('like',0)}",'callback_data':f'br:{bid}:like'}],[{'text':'💬 COMMENT','callback_data':f'bc:{bid}'}]]}

def cleanup_pending():
    cutoff=int(time.time())-900
    with db() as c:
        with cur(c) as x:
            x.execute('DELETE FROM pending_comments WHERE created_at < %s',(cutoff,))
            x.execute('DELETE FROM pending_broadcasts WHERE created_at < %s',(cutoff,))

def set_pending_broadcast(uid,chat_id):
    with db() as c:
        with cur(c) as x:x.execute('INSERT INTO pending_broadcasts(user_id,chat_id,created_at) VALUES(%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET chat_id=EXCLUDED.chat_id,created_at=EXCLUDED.created_at',(uid,chat_id,int(time.time())))

def get_pending_broadcast(uid):
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT chat_id FROM pending_broadcasts WHERE user_id=%s',(uid,));r=x.fetchone();return int(r['chat_id']) if r else None

def clear_pending_broadcast(uid):
    with db() as c:
        with cur(c) as x:x.execute('DELETE FROM pending_broadcasts WHERE user_id=%s',(uid,))

def create_broadcast(admin_id,source_chat_id,source_message_id,content_type,text=None):
    with db() as c:
        with cur(c) as x:
            x.execute('INSERT INTO broadcasts(admin_id,text,source_chat_id,source_message_id,content_type,created_at) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id',(admin_id,text,source_chat_id,source_message_id,content_type,int(time.time())));return int(x.fetchone()['id'])

def broadcast_job(bid,source_chat_id,source_message_id):
    with db() as c:
        with cur(c) as x:x.execute('SELECT user_id FROM users ORDER BY user_id');users=[int(a['user_id']) for a in x.fetchall()]
    k=broadcast_buttons(bid);okn=0;bad=0
    for uid in users:
        r=tg('copyMessage',{'chat_id':uid,'from_chat_id':source_chat_id,'message_id':source_message_id,'reply_markup':k},30)
        if r.get('ok'):okn+=1
        else:bad+=1
        time.sleep(0.04)
    with db() as c:
        with cur(c) as x:x.execute('UPDATE broadcasts SET sent_count=%s,failed_count=%s WHERE id=%s',(okn,bad,bid))
    send(ADMIN_USER_ID,f'📣 BROADCAST #{bid} COMPLETE\n\n✅ Sent: {okn}\n❌ Failed: {bad}')

def start_broadcast(admin_id,source_chat_id,source_message_id,content_type,text=None):
    bid=create_broadcast(admin_id,source_chat_id,source_message_id,content_type,text);executor.submit(broadcast_job,bid,source_chat_id,source_message_id);return bid

def set_pending_comment(uid,bid):
    with db() as c:
        with cur(c) as x:x.execute('INSERT INTO pending_comments(user_id,broadcast_id,created_at) VALUES(%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET broadcast_id=EXCLUDED.broadcast_id,created_at=EXCLUDED.created_at',(uid,bid,int(time.time())))

def get_pending_comment(uid):
    with db() as c:
        with cur(c) as x:x.execute('SELECT broadcast_id FROM pending_comments WHERE user_id=%s',(uid,));r=x.fetchone();return int(r['broadcast_id']) if r else None

def save_comment(uid,bid,text):
    with db() as c:
        with cur(c) as x:x.execute('INSERT INTO broadcast_comments(broadcast_id,user_id,comment,created_at) VALUES(%s,%s,%s,%s)',(bid,uid,text,int(time.time())));x.execute('DELETE FROM pending_comments WHERE user_id=%s',(uid,))

def top_liked_tracks(limit=10):
    with db() as c:
        with cur(c) as x:
            x.execute("""SELECT t.mood,t.channel_id,t.message_id,
                         COALESCE(NULLIF(t.title,''),'Track #'||t.message_id::text) AS title,
                         COUNT(f.id) AS likes
                         FROM tracks t JOIN track_feedback f
                         ON f.channel_id=t.channel_id AND f.message_id=t.message_id AND f.feedback='like'
                         GROUP BY t.id,t.mood,t.channel_id,t.message_id,t.title
                         ORDER BY likes DESC,t.created_at DESC LIMIT %s""",(limit,));return x.fetchall()

async def backfill_track_titles_async(rows):
    for row in rows:
        if row.get('title'):
            continue
        try:
            ent=await client.get_entity(int(row['channel_id']))
            msg=await client.get_messages(ent,ids=int(row['message_id']))
            if not msg:
                continue
            title=message_title(msg)
            if not title:
                continue
            with db() as c:
                with cur(c) as x:
                    x.execute('UPDATE tracks SET title=%s WHERE channel_id=%s AND message_id=%s',(title,str(row['channel_id']),int(row['message_id'])))
            row['title']=title
        except Exception:
            log.exception('backfill track title channel=%s message=%s',row.get('channel_id'),row.get('message_id'))

def backfill_track_titles(rows):
    if not rows or client is None or tele_loop is None or not ready.is_set():
        return rows
    try:
        fut=asyncio.run_coroutine_threadsafe(backfill_track_titles_async(rows),tele_loop)
        fut.result(timeout=45)
    except Exception:
        log.exception('top liked title backfill')
    return rows

def top_liked_text():
    rows=backfill_track_titles(top_liked_tracks(10))
    lines=['🏆 TOP 10 MOST LIKED TRACKS','━━━━━━━━━━━━━━━━━━','']
    if not rows:
        lines.append('No likes yet. Start liking tracks ❤️')
        return '\n'.join(lines)
    for i,a in enumerate(rows,1):
        title=str(a['title']).replace('\n',' ')[:90]
        lines.append(f'{i}. 🎵 {title}')
        lines.append(f'   {INFO[a["mood"]][0]} • ❤️ {int(a["likes"])} likes')
    return '\n'.join(lines)

def daily_stats():
    today=datetime.now(ZoneInfo('Asia/Yangon')).date();days=[today-timedelta(days=i) for i in range(7)]
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT COUNT(*) n FROM users');total=int(x.fetchone()['n'])
            x.execute('SELECT COUNT(*) n FROM daily_activity WHERE day=%s',(today,));today_n=int(x.fetchone()['n'])
            x.execute('SELECT COUNT(DISTINCT user_id) n FROM daily_activity WHERE day >= %s',(today-timedelta(days=6),));week_n=int(x.fetchone()['n'])
            x.execute('SELECT day,COUNT(*) n FROM daily_activity WHERE day >= %s GROUP BY day ORDER BY day DESC',(today-timedelta(days=6),));rows={a['day']:int(a['n']) for a in x.fetchall()}
    return total,today_n,week_n,[(d,rows.get(d,0)) for d in days]

def comments_text(limit=20):
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT c.id,c.broadcast_id,c.user_id,c.comment,c.created_at FROM broadcast_comments c ORDER BY c.id DESC LIMIT %s',(limit,));rows=x.fetchall()
    if not rows:return '💬 COMMENTS\n\nNo comments yet.'
    lines=['💬 LATEST COMMENTS','━━━━━━━━━━━━━━━━━━','']
    for a in rows:
        txt=(a['comment'] or '').replace('\n',' ')[:180]
        lines.append(f"Comment #{a['id']} • Broadcast #{a['broadcast_id']} • User {a['user_id']}\n{txt}\n")
    return '\n'.join(lines)

def admin_panel():
    return {'inline_keyboard':[
        [{'text':'📊 DAILY USERS','callback_data':'admin:daily'},
         {'text':'📈 STATS','callback_data':'admin:stats'}],
        [{'text':'💬 COMMENTS','callback_data':'admin:comments'},
         {'text':'📣 BROADCAST','callback_data':'admin:broadcast'}],
        [{'text':'🏆 TOP 10 LIKED','callback_data':'admin:top'}],
        [{'text':'📡 TELETHON','callback_data':'admin:telegram'}]
    ]}

def admin_dashboard():
    total,today_n,week_n,rows=daily_stats()
    track_counts=counts();track_total=sum(track_counts.values())
    recent_24h=0
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT COUNT(*) n FROM broadcasts WHERE created_at >= %s',(int(time.time())-86400,))
            recent_24h=int(x.fetchone()['n'])
    return (
        '🛠 NOT YOUR VIBE — ADMIN\n'
        '━━━━━━━━━━━━━━━━━━\n\n'
        f'👥 Total Users: {total}\n'
        f'🟢 Today Active: {today_n}\n'
        f'📅 7-Day Active: {week_n}\n'
        f'🎵 Tracks: {track_total}\n'
        f'📣 Broadcasts (24h): {recent_24h}\n\n'
        f'📡 Telethon: {"CONNECTED" if ready.is_set() else "DISCONNECTED"}\n'
        f'🗄 PostgreSQL: {"ONLINE" if db_pool else "OFFLINE"}'
    )

def mood_menu():
    return {'inline_keyboard':[
        [{'text':INFO['sad'][0],'callback_data':'mood_sad'},{'text':INFO['love'][0],'callback_data':'mood_love'}],
        [{'text':INFO['chill'][0],'callback_data':'mood_chill'},{'text':INFO['hype'][0],'callback_data':'mood_hype'}],
        [{'text':INFO['dark'][0],'callback_data':'mood_dark'},{'text':INFO['energetic'][0],'callback_data':'mood_energetic'}],
        [{'text':INFO['night'][0],'callback_data':'mood_night'},{'text':INFO['melodic'][0],'callback_data':'mood_melodic'}],
        [{'text':'🔥 DAILY VIBE','callback_data':'daily_vibe'},{'text':'🧠 FOR YOU','callback_data':'for_you'}],
        [{'text':'🎲 SURPRISE ME','callback_data':'surprise_me'},{'text':'📈 TRENDING','callback_data':'trending'}],
        [{'text':'🎵 TRACK OF THE DAY','callback_data':'track_of_day'}],
        [{'text':'🏆 TOP 10 LIKED','callback_data':'top_liked'}]
    ]}

def eligible_tracks(uid, extra_where='', params=()):
    hist=history(uid)
    with db() as c:
        with cur(c) as x:
            q="""SELECT mood,message_id,channel_id,title FROM tracks
                   WHERE NOT EXISTS (SELECT 1 FROM track_feedback f
                     WHERE f.user_id=%s AND f.channel_id=tracks.channel_id
                     AND f.message_id=tracks.message_id AND f.feedback='not_for_me')"""
            args=[uid]
            if extra_where:
                q += ' AND ' + extra_where; args.extend(params)
            x.execute(q,args); rows=x.fetchall()
    unseen=[r for r in rows if (str(r['channel_id']),int(r['message_id'])) not in hist]
    return unseen or rows

def stable_pick(rows, key):
    if not rows:return None
    import hashlib
    idx=int(hashlib.md5(key.encode()).hexdigest()[:8],16)%len(rows)
    r=rows[idx]
    return r['mood'],int(r['message_id']),str(r['channel_id']),r.get('title')

def daily_vibe_track(uid):
    rows=eligible_tracks(uid)
    today=datetime.now(ZoneInfo('Asia/Yangon')).date().isoformat()
    return stable_pick(rows,f'daily:{uid}:{today}')

def track_of_day(uid):
    rows=eligible_tracks(uid)
    today=datetime.now(ZoneInfo('Asia/Yangon')).date().isoformat()
    return stable_pick(rows,f'today:{uid}:{today}')

def for_you_track(uid):
    weights=radio_weights(uid); rows=eligible_tracks(uid)
    if not rows:return None
    r=random.choices(rows,weights=[max(0.05,float(weights.get(z['mood'],0.05))) for z in rows],k=1)[0]
    return r['mood'],int(r['message_id']),str(r['channel_id']),r.get('title')

def surprise_track(uid):
    r=ratios(uid)
    mood_scores=sorted([((r[m]['not']+1)/(r[m]['like']+r[m]['not']+2),m) for m in MOODS], reverse=True)
    for _,m in mood_scores:
        rows=eligible_tracks(uid,'mood=%s',(m,))
        if rows:
            z=random.choice(rows); return z['mood'],int(z['message_id']),str(z['channel_id']),z.get('title')
    return None

def trending_rows(limit=10):
    with db() as c:
        with cur(c) as x:
            x.execute("""SELECT t.id,t.mood,t.channel_id,t.message_id,
                       COALESCE(NULLIF(t.title,''),'Track #'||t.message_id::text) AS title,
                       COUNT(f.id) AS likes FROM tracks t JOIN track_feedback f
                       ON f.channel_id=t.channel_id AND f.message_id=t.message_id AND f.feedback='like'
                       GROUP BY t.id,t.mood,t.channel_id,t.message_id,t.title
                       ORDER BY likes DESC,t.created_at DESC LIMIT %s""",(limit,)); return x.fetchall()

def trending_text(limit=10):
    rows=trending_rows(limit)
    if not rows:return '📈 TRENDING NOW\n━━━━━━━━━━━━━━━━━━\n\nNo liked tracks yet. Start liking tracks ❤️'
    lines=['📈 TRENDING NOW','━━━━━━━━━━━━━━━━━━','']
    for i,r in enumerate(rows,1):
        title=str(r['title']).replace('\n',' ')[:90]
        lines.append(f"{i}. 🎵 {title}")
        lines.append(f"   {INFO[r['mood']][0]} • ❤️ {int(r['likes'])}")
    return '\n'.join(lines)

def taste_analytics(uid):
    r=ratios(uid); total_like=sum(v['like'] for v in r.values()); total_not=sum(v['not'] for v in r.values())
    ranked=sorted(MOODS,key=lambda m:(r[m]['like'],r[m]['like']-r[m]['not']),reverse=True)
    lines=['📊 TASTE ANALYTICS','━━━━━━━━━━━━━━━━━━','',f'❤️ Likes: {total_like}    😴 Not for me: {total_not}']
    if total_like+total_not:
        lines.append(f'🎯 Positive ratio: {total_like/(total_like+total_not)*100:.0f}%')
    lines.append('')
    for m in ranked:
        l,n=r[m]['like'],r[m]['not']
        if l+n:lines.append(f'{INFO[m][0]} → ❤️ {l} / 😴 {n}')
    if not total_like+total_not:lines.append('Like or skip tracks to build your taste profile.')
    return '\n'.join(lines)

def special_buttons(uid,ch,msg,mood):
    return {'inline_keyboard':[[{'text':'❤️','callback_data':f'like:{mood}:{ch}:{msg}'},{'text':'😴','callback_data':f'notme:{mood}:{ch}:{msg}'}],[{'text':'⏭ NEXT','callback_data':'next_music'},{'text':'📻 RADIO','callback_data':'radio'}],[{'text':'🧠 FOR YOU','callback_data':'for_you'},{'text':'🎛 CHANGE MOOD','callback_data':'change_mood'}],[{'text':'👤 PROFILE','callback_data':'profile'}]]}

def send_special_music(chat,uid,track,header):
    if not track:return send(chat,'⚠️ No suitable track found.',mood_menu())
    mood,msg,ch,title=track
    r=copy_music(chat,ch,msg)
    if not r.get('ok'):return send(chat,'⚠️ This track could not be delivered.',mood_menu())
    if not reserve(uid,(mood,msg,ch)):log.warning('History record failed uid=%s channel=%s message=%s',uid,ch,msg)
    label=(title or f'Track #{msg}').replace('\n',' ')[:120]
    send(chat,f'{header}\n━━━━━━━━━━━━━━━━━━\n\n🎵 {label}\n{INFO[mood][0]}\n\nEnjoy the vibe. ✨',special_buttons(uid,ch,msg,mood))

def buttons(uid,ch,msg,mood):
    f=feedback(uid,ch,msg);return {'inline_keyboard':[[{'text':'❤️✓' if f=='like' else '❤️','callback_data':f'like:{mood}:{ch}:{msg}'},{'text':'😴✓' if f=='not_for_me' else '😴','callback_data':f'notme:{mood}:{ch}:{msg}'}],[{'text':'⏭ NEXT','callback_data':'next_music'},{'text':'📻 RADIO','callback_data':'radio'}],[{'text':'👤 PROFILE','callback_data':'profile'},{'text':'🆕 NEW TRACKS','callback_data':'new_tracks'}],[{'text':'🎛 CHANGE MOOD','callback_data':'change_mood'}]]}

def send_music(chat,uid,mood,radio=False):
    track=radio_track(uid) if radio else normal_track(uid,mood)
    if not track:return send(chat,'⚠️ No suitable track found.',mood_menu())
    sm,msg,channel=track
    r=copy_music(chat,channel,msg)
    if not r.get('ok'):return send(chat,'⚠️ This track could not be delivered.',mood_menu())
    if not reserve(uid,track):log.warning('History record failed uid=%s channel=%s message=%s',uid,channel,msg)
    title='📻 YOUR RADIO' if radio else '🎧 NOW PLAYING'; desc='Personalized by your Like ratio across all moods.' if radio else INFO[sm][1]
    send(chat,f'{title}\n━━━━━━━━━━━━━━━━━━\n\n{INFO[sm][0]}\n\n{desc}\n\nEnjoy the vibe. ✨',buttons(uid,channel,msg,sm))

def schedule(chat,uid,mood,radio=False):
    with pending_lock:
        if uid in pending:return False
        pending.add(uid)
    def work():
        try:send_music(chat,uid,mood,radio)
        except Exception:log.exception('music worker')
        finally:
            with pending_lock:pending.discard(uid)
    executor.submit(work);return True

def profile_text(uid):
    with db() as c:
        with cur(c) as x:
            x.execute('SELECT username,first_name,last_name,total_requests FROM users WHERE user_id=%s',(uid,));u=x.fetchone() or {}
            x.execute("SELECT COUNT(*) n FROM track_feedback WHERE user_id=%s AND feedback='like'",(uid,));likes=int(x.fetchone()['n'])
            x.execute("SELECT COUNT(*) n FROM track_feedback WHERE user_id=%s AND feedback='not_for_me'",(uid,));nots=int(x.fetchone()['n'])
            x.execute("SELECT COUNT(*) n FROM user_history WHERE user_id=%s AND action='served'",(uid,));served=int(x.fetchone()['n'])
    r=ratios(uid); ranked=sorted(MOODS,key=lambda m:(r[m]['like'],r[m]['like']-r[m]['not']),reverse=True)
    fav,second=ranked[0],ranked[1]
    name=' '.join(x for x in [u.get('first_name') or '',u.get('last_name') or ''] if x).strip() or 'Vibe Listener'
    username=f"@{u.get('username')}" if u.get('username') else 'Not set'
    mood=get_mood(uid)
    return (f"👤 VIBE PROFILE 2.0\n━━━━━━━━━━━━━━━━━━\n\nName: {name}\nUsername: {username}\nCurrent mood: {INFO[mood][0] if mood else 'Not selected'}\n\n"
            f"🎵 Tracks played: {served}\n❤️ Likes: {likes}\n😴 Not for me: {nots}\n\n"
            f"🏆 Top mood: {INFO[fav][0]}\n🥈 Second: {INFO[second][0]}\n\n"
            'Your Radio learns from your feedback across all moods.')

def new_tracks(chat):
    lines=['🆕 NEW TRACKS','━━━━━━━━━━━━━━━━━━','', 'Latest 5 tracks from each mood channel:','']
    for mood in MOODS:
        ch=CHANNELS.get(mood)
        if not ch:continue
        with db() as c:
            with cur(c) as x:
                x.execute('SELECT message_id,title FROM tracks WHERE mood=%s ORDER BY created_at DESC,id DESC LIMIT 5',(mood,));rows=x.fetchall()
        lines.append(INFO[mood][0])
        if not rows:lines.append('  — No tracks')
        else:
            for a in rows:
                label=(a.get('title') or f'Track #{a["message_id"]}').replace('\n',' ')[:80]
                lines.append(f'  • {label}')
        lines.append('')
    send(chat,'\n'.join(lines),mood_menu())

def parse_fb(d):
    p=d.split(':',3)
    if len(p)!=4 or p[0] not in ('like','notme') or p[1] not in MOODS:return None
    try:return p[0],p[1],p[2],int(p[3])
    except:return None

def callback(c):
    uid=c.get('from',{}).get('id');msg=c.get('message',{});chat=msg.get('chat',{}).get('id');data=c.get('data','')
    if not isinstance(uid,int) or not isinstance(chat,int):return
    register(c.get('from',{}))
    if data.startswith('admin:'):
        if str(uid)!=ADMIN_USER_ID:
            answer(c.get('id'),'Admin only');return
        action=data.split(':',1)[1]
        answer(c.get('id'))
        if action=='daily':
            total,today_n,week_n,rows=daily_stats()
            lines=['📊 DAILY USERS','━━━━━━━━━━━━━━━━━━','',f'🟢 Today: {today_n}',f'📅 Last 7 days unique: {week_n}',f'👥 Total users: {total}','', 'Daily activity:']
            lines.extend(f'{d.isoformat()} → {n}' for d,n in rows)
            send(chat,'\n'.join(lines),admin_panel());return
        if action=='stats':
            cc=counts();send(chat,'📈 BOT STATS\n━━━━━━━━━━━━━━━━━━\n\n'+'\n'.join(f'{INFO[m][0]} → {cc[m]}' for m in MOODS)+f'\n\n📡 Telethon: {"CONNECTED" if ready.is_set() else "DISCONNECTED"}',admin_panel());return
        if action=='comments':send(chat,comments_text(),admin_panel());return
        if action=='top':send(chat,top_liked_text(),admin_panel());return
        if action=='broadcast':set_pending_broadcast(uid,chat);send(chat,'📣 BROADCAST\n━━━━━━━━━━━━━━━━━━\n\nNow send the post you want to broadcast.\n\n✅ Text, photo, video, audio, document and other Telegram posts are supported.\n❌ Send /cancel to stop.',admin_panel());return
        if action=='telegram':send(chat,'📡 TELETHON\n━━━━━━━━━━━━━━━━━━\n\nStatus: '+('🟢 CONNECTED' if ready.is_set() else '🔴 DISCONNECTED'),admin_panel());return
        return
    if data.startswith('br:'):
        p=data.split(':',2)
        if len(p)==3:
            try:bid=int(p[1])
            except:return
            reaction=p[2]
            if reaction not in ('love','fire','like'):return
            with db() as dbc:
                with cur(dbc) as x:
                    x.execute('SELECT 1 FROM broadcasts WHERE id=%s',(bid,))
                    if not x.fetchone():
                        answer(c.get('id'),'Broadcast not found'); return
                    x.execute('SELECT reaction FROM broadcast_reactions WHERE broadcast_id=%s AND user_id=%s',(bid,uid));old=x.fetchone()
                    if old and old['reaction']==reaction:
                        x.execute('DELETE FROM broadcast_reactions WHERE broadcast_id=%s AND user_id=%s',(bid,uid))
                    else:
                        x.execute('INSERT INTO broadcast_reactions(broadcast_id,user_id,reaction,created_at) VALUES(%s,%s,%s,%s) ON CONFLICT(broadcast_id,user_id) DO UPDATE SET reaction=EXCLUDED.reaction,created_at=EXCLUDED.created_at',(bid,uid,reaction,int(time.time())))
            answer(c.get('id'),'Reaction saved');edit_k(chat,msg.get('message_id'),broadcast_buttons(bid))
        return
    if data.startswith('bc:'):
        try:bid=int(data.split(':',1)[1])
        except:return
        with db() as dbc:
            with cur(dbc) as x:
                x.execute('SELECT 1 FROM broadcasts WHERE id=%s',(bid,))
                if not x.fetchone(): answer(c.get('id'),'Broadcast not found'); return
        set_pending_comment(uid,bid);answer(c.get('id'),'Send your comment');send(chat,'💬 Send your comment below 👇',{'force_reply':True,'input_field_placeholder':'Write a comment...'})
        return
    if data.startswith('mood_'):
        m=data[5:]
        if set_mood(uid,m):answer(c.get('id'),f'{INFO[m][0]} ✓');schedule(chat,uid,m,False)
        return
    if data=='next_music':
        m=get_mood(uid)
        if not m:answer(c.get('id'),'Choose a mood first');send(chat,'🎧 Choose your mood 👇',mood_menu())
        else:answer(c.get('id'),'⏭ Finding next track...');schedule(chat,uid,m,False)
        return
    if data=='radio':
        m=get_mood(uid) or 'melodic';set_radio(uid,True);answer(c.get('id'),'📻 Personalized Radio...');schedule(chat,uid,m,True);return
    if data=='change_mood':answer(c.get('id'),'Choose your mood');send(chat,'🎛 MOOD SELECTOR\n━━━━━━━━━━━━━━━━━━\n\nWhat are you feeling right now?',mood_menu());return
    if data=='profile':
        answer(c.get('id'))
        send(chat,profile_text(uid),{'inline_keyboard':[[{'text':'📊 TASTE ANALYTICS','callback_data':'taste_analytics'}],[{'text':'🧠 FOR YOU','callback_data':'for_you'},{'text':'📻 RADIO','callback_data':'radio'}],[{'text':'🎛 CHANGE MOOD','callback_data':'change_mood'}]]})
        return
    if data=='daily_vibe':
        answer(c.get('id'),'🔥 Daily Vibe');send_special_music(chat,uid,daily_vibe_track(uid),'🔥 YOUR DAILY VIBE');return
    if data=='for_you':
        answer(c.get('id'),'🧠 Personal pick');send_special_music(chat,uid,for_you_track(uid),'🧠 PICKED FOR YOU');return
    if data=='surprise_me':
        answer(c.get('id'),'🎲 Surprise!');send_special_music(chat,uid,surprise_track(uid),'🎲 SURPRISE ME');return
    if data=='trending':
        answer(c.get('id'));send(chat,trending_text(),mood_menu());return
    if data=='track_of_day':
        answer(c.get('id'),'🎵 Track of the Day');send_special_music(chat,uid,track_of_day(uid),'🎵 TRACK OF THE DAY');return
    if data=='taste_analytics':
        answer(c.get('id'));send(chat,taste_analytics(uid),{'inline_keyboard':[[{'text':'🧠 FOR YOU','callback_data':'for_you'},{'text':'📻 RADIO','callback_data':'radio'}],[{'text':'👤 PROFILE','callback_data':'profile'}]]});return
    if data=='top_liked':answer(c.get('id'));send(chat,top_liked_text(),mood_menu());return
    if data=='new_tracks':answer(c.get('id'));new_tracks(chat);return
    f=parse_fb(data)
    if f:
        a,m,ch,mid=f;new='like' if a=='like' else 'not_for_me';old=feedback(uid,ch,mid)
        if old==new:clear_feedback(uid,ch,mid);answer(c.get('id'),'Feedback cleared')
        else:
            if save_feedback(uid,ch,mid,m,new): answer(c.get('id'),'❤️ Added to your taste' if new=='like' else '😴 Radio will avoid this')
            else: answer(c.get('id'),'⚠️ Track not found')
        edit_k(chat,msg.get('message_id'),buttons(uid,ch,mid,m))

def command(t):return t.split(maxsplit=1)[0].lower().split('@',1)[0] if t.startswith('/') else ''
def message(m):
    chat=m.get('chat',{}).get('id');u=m.get('from',{});uid=u.get('id');
    if not isinstance(chat,int):return
    register(u);cleanup_pending();cmd=command((m.get('text') or '').strip())
    pending_b=get_pending_broadcast(uid) if str(uid)==ADMIN_USER_ID else None
    if pending_b:
        raw_text=(m.get('text') or m.get('caption') or '').strip()
        if raw_text.startswith('/cancel'):
            clear_pending_broadcast(uid);send(chat,'❌ Broadcast cancelled.',admin_panel());return
        if m.get('message_id'):
            clear_pending_broadcast(uid)
            ctype='text' if m.get('text') else ('photo' if m.get('photo') else ('video' if m.get('video') else ('audio' if m.get('audio') else ('document' if m.get('document') else ('animation' if m.get('animation') else 'media')))))
            bid=start_broadcast(uid,chat,int(m['message_id']),ctype,raw_text or None)
            send(chat,f'📣 Broadcast #{bid} started.\n\n📦 Type: {ctype}\n❤️ 🔥 👍 reactions + 💬 comments are enabled.')
            return
    pending=get_pending_comment(uid)
    if pending and (m.get('text') or '').strip() and not (m.get('text') or '').strip().startswith('/'):
        text=(m.get('text') or '').strip()[:2000];save_comment(uid,pending,text)
        send(ADMIN_USER_ID,f'💬 NEW COMMENT\n━━━━━━━━━━━━━━━━━━\nBroadcast: #{pending}\nUser: {uid}\n\n{text}')
        send(chat,'✅ Thanks! Your comment was sent to the Not Your Vibe team.',mood_menu());return
    if cmd in ('/start','/mood'):send(chat,'🎧 NOT YOUR VIBE\n━━━━━━━━━━━━━━━━━━\n\nYour music. Your mood. Your radio.\n\nChoose a mood 👇',mood_menu());return
    if cmd=='/next':
        md=get_mood(uid);send(chat,'🎧 Choose your mood first 👇',mood_menu()) if not md else schedule(chat,uid,md,False);return
    if cmd=='/radio':
        md=get_mood(uid) or 'melodic';set_radio(uid,True);schedule(chat,uid,md,True);return
    if cmd=='/profile':send(chat,profile_text(uid),mood_menu());return
    if cmd=='/new':new_tracks(chat);return
    if cmd=='/top':send(chat,top_liked_text(),mood_menu());return
    if cmd=='/dailyvibe':send_special_music(chat,uid,daily_vibe_track(uid),'🔥 YOUR DAILY VIBE');return
    if cmd=='/foryou':send_special_music(chat,uid,for_you_track(uid),'🧠 PICKED FOR YOU');return
    if cmd=='/surprise':send_special_music(chat,uid,surprise_track(uid),'🎲 SURPRISE ME');return
    if cmd=='/trending':send(chat,trending_text(),mood_menu());return
    if cmd=='/today':send_special_music(chat,uid,track_of_day(uid),'🎵 TRACK OF THE DAY');return
    if cmd=='/taste':send(chat,taste_analytics(uid),mood_menu());return
    if cmd=='/help':send(chat,'🎧 NOT YOUR VIBE\n\n/start /mood /next /radio /profile /new /top /stats /telegram /help\n\n🔥 Daily Vibe • 🧠 For You • 🎲 Surprise Me\n📈 Trending • 🎵 Track of the Day • 📊 Taste Analytics\n\n❤️ Like = improve Radio\n😴 = avoid track/mood signal');return
    if cmd=='/admin':
        if str(uid)!=ADMIN_USER_ID:return send(chat,'❌ Admin only.')
        send(chat,admin_dashboard(),admin_panel());return
    if cmd=='/broadcast':
        if str(uid)!=ADMIN_USER_ID:return send(chat,'❌ Admin only.')
        set_pending_broadcast(uid,chat)
        send(chat,'📣 BROADCAST\n━━━━━━━━━━━━━━━━━━\n\nNow send the post you want to broadcast.\n\n✅ Text, photo, video, audio, document and other Telegram posts are supported.\n❌ Send /cancel to stop.',admin_panel());return
    if cmd=='/daily':
        if str(uid)!=ADMIN_USER_ID:return send(chat,'❌ Admin only.')
        total,today_n,week_n,rows=daily_stats();lines=[f'📊 DAILY USERS\n━━━━━━━━━━━━━━━━━━','Today: {0}'.format(today_n),f'Last 7 days unique: {week_n}',f'Total users: {total}','', 'Last 7 days:']
        lines.extend(f'{d.isoformat()} → {n}' for d,n in rows);send(chat,'\n'.join(lines));return
    if cmd=='/comments':
        if str(uid)!=ADMIN_USER_ID:return send(chat,'❌ Admin only.')
        send(chat,comments_text());return
    if cmd=='/stats':
        if str(uid)!=ADMIN_USER_ID:return send(chat,'❌ Admin only.')
        send(chat,'📊 TRACKS\n\n'+'\n'.join(f'{INFO[m][0]} → {counts()[m]}' for m in MOODS)+f'\n\nTelethon: {"CONNECTED" if ready.is_set() else "DISCONNECTED"}');return
    if cmd=='/telegram':send(chat,'🟢 TELETHON CONNECTED' if ready.is_set() else '🔴 TELETHON DISCONNECTED');return

def update(u):
    if isinstance(u.get('callback_query'),Mapping):callback(u['callback_query'])
    elif isinstance(u.get('message'),Mapping):message(u['message'])

@app.route('/')
def home():return '🎧 NOT YOUR VIBE MUSIC BOT ONLINE'
@app.route('/health')
def health():
    try:
        with db() as c:
            with cur(c) as x:x.execute('SELECT 1')
        return 'OK',200
    except:return 'Database not ready',503
@app.route('/status')
def status():return {'bot':'online','ai':False,'database':'online' if db_pool else 'offline','telethon':'connected' if ready.is_set() else 'disconnected','tracks':counts()}
@app.route('/webhook',methods=['POST'])
def webhook():
    if WEBHOOK_SECRET and request.headers.get('X-Telegram-Bot-Api-Secret-Token','')!=WEBHOOK_SECRET:return 'Forbidden',403
    try:
        u=request.get_json(silent=True)
        if isinstance(u,Mapping):update(u)
    except Exception:log.exception('webhook')
    return 'OK',200

def normch(v):
    v=str(v).strip()
    if v.startswith('-100'):return v
    if v.lstrip('-').isdigit():return '-100'+v.lstrip('-')
    return None

def is_music(msg):
    if getattr(msg,'audio',None):return True
    d=getattr(msg,'document',None)
    if not d:return False
    mime=(getattr(d,'mime_type','') or '').lower()
    if mime.startswith(('audio/','video/')):return True
    name=(getattr(getattr(msg,'file',None),'name','') or '').lower();return name.endswith(AUDIO)

def message_title(msg):
    try:
        f=getattr(msg,'file',None)
        name=(getattr(f,'name','') or '').strip() if f else ''
        if name:return name.rsplit('/',1)[-1][:200]
    except:pass
    text=(getattr(msg,'message','') or '').strip()
    return text[:200] if text else None

async def scan(mood,val):
    if not val:return 0
    try:
        ent=await client.get_entity(int(val) if val.lstrip('-').isdigit() else val);n=0
        async for msg in client.iter_messages(ent):
            if is_music(msg):
                ch=normch(getattr(ent,'id',0));n+=save_track(mood,ch,msg.id,message_title(msg))
        return n
    except Exception:log.exception('scan %s',mood);return 0
async def scan_all():
    global last_scan
    channel_map.clear()
    for m,v in CHANNELS.items():
        if v:nv=normch(v)
        if nv: channel_map[nv]=m
    for m,v in CHANNELS.items():
        if v:await scan(m,v);await asyncio.sleep(.3)
    last_scan=int(time.time());log.info('tracks=%s',counts())

def tele_worker():
    global client,tele_loop
    if not (API_ID and API_HASH and SESSION):log.error('Missing Telethon API_ID/API_HASH/SESSION');return
    try: api_id=int(API_ID)
    except (TypeError,ValueError): log.error('TELETHON_API_ID/API_ID must be an integer'); return
    client=TelegramClient(StringSession(SESSION),api_id,API_HASH,connection_retries=10,retry_delay=5,timeout=30,auto_reconnect=True)
    @client.on(events.NewMessage(incoming=True))
    async def new(event):
        try:
            ch=normch(event.chat_id);m=channel_map.get(ch)
            if m and is_music(event.message):save_track(m,ch,event.message.id,message_title(event.message))
        except Exception:log.exception('watcher')
    async def run():
        global tele_loop
        tele_loop=asyncio.get_running_loop()
        while True:
            try:
                await client.connect()
                if not await client.is_user_authorized():log.error('Telethon unauthorized');return
                ready.set();await scan_all();await client.run_until_disconnected()
            except Exception:log.exception('Telethon error')
            finally:
                ready.clear()
                try:
                    if client.is_connected():await client.disconnect()
                except:pass
            await asyncio.sleep(RECONNECT)
    asyncio.run(run())

def start_telethon():
    global tele_thread
    with tele_lock:
        if tele_thread and tele_thread.is_alive():return
        tele_thread=threading.Thread(target=tele_worker,name='telethon-worker',daemon=True);tele_thread.start()

def webhook_setup():
    if not BOT_TOKEN or not RENDER_EXTERNAL_URL:return
    p={'url':RENDER_EXTERNAL_URL.rstrip('/')+'/webhook','allowed_updates':['message','callback_query'],'max_connections':40}
    if WEBHOOK_SECRET:p['secret_token']=WEBHOOK_SECRET
    log.info('webhook=%s',tg('setWebhook',p).get('ok'))

def startup():
    if not BOT_TOKEN or not DATABASE_URL:log.error('BOT_TOKEN and DATABASE_URL are required');return False
    init_db();webhook_setup();start_telethon();log.info('🟢 BOT READY');return True

if __name__=='__main__':
    if not startup():raise SystemExit(1)
    app.run(host='0.0.0.0',port=geti('PORT',10000,1,65535),threaded=True,use_reloader=False)
