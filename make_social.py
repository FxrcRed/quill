"""GitHub 分享预览图 docs/social.png（1280×640）：左边图标 + 名字 + 一句话，右边主窗口截图（先跑 make_demo.py 生成 docs/main.png）。
.venv\\Scripts\\python.exe make_social.py，然后在仓库 Settings → Social preview 上传。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).parent
W, H = 1280, 640
im = Image.new('RGBA', (W, H), (230, 230, 230, 255)); d = ImageDraw.Draw(im)
icon = Image.open(HERE / 'icon_dial.png').convert('RGBA').resize((120, 120), Image.LANCZOS); im.alpha_composite(icon, (90, 120))
title = ImageFont.truetype(str(HERE / 'fonts' / 'Michroma-Regular.ttf'), 76)
body = ImageFont.truetype(str(HERE / 'fonts' / 'NotoSansSC-Medium.otf'), 34)
small = ImageFont.truetype(str(HERE / 'fonts' / 'NotoSansSC-Medium.otf'), 26)
d.text((86, 270), 'QUILL', font=title, fill=(30, 30, 30))
for i, t in enumerate(['Offline voice typing for Windows.', 'Speak messy English or Chinese,', 'get clean English at your cursor.']):
    d.text((90, 380 + i * 48), t, font=body, fill=(43, 43, 43))
d.text((90, 545), 'Parakeet · SenseVoice · Qwen3 · 100% local', font=small, fill=(140, 140, 140))
shot = Image.open(HERE / 'docs' / 'main.png').convert('RGBA'); s = 560 / shot.height
shot = shot.resize((round(shot.width * s), 560), Image.LANCZOS); im.alpha_composite(shot, (W - shot.width - 90, 40))
im.convert('RGB').save(HERE / 'docs' / 'social.png'); print('docs/social.png', im.size)
