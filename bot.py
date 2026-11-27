import sys, asyncio
if sys.version_info >= (3, 10):
    asyncio.set_event_loop(asyncio.new_event_loop())

import os, re, time, queue, threading, subprocess, shutil, logging
from pyrogram import Client, filters
from yt_dlp import YoutubeDL
import imageio_ffmpeg

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("bot")

API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not (API_ID and API_HASH and BOT_TOKEN):
    raise ValueError("⚠️ أضف API_ID و API_HASH و BOT_TOKEN في متغيرات البيئة.")

tg = Client("twitter_vod_bot", api_id=int(API_ID), api_hash=API_HASH,
            bot_token=BOT_TOKEN, in_memory=True, workers=2)

MAX_SECONDS = 30 * 60
MAX_QUEUE = 3
jobs = queue.Queue(maxsize=MAX_QUEUE)
URL_RE = re.compile(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/\S+', re.I)

HELP = ("⬇️ أرسل رابط بث/فيديو من تويتر/X بعد ما انتهى ونزل كبوست.\n"
        "⏱ الحد الأقصى: 30 دقيقة.\n📐 الأبعاد: نفس الأصل بالضبط بدون تمدد.")


def get_ffmpeg():
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    ffdir = '/tmp/ffbin'
    os.makedirs(ffdir, exist_ok=True)
    link = os.path.join(ffdir, 'ffmpeg')
    if not os.path.exists(link):
        try:
            os.symlink(ff, link)
        except OSError:
            shutil.copyfile(ff, link)
            os.chmod(link, 0o755)
    return ff, ffdir


def probe(ff, path):
    """اقرأ الأبعاد الحقيقية للملف المنزّل."""
    r = subprocess.run([ff, '-i', path], capture_output=True, text=True)
    err = r.stderr or ''
    m = re.search(r'Stream.*Video:.*?(\d{2,5})x(\d{2,5})', err)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


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
        tg.edit_message_text(chat_id, notify_id, "🔍 جاري فحص البث...")
        with YoutubeDL({'quiet': True, 'no_warnings': True, 'noplaylist': True}) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get('live_status') == 'is_live':
            raise RuntimeError("البث ما زال مباشرًا ⏺️ أرسل الرابط بعد ما ينتهي وينزل كبوست.")
        duration = info.get('duration') or 0
        if duration > MAX_SECONDS:
            raise RuntimeError(f"المدة {duration // 60} دقيقة — أطول من الحد (30 دقيقة).")

        # 📐 الأبعاد الأصلية المعلنة من X — هذي مرجعنا الوحيد
        src_w = info.get('width') or 0
        src_h = info.get('height') or 0

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
                                     f"⬇️ جاري التحميل...\n📊 {pct:.1f}%\n"
                                     f"💾 {done/1e6:.1f} / {total/1e6:.1f} MB\n🚀 {speed:.2f} MB/s")
            except Exception:
                pass

        ff, ffdir = get_ffmpeg()
        base = f'/tmp/dl_{int(time.time())}_{chat_id}'
        opts = {
            'quiet': True, 'no_warnings': True, 'noplaylist': True,
            'format': 'best',
            'outtmpl': base + '.%(ext)s',
            'ffmpeg_location': ffdir,
            'fragment_retries': 5,
            'progress_hooks': [hook],
        }
        with YoutubeDL(opts) as ydl:
            dl_info = ydl.extract_info(url, download=True)
        filepath = (dl_info.get('requested_downloads') or [{}])[0].get('filepath')
        if not filepath or not os.path.exists(filepath):
            raise RuntimeError("ما لقيت الملف بعد التحميل")

        # 🔧 المطابقة الجذرية للأبعاد
        clean = base + '_clean.mp4'
        fw, fh = probe(ff, filepath)
        tg.edit_message_text(chat_id, notify_id, f"🔧 مطابقة الأبعاد الأصلية ({src_w}x{src_h})...")

        if src_w and src_h and (fw, fh) != (src_w, src_h):
            # تعارض أبعاد → نجبر أبعاد الأصل بالضبط + حواف سوداء بدل التمدد (استحالة سترچ)
            vf = (f"scale={src_w}:{src_h}:force_original_aspect_ratio=decrease,"
                  f"pad={src_w}:{src_h}:(ow-iw)/2:(oh-ih)/2,setsar=1")
            cmd = [ff, '-y', '-loglevel', 'error', '-i', filepath, '-vf', vf,
                   '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                   '-c:a', 'copy', '-movflags', '+faststart', clean]
        else:
            # الأبعاد سليمة → نسخ سريع بدون إعادة ترميز + faststart
            cmd = [ff, '-y', '-loglevel', 'error', '-i', filepath,
                   '-c', 'copy', '-movflags', '+faststart', clean]
        subprocess.run(cmd, check=True, timeout=1800)
        os.remove(filepath)
        filepath = clean

        out_w, out_h = (src_w, src_h) if src_w and src_h else (fw, fh)
        size = os.path.getsize(filepath)
        if size < 50_000:
            raise RuntimeError("الملف فاضي أو تالف")
        if size > 1_900_000_000:
            raise RuntimeError("حجم الملف أكبر من حد تليجرام (2GB)")

        tg.edit_message_text(chat_id, notify_id, f"📤 جاري الرفع ({size/1e6:.1f} MB)...")
        # 📐 نخبر تليجرام بالأبعاد صراحةً عشان ما يجتهد من عنده
        tg.send_video(chat_id, filepath,
                      width=out_w, height=out_h,
                      duration=int(duration) or None,
                      supports_streaming=True,
                      caption=f"⏺️ البث بعد انتهاءه — {out_w}x{out_h} — المدة: {duration // 60} دقيقة")
        sent = True
        tg.edit_message_text(chat_id, notify_id, "✅ تم التحميل والإرسال بأبعاد الأصل!")
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
        logger.info("🤖 بوت تحميل بثوث تويتر — نسخة الأبعاد المضمونة.")
        await asyncio.Event().wait()

    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        loop.run_until_complete(tg.stop())
