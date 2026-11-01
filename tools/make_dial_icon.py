"""生成 Quill 图标 icon_dial.ico：和主页圆盘一样（浅灰圆盘 + 刻度圈 + 6 根黑色声波条），不带文字；
icon_dial_dark.ico 是深色主题用的（深灰圆盘 + 白色声波条，换主题时任务栏图标跟着换）。
.venv\\Scripts\\python.exe tools\\make_dial_icon.py
大尺寸带 60 根刻度；32 像素及以下只画圆盘和声波条（刻度太细会糊）。换桌面图标要换文件名（Explorer 按路径缓存）。"""
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent.parent
BASE = (0.34, 0.68, 1.0, 0.55, 0.84, 0.40)  # 和 app.pyw 的 BAR_BASE 一样（READY 时的高矮）
SIZES = (256, 128, 64, 48, 40, 32, 24, 20, 16)

def grad(size, y0, y1, top, bot):
    ys = np.clip((np.arange(size) - y0) / max(1.0, y1 - y0), 0, 1)
    t, b = np.array(top, float), np.array(bot, float)
    col = (t[None, :] * (1 - ys[:, None]) + b[None, :] * ys[:, None]).astype(np.uint8)
    return Image.fromarray(np.repeat(col[:, None, :], size, axis=1), 'RGB').convert('RGBA')

# 圆盘上 / 下、主刻度、小刻度、声波条上 / 下、投影
PALETTES = {'light': ((246, 246, 247), (220, 220, 223), (143, 143, 143), (190, 190, 194), (74, 74, 80), (14, 14, 16), (40, 40, 48)),
            'dark': ((62, 62, 66), (40, 40, 43), (124, 124, 128), (78, 78, 82), (199, 199, 204), (255, 255, 255), (0, 0, 0))}

def draw(n, pal=PALETTES['light']):
    top, bot, tmaj, tmin, btop, bbot, shc = pal
    ss = 4; N = n * ss; C = N / 2; small = n <= 32
    img = Image.new('RGBA', (N, N), (0, 0, 0, 0))
    R = N * (0.47 if small else 0.44)
    if not small:  # 柔和投影
        sh = Image.new('L', (N, N), 0); ImageDraw.Draw(sh).ellipse((C - R, C - R + N * 0.025, C + R, C + R + N * 0.025), fill=90)
        img.putalpha(0); img = Image.composite(Image.new('RGBA', (N, N), shc + (255,)), img, sh.filter(ImageFilter.GaussianBlur(N * 0.025)))
    disc = Image.new('L', (N, N), 0); ImageDraw.Draw(disc).ellipse((C - R, C - R, C + R, C + R), fill=255)
    img.paste(grad(N, C - R, C + R, top, bot), (0, 0), disc)  # 上亮下暗，像凸起
    d = ImageDraw.Draw(img)
    if not small:
        for i in range(60):
            major = i % 15 == 0; a = i / 60 * math.tau - math.pi / 2; ca, sa = math.cos(a), math.sin(a)
            r1 = R * 0.86; r0 = R * (0.72 if major else 0.78)
            d.line((C + ca * r0, C + sa * r0, C + ca * r1, C + sa * r1), fill=(tmaj if major else tmin) + (255,),
                   width=max(1, round(N * (0.012 if major else 0.007))))
    H = R * (1.05 if small else 0.9); bw = H * (0.16 if small else 0.13); gap = bw * (0.6 if small else 0.78)
    x0 = C - (6 * bw + 5 * gap) / 2; m = Image.new('L', (N, N), 0); md = ImageDraw.Draw(m)
    for i, b in enumerate(BASE):
        hh = max(bw, b * H) / 2; x = x0 + i * (bw + gap)
        md.rounded_rectangle((x, C - hh, x + bw, C + hh), bw / 2, fill=255)
    img.paste(grad(N, C - H / 2, C + H / 2, btop, bbot), (0, 0), m)
    return img.resize((n, n), Image.LANCZOS)

if __name__ == '__main__':
    for name, suffix in (('light', ''), ('dark', '_dark')):
        ims = [draw(n, PALETTES[name]) for n in SIZES]
        ims[0].save(HERE / f'icon_dial{suffix}.ico', sizes=[(n, n) for n in SIZES], append_images=ims[1:])
        ims[0].save(HERE / f'icon_dial{suffix}.png')
    print('写好了 icon_dial.ico / icon_dial_dark.ico 和对应 png')
