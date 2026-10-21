r"""用法：.venv\Scripts\python.exe words_test.py <项目目录> <截图.png>。常用词窗口测试：WORDS_FILE 指到临时文件，不动真的 words.txt。增 / 并 / 改 / 删后检查文件，再截一张窗口图。"""
import ctypes, importlib.machinery, importlib.util, sys, tempfile, time
import tkinter as tk
from pathlib import Path
from PIL import ImageGrab
ctypes.windll.shcore.SetProcessDpiAwareness(1)
HERE = Path(sys.argv[1]); OUT = Path(sys.argv[2])
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
m.load_fonts()
root = tk.Tk(); root.withdraw(); m.root = root; m.S = root.winfo_fpixels('1i') / 96
m.setup_fonts(root)
tmp = Path(tempfile.mkdtemp()) / 'words.txt'; m.WORDS_FILE = tmp
tmp.write_text(m.WORDS_HEAD + '# 我自己的注释\nGitHub\nClaude： cloud、 克劳德\nOllama: 奥拉玛?\n', encoding='utf-8')

class FakeApp:
    def __init__(self):
        self.root = root; root.geometry('400x600+1400+40'); root.deiconify(); self.chrome = m.Chrome(root, R=48); self.changed = 0
    def words_changed(self):
        self.changed += 1; self.w.build(keep=True)
app = FakeApp(); w = app.w = m.Words(app)
ok = lambda c, t: print(('通过 ' if c else '失败 ') + t)
lines = lambda: tmp.read_text(encoding='utf-8').splitlines()
ok([p[0] for l in lines() if (p := m.parse_word(l))] == ['GitHub', 'Claude', 'Ollama'], '解析：中文冒号 / 顿号 / 旧 ?')
w.ent.insert(0, 'Mirasim: 米拉西姆'); w.submit(); ok(lines()[-1] == 'Mirasim: 米拉西姆', '加入新词')
w.ent.insert(0, 'claude: 可劳德, cloud'); w.submit(); ok('Claude: cloud, 克劳德, 可劳德' in lines(), '已有的词：听错写法并进那行、去重')
w.edit('GitHub'); ok(w.ent.get() == 'GitHub' and w.editing, '改：放进输入框')
w.ent.delete(0, 'end'); w.ent.insert(0, 'GitHub: 吉特哈布'); w.submit(); ok('GitHub: 吉特哈布' in lines() and 'GitHub' not in lines(), '改：原地换掉那行')
w.delete('Ollama: 奥拉玛?'); ok('Ollama: 奥拉玛?' in lines() and w.arm_del, '删：第一下只变红')
w.delete('Ollama: 奥拉玛?'); ok('Ollama: 奥拉玛?' not in lines(), '删：第二下删掉')
w.delete('GitHub: 吉特哈布'); w.delete('Mirasim: 米拉西姆'); first = w.arm_job; w.disarm()
ok(w.arm_del is None and w.arm_job is None and first, '删：换一行再点，前一个计时作废（不会提前撤红）')
w.save(None, 'Quil: 奎尔'); w.edit('Quil: 奎尔'); w.ent.delete(0, 'end'); w.ent.insert(0, 'mirasim: 奎尔'); w.submit()
ok('Quil: 奎尔' not in lines() and 'Mirasim: 米拉西姆, 奎尔' in lines() and sum(l.lower().startswith('mirasim') for l in lines()) == 1, '改成已有的词：并进那行，不出两行')
ok('# 我自己的注释' in lines() and lines()[0].startswith('# 常用词'), '注释行保留')
ok([t for t in m.glossary()[0]] == ['GitHub', 'Claude', 'Mirasim'], 'glossary 重读到新词表')
for i in range(8): w.save(None, f'Word{i}: 听错{i}, 另一个很长很长很长很长的听错写法{i}')
w.edit('Mirasim: 米拉西姆'); w.delete('Word7: 听错7, 另一个很长很长很长很长的听错写法7')
m.place_side(app, w.w, w.W); w.w.deiconify(); w.w.chrome.sync(); w.w.lift()
for _ in range(20): root.update(); m.sync_chromes(); time.sleep(0.03)
u = ctypes.windll.user32; r = ctypes.wintypes.RECT() if hasattr(ctypes, 'wintypes') else None
import ctypes.wintypes as W
r = W.RECT(); u.GetWindowRect(ctypes.c_void_p(w.w.chrome.hwnd), ctypes.byref(r))
import subprocess; pr = subprocess.Popen([sys.executable, str(HERE / 'shot.py'), '常用词', str(OUT)])
while pr.poll() is None: root.update(); time.sleep(0.02)
print('图', OUT, r.right - r.left, r.bottom - r.top, '改了', app.changed, '次')
