"""生成 README 用的演示图：.venv\\Scripts\\python.exe make_demo.py
用 Windows 语音合成说两句（英文结巴 + 中文改口），按实时速度喂给 Quill（不用麦克风、不真的粘贴、不写 history），
每 0.1 秒记一帧真实的底部浮窗，拼上一个假的输入框和「按住右 Alt」提示 → docs/demo.gif；再截主窗口 → docs/main.png。
要先关掉正在运行的 Quill（Stop-Process -Name pythonw）。"""
import ctypes, importlib.machinery, importlib.util, subprocess, sys, threading, time, wave
import tkinter as tk
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).parent; OUT = HERE / 'docs'; OUT.mkdir(exist_ok=True)
LINES = [('en', "I, I want to, um, I want to change the the color, no, the size of the button."),
         ('zh', '嗯，你能不能帮我把这个，这个PR merge一下，然后呃，跟大家说一声。')]

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

ctypes.windll.shcore.SetProcessDpiAwareness(1)
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts(); root = tk.Tk(); root.withdraw(); m.root = root; m.S = S = root.winfo_fpixels('1i') / 96; m.setup_fonts(root)
m.save_history = m.save_cfg = lambda *a: None
typed, latest, frames = [''], [None], []
m.paste = lambda t: typed.__setitem__(0, (typed[0] + '\n' + t).strip())  # 「粘贴」到假的输入框里
orig = m.ulw
def ulw(h, img, x, y): orig(h, img, x, y); latest[0] = img.copy()
m.ulw = ulw
audio = {lang: tts(lang, text, HERE / f'_demo_{lang}.wav') for lang, text in LINES}
m.cfg.update(lang='en', mode='fast', hotkey=0xA5)  # 演示用默认快捷键右 Alt
app = m.App(root); root.deiconify()
for t in (m.fix_worker, m.eng.load, m.eng.load_zh): threading.Thread(target=t, daemon=True).start()

def sample():  # 每 0.1 秒一帧：浮窗（看得见时）、输入框里的字、快捷键是不是按着、当前语言
    frames.append((latest[0] if app.overlay.visible else None, typed[0], m.eng.rec, m.cfg['lang']))
    root.after(100, sample)

def say(lang):
    if m.cfg['lang'] != lang: root.after(0, lambda: app.set_lang(lang)); time.sleep(1.2)
    a = np.concatenate([np.zeros(int(16000 * 0.3), np.float32), audio[lang], np.zeros(int(16000 * 0.4), np.float32)])
    m.eng.start('key'); t0 = time.time(); F = 480
    for i in range(0, len(a), F):
        m.eng.chunks.append(a[i:i + F, None].copy()); m.eng.peak = max(m.eng.peak, float(np.abs(a[i:i + F]).max()))
        time.sleep(max(0, t0 + (i + F) / 16000 - time.time()))
    m.eng.stop()
    while m.eng.busy: time.sleep(0.05)
    time.sleep(3.0)

def scenario():
    try:
        m.eng.loaded.wait(); m.eng.zh_loaded.wait(); time.sleep(3)
        root.after(0, sample); time.sleep(1.0)
        for lang, _ in LINES: say(lang)
        root.after(0, lambda: app.set_lang('en'))
        time.sleep(1.5)
    finally:
        root.after(0, finish)

def finish():
    app.notice_until = 0; app.set_state('ready')  # 截主窗口前清掉「显卡被占着」这类提示
    q = subprocess.Popen([sys.executable, str(HERE / 'shot.py'), 'Quill', str(OUT / 'main.png')])
    while q.poll() is None: root.update(); time.sleep(0.02)
    shot = Image.open(OUT / 'main.png').convert('RGBA'); mask = Image.new('L', shot.size, 0)  # 圆角外面透明（GitHub 深色主题下不露白角）
    ImageDraw.Draw(mask).rounded_rectangle((3, 3, shot.width - 4, shot.height - 4), round(48 * S) - 3, fill=255)  # 往里收 3 像素：裁掉窗口边缘的深色残边; shot.putalpha(mask); shot.save(OUT / 'main.png')
    build(); root.destroy()

def build():
    """拼 GIF：上面一个输入框（凸起卡片），中间「按住右 Alt」，下面是真实的底部浮窗。坐标按 100% 写，乘 S。"""
    ov = [f for f, *_ in frames if f is not None]
    oh = max(f.height for f in ov)
    W, top = 760, 262; H = top + oh / S + 8
    fe, fl, fk = m.pfont(16), m.pfont(11), m.pfont(12, 'b')
    out = []
    for k, (f, text, held, lang) in enumerate(frames):
        im = Image.new('RGB', (round(W * S), round(H * S)), m.BG); d = ImageDraw.Draw(im)
        m.raised(im, 40, 30, 680, 150, 24, d=7)
        d.text((64 * S, 48 * S), 'Message', font=fl, fill=m.SUB)
        lines = [l for t in text.split('\n') for l in m.flow([(t, m.TEXT, False)], fe, 620 * S, 3)]
        y = 78
        for ln in lines:
            d.text((64 * S, y * S), ''.join(t for _, t, _, _ in ln), font=fe, fill=m.TEXT); y += 28
        if (k // 5) % 2 == 0:  # 光标闪
            cx = 64 * S + (fe.getlength(''.join(t for _, t, _, _ in lines[-1])) + 3 if lines else 0)
            cy = (y - 28 if lines else 78) * S
            d.line((cx, cy + 3 * S, cx, cy + 22 * S), fill=m.INK, width=max(1, round(1.5 * S)))
        (m.inset if held else m.raised)(im, 40, 202, 130, 38, 19, d=4 if held else 5)
        d.text((105 * S, 221 * S), 'Right Alt', font=fk, fill=m.INK if held else '#555555', anchor='mm')
        d.text((186 * S, 221 * S), ('holding · speak ' + ('Chinese' if lang == 'zh' else 'English')) if held else 'hold to talk',
               font=fl, fill=m.SUB, anchor='lm')
        d.text((720 * S, 221 * S), 'input: ' + ('中文' if lang == 'zh' else 'English'), font=fl, fill=m.SUB, anchor='rm')
        if f is not None: im.paste(f, ((im.width - f.width) // 2, round(top * S)), f if f.mode == 'RGBA' else None)
        out.append(im.resize((W, round(H)), Image.LANCZOS))
    pal = out[len(out) // 2].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    gif = [o.quantize(palette=pal, dither=Image.Dither.NONE) for o in out]
    gif[0].save(OUT / 'demo.gif', save_all=True, append_images=gif[1:], duration=100, loop=0, optimize=True)
    print('demo.gif', len(gif), 'frames', round((OUT / 'demo.gif').stat().st_size / 1e6, 1), 'MB', flush=True)
    for lang, _ in LINES: (HERE / f'_demo_{lang}.wav').unlink(missing_ok=True)

threading.Thread(target=scenario, daemon=True).start()
root.mainloop()
