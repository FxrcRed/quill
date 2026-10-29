"""端到端测试（不用真麦克风、不会粘贴到别的程序）：.venv\\Scripts\\python.exe e2e_test.py
把 test.wav（说两遍、中间停 0.9 秒）按实时速度喂进去跑一句，
浮窗每个阶段的样子存到 e2e_out\\ov_*.png，耗时看输出和 run.log。不写 history.txt，结束后恢复剪贴板。
要先关掉正在运行的 Quill（Stop-Process -Force -Id (Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | ? CommandLine -match 'voice-input').ProcessId），否则两个都在用麦克风和快捷键。"""
import ctypes, importlib.machinery, importlib.util, sys, threading, time, wave
import tkinter as tk
from pathlib import Path
import numpy as np

HERE = Path(__file__).parent; OUT = HERE / 'e2e_out'; OUT.mkdir(exist_ok=True)
ctypes.windll.shcore.SetProcessDpiAwareness(1)
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts()
root = tk.Tk(); root.withdraw(); m.root = root; m.S = root.winfo_fpixels('1i') / 96
m.setup_fonts(root)
m.save_history = lambda *a: None
latest = [None]; orig = m.ulw
def ulw(h, img, x, y): orig(h, img, x, y); latest[0] = img
m.ulw = ulw
try: clip = root.clipboard_get()
except Exception: clip = None
LANG = 'zh' if {'zh', 'zhzh'} & set(sys.argv) else 'en'; m.cfg['lang'] = LANG  # e2e_test.py zh：说中文（test_zh.wav）
ZHZH = 'zhzh' in sys.argv; m.cfg['zh_out'] = 'zh' if ZHZH else 'en'  # e2e_test.py zhzh：说中文 → 出中文
app = m.App(root); root.deiconify()
threading.Thread(target=m.fix_worker, daemon=True).start(); threading.Thread(target=m.eng.load, daemon=True).start()

with wave.open(str(HERE / ('test_zh.wav' if LANG == 'zh' else 'test.wav'))) as w:
    a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
audio = np.concatenate([a, (np.random.randn(int(16000 * 0.9)) * 0.001).astype(np.float32), a])

def snap(name):
    time.sleep(0.15)
    if latest[0]: latest[0].save(OUT / f'ov_{"zhzh" if ZHZH else LANG}_{name}.png')

def say(tag):
    m.eng.start('mouse'); t0 = time.time(); F = 480
    for i in range(0, len(audio), F):
        m.eng.chunks.append(audio[i:i + F, None].copy()); m.eng.peak = max(m.eng.peak, float(np.abs(audio[i:i + F]).max()))
        if abs(time.time() - t0 - 4.0) < 0.016: snap(f'{tag}_live1')
        if abs(time.time() - t0 - 10.0) < 0.016: snap(f'{tag}_live2')
        time.sleep(max(0, t0 + (i + F) / 16000 - time.time()))
    m.eng.stop(); tr = time.time(); time.sleep(0.3); snap(f'{tag}_think')
    while m.eng.busy: time.sleep(0.05)
    print(f'{tag}: release -> english {time.time() - tr:.2f}s', flush=True); snap(f'{tag}_result')
    time.sleep(2.5); snap(f'{tag}_zh')

def scenario():
    try:
        m.eng.loaded.wait(); time.sleep(4)
        say('fast')
    finally:
        def end():
            if clip is not None: root.clipboard_clear(); root.clipboard_append(clip); root.update()
            root.destroy()
        root.after(500, end)

threading.Thread(target=scenario, daemon=True).start()
root.mainloop()
