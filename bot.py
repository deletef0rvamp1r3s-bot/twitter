import sys, asyncio
if sys.version_info >= (3, 10):
    asyncio.set_event_loop(asyncio.new_event_loop())

import os, re, time, queue, threading, logging
from pyrogram import Client, filters
from yt_dlp import YoutubeDL

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("bot")

# 🔑 المفاتيح من متغيرات البيئة
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not (API_ID and API_HASH and BOT_TOKEN):
    raise ValueError("⚠️ أضف API_ID و API_HASH و BOT_TOKEN في متغيرات البيئة.")

tg = Client("twitter_vod_bot", api_id=int(API_ID), api_hash=API_HASH,
            bot_token=BOT_TOKEN, in_memory=True, workers=2)

MAX_SECONDS = 30 * 60          # ⏱ الحد الأقصى: بث 30 دقيقة
MAX_QUEUE = 3                  # طابور حماية السيرفر
jobs = queue.Queue(maxsize=MAX_QUEUE)
URL_RE = re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+', re.I)

HELP = ("⬇️ أرسل لي رابط بث/فيديو من تويتر/X **بعد ما انتهى ونزل كبوست** وسأحمّله وأرسله لك جاهزًا.\n"
        "⏱ الحد الأقصى للمدة: 30 دقيقة.\n"
        "مثال: https://x.com/user/status/123456789")


@tg.on_message(filters.command("start"))
def on_start(c, m):
    m.reply(HELP)


@tg.on_message(filters.text & ~filters.bot)
def on_text(c, m):
    match = URL_RE.search(m.text or '')
    if not match:
        return
    notify = m.reply("⏳ جاري فحص الرابط...")
    try:
        jobs.put_nowait((m.chat.id, match.group(0), notify.id))
    except queue.Full:
        notify.edit("⚠️ الطابور ممتلئ حاليًا، جرب بعد قليل.")


def worker():
    """تحميل واحد بالمرّة — والباقي بالطابور."""
    while True:
        chat_id, url, notify_id = jobs.get()
        try:
            download_and_send(chat_id, url, notify_id)
        except Exception as e:
            logger.exception(e)
            try:
                tg.edit_message_text(chat_id, notify_id, f"❌ فشل التحميل: {e}")
            except Exception:
                pass
        finally:
            jobs.task_done()


def download_and_send(chat_id, url, notify_id):
    filepath = None
    sent = False
    try:
        # 1) فحص الفيديو: مدته وحالته
        tg.edit_message_text(chat_id, notify_id, "🔍 جاري فحص البث...")
        probe = {'quiet': True, 'no_warnings': True, 'noplaylist': True}
        with YoutubeDL(probe) as ydl:
            info = ydl.extract_info(url, download=False)

        live_status = info.get('live_status')
        duration = info.get('duration') or 0

        if live_status == 'is_live':
            raise RuntimeError("البث ما زال مباشرًا الآن ⏺️ أرسل الرابط بعد ما ينتهي وينزل كبوست.")
        if duration > MAX_SECONDS:
            raise RuntimeError(f"المدة {duration // 60} دقيقة — أطول من الحد المسموح (30 دقيقة).")

        # 2) التحميل مع شريط تقدم حي
        state = {'last': 0.0}
        def hook(d):
            if d.get('status') != 'downloading':
                return
            now = time.time()
            if now - state['last'] < 2.5:
                return
            state['last'] = now
            done = d.get('downloaded_bytes') or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            pct = (done * 100 / total) if total else 0
            speed = (d.get('speed') or 0) / 1e6
            try:
                tg.edit_message_text(chat_id, notify_id,
                                     f"⬇️ جاري تحميل البث...\n"
                                     f"📊 {pct:.1f}%\n"
                                     f"💾 {done/1e6:.1f} / {total/1e6:.1f} MB\n"
                                     f"🚀 السرعة: {speed:.2f} MB/s")
            except Exception:
                pass

        opts = {
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'format': 'bv*+ba/b',
            'merge_output_format': 'mp4',
            'outtmpl': f'/tmp/dl_{int(time.time())}_{chat_id}.%(ext)s',
            'concurrent_fragment_downloads': 4,   # 🚀 تسريع تحميل مقاطع HLS
            'progress_hooks': [hook],
        }
        with YoutubeDL(opts) as ydl:
            dl_info = ydl.extract_info(url, download=True)
        filepath = (dl_info.get('requested_downloads') or [{}])[0].get('filepath')
        if not filepath or not os.path.exists(filepath):
            raise RuntimeError("ما لقيت الملف بعد التحميل")

        size = os.path.getsize(filepath)
        if size < 50_000:
            raise RuntimeError("الملف فاضي أو تالف")
        if size > 1_900_000_000:
            raise RuntimeError("حجم الملف أكبر من حد تليجرام (2GB)")

        # 3) الرفع ثم التنظيف
        tg.edit_message_text(chat_id, notify_id, f"📤 جاري رفع البث ({size/1e6:.1f} MB)...")
        tg.send_video(chat_id, filepath,
                      caption=f"⏺️ البث بعد انتهاءه — المدة: {duration // 60} دقيقة")
        sent = True
        tg.edit_message_text(chat_id, notify_id, "✅ تم تحميل البث وإرساله!")
    finally:
        if not sent and filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except OSError:
                pass


if __name__ == "__main__":
    threading.Thread(target=worker, daemon=True).start()

    async def main():
        await tg.start()
        logger.info("🤖 بوت تحميل بثوث تويتر (VOD) شغال الآن — حد 30 دقيقة.")
        await asyncio.Event().wait()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(tg.stop())
