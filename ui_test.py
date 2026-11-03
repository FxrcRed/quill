"""界面语言检查：.venv\\Scripts\\python.exe ui_test.py [en|zh]（默认 en）
按这种界面语言打开主页、全部子窗口、浮窗各状态，收集画出来的字：en 下带汉字、又不是记录 / 常用词里用户自己的话，就是漏翻，打印出来。
每页截图存到 e2e_out\\ui_<语言>_*.png，看字有没有出框、截断。不写 history / settings。
要先关掉正在运行的 Quill（同 e2e_test.py）。"""
import ctypes, importlib.machinery, importlib.util, re, subprocess, sys, threading, time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).parent; OUT = HERE / 'e2e_out'; OUT.mkdir(exist_ok=True)
LANG = 'zh' if 'zh' in sys.argv else 'en'
ctypes.windll.shcore.SetProcessDpiAwareness(1)
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts()
root = tk.Tk(); root.withdraw(); m.root = root; m.S = root.winfo_fpixels('1i') / 96
m.setup_fonts(root); m.apply_theme(m.system_theme())
m.save_history = lambda *a: None; m.save_cfg = lambda: None
m.cfg['ui'] = LANG

# en 界面下画出来的字带汉字、又不是用户自己说的话 / 写的词 = 漏翻
user = ''.join((HERE / f).read_text(encoding='utf-8') for f in ('history.txt', 'words.txt') if (HERE / f).exists())
user = re.sub(r'\s', '', user)
def is_user(t):  # 整段都出自记录 / 常用词（去掉「Said: 」这类前缀、折行和截断的 …）才算用户说的话；只看片段会把「永久」这种也放过
    core = re.sub(r'^.*?(Said: |Misheard: |→ )', '', t).split('…')[0]
    return all(p in user for p in re.split(r', ', re.sub(r'\s', '', core)) if p)

drawn = []  # (在哪, 字)
orig_flow = m.flow
def flow(runs, *a, **k):  # 浮窗的字画在 PIL 图上，在这里记下
    drawn.append(('浮窗', ''.join(r[0] for r in runs))); return orig_flow(runs, *a, **k)
m.flow = flow

def canvas_texts(where, c):
    for i in c.find_all():
        if c.type(i) == 'text': drawn.append((where, c.itemcget(i, 'text')))

def shot(title, name):  # 别的进程用 PrintWindow 截，这边主循环照常跑（窗口要能应答）
    p = subprocess.Popen([sys.executable, str(HERE / 'tools' / 'shot.py'), title, str(OUT / f'ui_{LANG}_{name}.png')], stdout=subprocess.DEVNULL)
    while p.poll() is None: root.update(); time.sleep(0.02)

import numpy as np
offs = []  # 对不齐的：(在哪, 什么, 竖直偏, 水平偏)，物理像素，正 = 偏下 / 偏右
def ink_center(im, x0, y0, x1, y1):
    reg = im[max(y0, 0):y1, max(x0, 0):x1]
    if reg.size == 0: return None
    mask = np.abs(reg - np.median(reg)) > 40; rows = mask.sum(1); cols = np.where(mask.any(0))[0]
    if not rows.any(): return None
    top = np.where(rows > 0)[0].min(); base = np.where(rows >= 0.3 * rows.max())[0].max()  # 从最高的墨迹量到基线：g / y 的下伸笔画墨迹少，不算（看着居中靠的是大写高度到基线）
    return max(y0, 0) + (top + base) / 2, max(x0, 0) + (cols.min() + cols.max()) / 2

