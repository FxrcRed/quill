"""主页常用词卡片：截平时 / 按下两张图（先关掉正在跑的 Quill，标题都叫 Quill）。"""
import ctypes, importlib.machinery, importlib.util, subprocess, sys, time
import tkinter as tk
from pathlib import Path
ctypes.windll.shcore.SetProcessDpiAwareness(1)
HERE = Path(sys.argv[1])
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts()
root = tk.Tk(); root.withdraw(); m.root = root; m.S = root.winfo_fpixels('1i') / 96
m.setup_fonts(root)
app = m.App(root); root.deiconify()
def pump(t):
    end = time.time() + t
    while time.time() < end: root.update(); m.sync_chromes(); time.sleep(0.02)
def shot(name):
    pr = subprocess.Popen([sys.executable, str(HERE / 'shot.py'), m.TITLE, str(HERE / 'e2e_out' / name)])
    while pr.poll() is None: root.update(); time.sleep(0.02)
root.geometry(f'+{int(sys.argv[2])}+40'); pump(0.5)
def rects():
    ws = [app.root] + [getattr(app, a).w for a in ('settings', 'history', 'words') if app.alive(a) and getattr(app, a).w.winfo_viewable()]
    rs = sorted((v.winfo_x(), v.winfo_x() + v.winfo_width(), v.title()) for v in ws)
    over = any(a[1] > b[0] for a, b in zip(rs, rs[1:])); gaps = [b[0] - a[1] for a, b in zip(rs, rs[1:])]
    return f'{[(r[2], r[0], r[1]) for r in rs]} 间隔 {gaps} 重叠 {over} 出屏 {rs[0][0] < 0 or rs[-1][1] > 2560}'
def align():
    bad = []
    for c in m.CHROMES:
        if not c.shown: continue
        a, b = ctypes.wintypes.RECT(), ctypes.wintypes.RECT(); m.user32.GetWindowRect(c.hwnd, ctypes.byref(a)); m.user32.GetWindowRect(c.shwnd, ctypes.byref(b))
        if (b.left + m.px(c.M), b.top + m.px(c.M)) != (a.left, a.top): bad.append(c.win.title())
    return bad or "全部对齐"
for f in (app.open_settings, app.open_history, app.edit_words):
    f(); print("刚打开时窗口和阴影：", align()); pump(0.4); print(rects())
sys.stdout.flush(); import os; os._exit(0)
