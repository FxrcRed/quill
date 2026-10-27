"""增量 Whisper 离线评估：recordings\\*.wav 整段认 vs 按停顿切块、每块带上文提示认，比较文字是否一致、松开后要认的尾巴多长。
.venv\\Scripts\\python.exe chunk_compare.py [最短块秒数=4] [停顿秒数=0.7]"""
import importlib.machinery, importlib.util, os, re, sys, time, wave
from pathlib import Path
import numpy as np
HERE = Path(__file__).parent
cuda = Path(os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12'))
if cuda.exists(): os.add_dll_directory(str(cuda)); os.environ['PATH'] = str(cuda) + os.pathsep + os.environ['PATH']
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)
MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 4.0; GAP = float(sys.argv[2]) if len(sys.argv) > 2 else 0.7
from faster_whisper import WhisperModel
wh = WhisperModel(str(HERE / 'models' / 'whisper'), device='cuda', compute_type='int8_float16')
wh.transcribe(np.zeros(16000, np.float32), language='en')

def load(p):
    with wave.open(str(p)) as w: return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768

def rec(a, prompt=None):
    segs, _ = wh.transcribe(a, condition_on_previous_text=False, initial_prompt=prompt, language='en', beam_size=1)
    return ' '.join(s.text.strip() for s in segs).strip()

def norm(t): return re.findall(r"[a-z0-9']+", t.lower())
def cuts(a):
    pos, out = 0, []
    for L in range(8000, len(a) + 1, 8000):  # 每 0.5 秒看一次，和 Job.update 一样
        c = m.find_pause(a[:L], pos, min_speech=MIN, gap=GAP)
        if c: out.append(c); pos = c
    return out

import difflib
same = diff = 0; tw = tt = 0.0; tails = []
for f in sorted((HERE / 'recordings').glob('*.wav')):
    a = load(f); t = time.time(); whole = rec(a); tw += time.time() - t
    pts = [0] + cuts(a) + [len(a)]; parts = []; ttail = 0
    for i in range(len(pts) - 1):
        prompt = (' '.join(parts))[-200:] or None
        t = time.time(); parts.append(rec(a[pts[i]:pts[i + 1]], prompt)); ttail = time.time() - t
    tt += ttail; tails.append((len(a) / 16000, len(pts) - 2, (pts[-1] - pts[-2]) / 16000, ttail))
    ch = ' '.join(parts)
    if norm(ch) == norm(whole): same += 1
    else:
        diff += 1; w1, w2 = norm(whole), norm(ch)
        sm = difflib.SequenceMatcher(None, w1, w2)
        d = [(' '.join(w1[i1:i2]), ' '.join(w2[j1:j2])) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal']
        print(f'{f.name} {len(a) / 16000:.0f}s 块{len(pts) - 1}: {d[:6]}')
n = len(tails)
print(f'一致 {same} / 不同 {diff}；整段平均 {tw / n:.2f}s，只认最后一块平均 {tt / n:.2f}s；平均录音 {sum(x[0] for x in tails) / n:.1f}s，平均最后一块 {sum(x[2] for x in tails) / n:.1f}s；没切开的 {sum(1 for x in tails if x[1] == 0)}')