def check_align(where, n):
    """按钮里的字 / 图标离按钮中心多远；同一行左边的标签（「Model」「快捷键」、标题）和右边按钮的中心差多少。"""
    c, S = n.c, m.S; oy = c.canvasy(0)
    im = np.asarray(m.print_window(c.winfo_id()).convert('L')).astype(int)
    rows, mine = [], set()
    for b in n.btns:
        if not isinstance(b, m.Btn) or not b.lab: continue
        mine.update(b.lab); x, y, w, h = b.box
        cx, cy = (x + w / 2) * S, (y + h / 2) * S - oy
        if not 0 < cy < im.shape[0]: continue
        rows.append((cy, h * S / 2, b))
        p = ink_center(im, round(x * S) + 6, round(y * S - oy) + 4, round((x + w) * S) - 6, round((y + h) * S - oy) - 4)
        if not p: continue
        dy, dx = p[0] - cy, (p[1] - cx) if b.align == 'center' else 0
        if abs(dy) > 1.1 or abs(dx) > 1.5: offs.append((where, b.text or f'图标 {b.icon!r}', dy, dx))  # 量出来是 0.5 一档，1.0 算对齐
    by_row = {}  # 标签和同一行的按钮：一行里只有一个标签才比（两行字的块，比如常用词 + 「听错：」，是整块对着按钮居中）
    for i in c.find_all():
        if c.type(i) != 'text' or i in mine or not c.itemcget(i, 'text').strip(): continue
        x0, y0, x1, y1 = c.bbox(i)
        if y1 - y0 > 1.5 * m.fnt(12).metrics('linespace'): continue  # 折成几行的字（记录正文）是整块排的，不按单行比
        p = ink_center(im, x0, round(y0 - oy), x1, round(y1 - oy))
        near = p and [(abs(p[0] - cy), cy) for cy, hh, b in rows if abs(p[0] - cy) < hh]
        if near: by_row.setdefault(min(near)[1], []).append((p[0], c.itemcget(i, 'text')))
    for cy, labs in by_row.items():
        if len(labs) == 1 and abs(d := labs[0][0] - cy) > 1.5: offs.append((where, '标签 ' + labs[0][1][:20], d, 0))

app = m.App(root); root.deiconify()
threading.Thread(target=m.eng.load, daemon=True).start()

def pump(secs):
    t = time.time()
    while time.time() - t < secs: root.update(); time.sleep(0.02)

pump(6)  # 等 Whisper 载入、主页出 READY
canvas_texts('主页', app.n.c); canvas_texts('主页常用词', app.wn.c); shot('Quill', 'main'); check_align('主页', app.n); check_align('主页常用词', app.wn)
for attr, opener, title in (('settings', app.open_settings, '设置'), ('history', app.open_history, '记录'), ('words', app.open_words, '常用词'),
                            ('check_win', app.open_checks, '自检'), ('ready_win', app.open_ready, '准备情况')):
    opener(); pump(1.5); win = getattr(app, attr)
    canvas_texts(title, win.n.c); shot(title, attr); check_align(title, win.n); win.w.withdraw()
ov = app.overlay  # 浮窗各状态（只记字，样子看 e2e_test 存的图）
for f in (lambda: ov.live('', ''), lambda: ov.think(''), lambda: ov.result('I go there.', 'I went there.', None, True),
          lambda: ov.notice(m.L('按得太短了：按住说完一整句再松开。', 'Too short: hold the key until you finish the sentence.'), 'warn')):
    f(); pump(0.4)
for st in ('loading', 'ready', 'rec', 'busy'):  # 主页圆盘下面那行
    app.state = st; drawn.append(('主页提示', app.sub_text()))

han = re.compile(r'[一-鿿]')
bad = [(w, t) for w, t in drawn if LANG == 'en' and han.search(t) and not is_user(t)]
for w, t in bad: print('漏翻', w, '|', t.replace('\n', ' ')[:100])
for w, t, dy, dx in offs: print(f'没对齐 {w} | {t!r} 竖直 {dy:+.1f}px' + (f' 水平 {dx:+.1f}px' if dx else ''))
print(f'{LANG}：画出来 {len(drawn)} 段字，漏翻 {len(bad)} 处，没对齐 {len(offs)} 处（> 1px）；截图在 e2e_out\\ui_{LANG}_*.png')
app.state = 'ready'; before = app.sub_text()  # 设置里切到另一种语言：整个界面重建，主页提示跟着换
app.open_settings(); pump(1); app.settings.set_ui('zh' if LANG == 'en' else 'en'); pump(2)
print('切换语言：', '通过' if app.sub_text() != before else '没变', '|', app.sub_text())
seen = []  # 主页的说话语言按钮：按三下应该转一圈回到原处
for _ in range(3): app.next_lang(); seen.append(app.lang_btn.text)
print('语言按钮：', ' → '.join(seen))
root.destroy()
