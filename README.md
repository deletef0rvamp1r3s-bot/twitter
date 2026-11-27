# ⬇️ بوت تحميل بثوث تويتر/X (بعد انتهاءها)

يحمّل البث/الفيديو بعد ما ينتهي وينزل كبوست (VOD) ويرسله كملف فيديو جاهز.
- الحد الأقصى للمدة: 30 دقيقة.
- إذا البث ما زال مباشرًا: يرفض ويطلب إرسال الرابط بعد الانتهاء.

## المتطلبات
Python 3.10+ ومفاتيح: API_ID + API_HASH + BOT_TOKEN

## النشر على Railway
1. ارفع المشروع على GitHub.
2. Railway → New Project → Deploy from GitHub repo.
3. Variables: API_ID / API_HASH / BOT_TOKEN.
4. Settings → Start Command: python bot.py
5. Deploy ✅
