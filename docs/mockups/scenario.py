# Scripted *illustrative* conversation used to render docs/*.png and docs/demo.gif.
# Usage: MOCK_FONT_DIR=file:///path/to/fonts python3 render.py scenario ../   (needs Chrome + ffmpeg + Pillow)
from render import *
SPEC=dict(name='Sticker Maker', icon='🖼', c1='#ee5b9a', c2='#8a5cf6', scenarios=[
 dict(steps=[
  B(media=image('#8ec5fc','#e0c3fc','🐱','photo.jpg · 4032×3024'), id='ph'),
  B('🖼 عکس دریافت شد → تبدیل به WebP <b>512×512</b>…', id='p'),
  B(media=sticker('', '<div style="font-size:96px">🐱</div>'), id='s', text='✅ پیش‌نمایش · ۱۴۸ KB (زیر حد مجاز)',
    buttons=[['➕ افزودن به پک','😀 ویرایش ایموجی'],['✂️ برش دوباره']]),
 ]),
 dict(steps=[
  U('متن استیکر'),
  B(media=sticker('', '<div style="font:700 34px Vz;color:#8a5cf6;text-shadow:2px 2px 0 #fff,-2px -2px 0 #fff,2px -2px 0 #fff,-2px 2px 0 #fff">سلام دنیا!</div>'),
    id='t', text='🔤 متن فارسی با حروف چسبیده و جهت درست نوشته شد.',
    buttons=[['🎨 رنگ','🔠 فونت'],['➕ افزودن به پک']]),
 ]),
 dict(steps=[
  U('/packs'),
  B('📚 <b>پک‌های من</b>\n\n1️⃣ پک گربه‌ها · ۱۲ استیکر\n2️⃣ پک متن‌ها · ۸ استیکر', id='k',
    buttons=[['➕ پک جدید'],['👁 مشاهده','🗑 حذف استیکر'],['🎁 دعوت دوستان (+سهمیه)']]),
 ]),
])
