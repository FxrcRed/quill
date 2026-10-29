r"""生成 README 用的常用词演示图：.venv\Scripts\python.exe make_words_demo.py  → docs/words.png
左边常用词窗口（演示词表，不用自己的 words.txt），右边语音合成说「I asked cloud to fix the bug」后真实的底部浮窗（cloud → Claude）。
不用麦克风、不真的粘贴、不写 history、不扫记录。要先关掉正在运行的 Quill（Stop-Process -Name pythonw）。"""
import ctypes, importlib.machinery, importlib.util, subprocess, sys, threading, time, wave
import tkinter as tk
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent; OUT = HERE / 'docs'; OUT.mkdir(exist_ok=True)
LINE = 'I asked cloud to fix the bug.'
def tts(lang, text, path):
    """System.Speech 合成 16kHz 单声道 wav（文字经文件传，PowerShell 5.1 命令行里的中文会乱码）。"""
    (HERE / '_tts.txt').write_text(text, encoding='utf-8')
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$v = $s.GetInstalledVoices() | % {{ $_.VoiceInfo }} | ? {{ $_.Culture.Name -like '{'zh-CN' if lang == 'zh' else 'en-*'}' }} | select -First 1; "
          "$s.SelectVoice($v.Name); $s.Rate = -1; "
          "$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono); "
          f"$s.SetOutputToWaveFile('{path}', $f); $s.Speak([IO.File]::ReadAllText('{HERE / '_tts.txt'}', [Text.Encoding]::UTF8)); $s.Dispose()")
    subprocess.run(['powershell', '-NoProfile', '-Command', ps], check=True); (HERE / '_tts.txt').unlink()
    with wave.open(str(path)) as w: return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768

WORDS = 'GitHub\nOllama: 奥拉玛\nQuill: quilt\nClaude: cloud, 克劳德\n'  # 窗口里新加的在上面：Claude 放最后才排第一

ctypes.windll.shcore.SetProcessDpiAwareness(1)
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts(); root = tk.Tk(); root.withdraw(); m.root = root; m.S = S = root.winfo_fpixels('1i') / 96; m.setup_fonts(root)
m.save_history = m.save_cfg = lambda *a: None
m.WORDS_FILE = HERE / '_demo_words.txt'; m.WORDS_FILE.write_text(WORDS, encoding='utf-8')
m.word_sugs = lambda: []; m.App.words_loop = lambda self: None  # 不读、不扫真实记录
typed, latest = [''], [None]
m.paste = lambda t: typed.__setitem__(0, t)
orig = m.ulw
def ulw(h, img, x, y): orig(h, img, x, y); latest[0] = img.copy()
m.ulw = ulw
m.Overlay.grab_backdrop = lambda self, box: Image.new('RGBA', (box[2] - box[0], box[3] - box[1]), (238, 238, 240, 255))  # 玻璃后面用固定底色
audio = tts('en', LINE, HERE / '_demo_words.wav')
m.cfg.update(lang='en', hotkey=0xA5)
app = m.App(root); root.deiconify()
for t in (m.fix_worker, m.eng.load): threading.Thread(target=t, daemon=True).start()
shot = [None]

def scenario():
    try:
        m.eng.loaded.wait(); time.sleep(3)
        a = np.concatenate([np.zeros(4800, np.float32), audio, np.zeros(6400, np.float32)])
        m.eng.start('key'); t0 = time.time(); F = 480
        for i in range(0, len(a), F):
            m.eng.chunks.append(a[i:i + F, None].copy()); m.eng.peak = max(m.eng.peak, float(np.abs(a[i:i + F]).max()))
            time.sleep(max(0, t0 + (i + F) / 16000 - time.time()))
        m.eng.stop()
        while not typed[0]: time.sleep(0.05)
        time.sleep(0.6); shot[0] = latest[0]  # 出结果后浮窗定住的那一帧
        print('输入：', typed[0], flush=True)
    finally:
        root.after(0, finish)

def grab(title):
    p = HERE / '_shot.png'; q = subprocess.Popen([sys.executable, str(HERE / 'shot.py'), title, str(p)])
    while q.poll() is None: root.update(); time.sleep(0.02)  # PrintWindow 要等本进程处理消息，不能干等
    im = Image.open(p).convert('RGBA'); p.unlink(); return im

def finish():
    app.edit_words(); app.words.ent.delete(0, 'end'); root.focus_force()  # 输入框别有焦点（光标 / 输入法会画出一个点）
    for _ in range(20): root.update(); m.sync_chromes(); time.sleep(0.03)
    words = grab('常用词'); build(words, shot[0]); root.destroy()
    for p in (m.WORDS_FILE, HERE / '_demo_words.wav'): p.unlink(missing_ok=True)

def build(words, ov):
    """拼图：底色卡片上左边常用词窗口（只留上面一截，下边渐隐），右边上面一行灰字「You say」+ 原话，下面真实浮窗。坐标按 100% 写，乘 S。"""
    r = round(48 * S) - 3; mask = Image.new('L', words.size, 0)  # 窗口圆角外面透明（同 make_demo 的 main.png）
    ImageDraw.Draw(mask).rounded_rectangle((3, 3, words.width - 4, words.height - 4), r, fill=255); words.putalpha(mask)
    wh = round(520 * S); words = words.crop((0, 0, words.width, wh))
    fade = Image.linear_gradient('L').resize((words.width, round(120 * S)))  # 0 → 255 往下
    a = words.getchannel('A'); band = a.crop((0, wh - fade.height, words.width, wh))
    a.paste(Image.composite(Image.new('L', band.size, 0), band, fade), (0, wh - fade.height)); words.putalpha(a)
    W, H = round(1000 * S), round(560 * S)
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); bg = Image.new('RGBA', (W, H), m.BG + 'FF' if isinstance(m.BG, str) else m.BG)
    cm = Image.new('L', (W, H), 0); ImageDraw.Draw(cm).rounded_rectangle((0, 0, W - 1, H - 1), round(28 * S), fill=255)
    im.paste(bg, (0, 0), cm)
    im.alpha_composite(words, (round(20 * S), round(20 * S)))
    x = round(20 * S) + words.width + round(20 * S); d = ImageDraw.Draw(im)
    fl, fe = m.pfont(11), m.pfont(16)
    d.text((x + 20 * S, 120 * S), 'You say', font=fl, fill=m.SUB)
    d.text((x + 20 * S, 146 * S), LINE.rstrip('.'), font=fe, fill=m.TEXT)
    d.text((x + 20 * S, 186 * S), 'Quill types', font=fl, fill=m.SUB)
    ow = W - x - round(20 * S)  # 右边和左边一样留 20
    if ov.width > ow: ov = ov.resize((ow, round(ov.height * ow / ov.width)), Image.LANCZOS)
    im.alpha_composite(ov, (x, round(206 * S)))
    out = im.resize((1000, 560), Image.LANCZOS); out.save(OUT / 'words.png', optimize=True)
    print('words.png', out.size, round((OUT / 'words.png').stat().st_size / 1e3), 'KB', flush=True)

threading.Thread(target=scenario, daemon=True).start()
root.mainloop()
