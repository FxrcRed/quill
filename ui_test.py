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

app = m.App(root); root.deiconify()
threading.Thread(target=m.eng.load, daemon=True).start()

def pump(secs):
    t = time.time()
    while time.time() - t < secs: root.update(); time.sleep(0.02)

pump(6)  # 等 Whisper 载入、主页出 READY
canvas_texts('主页', app.n.c); canvas_texts('主页常用词', app.wn.c); shot('Quill', 'main')
for attr, opener, title in (('settings', app.open_settings, '设置'), ('history', app.open_history, '记录'), ('words', app.open_words, '常用词'),
                            ('check_win', app.open_checks, '自检'), ('ready_win', app.open_ready, '准备情况')):
    opener(); pump(1.5); win = getattr(app, attr)
    canvas_texts(title, win.n.c); shot(title, attr); win.w.withdraw()
ov = app.overlay  # 浮窗各状态（只记字，样子看 e2e_test 存的图）
for f in (lambda: ov.live('', ''), lambda: ov.think(''), lambda: ov.result('I go there.', 'I went there.', None, True),
          lambda: ov.notice(m.L('按得太短了：按住说完一整句再松开。', 'Too short: hold the key until you finish the sentence.'), 'warn')):
    f(); pump(0.4)
for st in ('loading', 'ready', 'rec', 'busy'):  # 主页圆盘下面那行
    app.state = st; drawn.append(('主页提示', app.sub_text()))

han = re.compile(r'[一-鿿]')
bad = [(w, t) for w, t in drawn if LANG == 'en' and han.search(t) and not is_user(t)]
for w, t in bad: print('漏翻', w, '|', t.replace('\n', ' ')[:100])
print(f'{LANG}：画出来 {len(drawn)} 段字，漏翻 {len(bad)} 处；截图在 e2e_out\\ui_{LANG}_*.png')
app.state = 'ready'; before = app.sub_text()  # 设置里切到另一种语言：整个界面重建，主页提示跟着换
app.open_settings(); pump(1); app.settings.set_ui('zh' if LANG == 'en' else 'en'); pump(2)
print('切换语言：', '通过' if app.sub_text() != before else '没变', '|', app.sub_text())
root.destroy()
