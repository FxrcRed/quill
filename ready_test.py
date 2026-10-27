r"""用法：.venv\Scripts\python.exe ready_test.py <项目目录> <截图前缀>。「准备情况」窗口 + 状态判断的测试：不启动 Quill 本体（用户的 Quill 开着也行），
假装各种缺东西的情形（eng.wh_state、Ollama 状态、模型文件）检查 readiness() 的结果，再把窗口截成 <前缀>_<情形>.png。"""
import importlib.machinery, importlib.util, ctypes, subprocess, sys, time
import tkinter as tk
from pathlib import Path
ctypes.windll.shcore.SetProcessDpiAwareness(1)
HERE = Path(sys.argv[1]); OUT = sys.argv[2]
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts()
root = tk.Tk(); root.withdraw(); m.root = root; m.S = root.winfo_fpixels('1i') / 96
m.setup_fonts(root)

class FakeApp:
    ol_last = 'ok'
    def __init__(self):
        self.root = root; root.geometry('400x600+1400+40'); root.deiconify(); self.chrome = m.Chrome(root, R=48)
app = FakeApp()
ok = lambda c, t: print(('通过 ' if c else '失败 ') + t)
m.nvidia_gpu = lambda: 'NVIDIA GeForce RTX 4070 Laptop GPU'
m.eng.asr, m.eng.load_error = object(), ''
levels = lambda ol: [r[0] for r in m.readiness(ol)]

m.eng.wh_state = 'ok'
ok(levels('ok') == ['ok', 'ok', 'ok', 'ok'], '全齐：四行都是绿')
m.eng.wh_state = 'nogpu'; m.nvidia_gpu = lambda: ''; m.LLM_GPU_LAYERS = 0
ok(levels('ok')[2:] == ['info', 'info'] and m.readiness('ok')[2][2] is None, '没有 N 卡：灰色说明、没有按钮、不算缺东西')
m.nvidia_gpu = lambda: 'GPU'; m.LLM_GPU_LAYERS = -1
m.eng.wh_state = 'nolib'; ok(m.readiness('ok')[2][:1] + m.readiness('ok')[2][2:] == ('warn', '修复'), '缺 cuBLAS：橙 + 修复')
m.eng.wh_state = 'nomodel'; ok(m.readiness('ok')[2][2] == '下载', '没下 Whisper 模型：下载')
m.eng.wh_state = 'ok'
ok(m.readiness('no_model')[1][:1] + m.readiness('no_model')[1][2:] == ('fail', '下载'), '没有 qwen 模型：红 + 下载')
ok(m.readiness('down')[1][0] == 'warn' and m.readiness('down')[1][2] is None, 'Ollama 没开：橙、无按钮（会自动启动）')
m.eng.load_error = 'x'; ok(m.readiness('ok')[0][0] == 'fail', '识别模型载入失败：红'); m.eng.load_error = ''
real = m.have_models; m.have_models = lambda sub, *f: sub != 'parakeet'
ok(m.readiness('ok')[0][:1] + m.readiness('ok')[0][2:] == ('fail', '下载'), '缺 Parakeet 文件：红 + 下载')
m.have_models = lambda sub, *f: sub != 'sensevoice'; ok(m.readiness('ok')[0][0] == 'warn', '只缺 SenseVoice：橙'); m.have_models = real
real_which, real_exists = m.shutil.which, m.os.path.exists; m.shutil.which = lambda n: None; m.os.path.exists = (lambda e: (lambda p: False if 'ollama.exe' in str(p).lower() else e(p)))(m.os.path.exists)
ok(m.readiness('down')[1][:1] + m.readiness('down')[1][2:] == ('fail', '安装'), '没装 Ollama：红 + 安装')
m.shutil.which, m.os.path.exists = real_which, real_exists

w = m.Ready(app)
def shot(tag):
    w.refresh(True)
    m.place_side(app, w.w, w.W); w.w.deiconify(); w.w.chrome.sync(); w.w.lift()
    for _ in range(15): root.update(); m.sync_chromes(); time.sleep(0.03)
    pr = subprocess.Popen([sys.executable, str(HERE / 'shot.py'), '准备情况', f'{OUT}_{tag}.png'])
    while pr.poll() is None: root.update(); time.sleep(0.02)
m.eng.wh_state = 'nolib'; app.ol_last = 'no_model'; shot('lack')
m.eng.wh_state = 'nogpu'; m.nvidia_gpu = lambda: ''; app.ol_last = 'ok'; shot('nogpu')
print('完成')
