"""中文识别字错率（CER）：eval_zh\*.wav 用 Whisper 认一遍，和 eval_zh\labels.txt（用户校对过的正确文字）比。
.venv\Scripts\python.exe cer_eval.py [ctx]   （ctx：把上一句的正确文字也放进提示，模拟「带上文」）
比较时去掉标点、空格，英文转小写。结果存 eval_out\cer_<时间>.txt，并和上一次比。"""
import os, re, sys, time, wave
from pathlib import Path
import numpy as np
HERE = Path(__file__).parent
cuda = Path(os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12'))  # 借 Ollama 自带的 cuBLAS
if cuda.exists(): os.add_dll_directory(str(cuda)); os.environ['PATH'] = str(cuda) + os.pathsep + os.environ['PATH']

def load(p):
    with wave.open(str(p)) as w: return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768

def terms():
    out = []
    for line in (HERE / 'words.txt').read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line and not line.startswith('#'): out.append(line.split(':')[0].strip())
    return out

def norm(t): return re.sub(r'[\s\W_]+', '', t).lower()

def dist(a, b):
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1): p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (x != y))
    return d[-1]

labels = []
D = HERE / 'eval_zh' / ('auto' if 'auto' in sys.argv else '')  # auto：harvest.py 自动收的样本
for line in (D / 'labels.txt').read_text(encoding='utf-8').splitlines():
    if line.strip() and not line.startswith('#') and '\t' in line: labels.append(line.split('\t', 1))
ctx, fix = 'ctx' in sys.argv, 'fix' in sys.argv  # fix：识别结果再走一遍「说中文 → 出中文」的改错（和 app 一样），算改错后的字错率
if fix:
    import threading, fix_test as ft
    _opts = ft.m.llm_opts
    ft.m.llm_opts = lambda: (lambda o, g: ({**o, 'seed': 42}, g))(*_opts())  # 同 eval_run：固定随机种子
    threading.Thread(target=ft.m.fix_worker, daemon=True).start()
    if 'nosmart' not in sys.argv: ft.m.zh_needs_llm = lambda t: True  # 每句都送模型（nosmart：和 app 一样只送带改口的）
from faster_whisper import WhisperModel
wh = WhisperModel(str(HERE / 'models' / 'whisper'), device='cuda', compute_type='int8_float16')
_t = terms(); _zh = [t for t in _t if not t.isascii()]  # half1 / half2：中文词只放前一半 / 后一半，用来看词表的水分
if 'half1' in sys.argv: _t = [t for t in _t if t.isascii() or t in _zh[:len(_zh) // 2]]
if 'half2' in sys.argv: _t = [t for t in _t if t.isascii() or t in _zh[len(_zh) // 2:]]
_hdr, _lst = '以下是普通话的句子，使用简体中文。', ('常用词：' + '、'.join(_t) + '。' if _t else '')
base = (_lst + _hdr if 'front' in sys.argv else _lst if 'nohdr' in sys.argv else _hdr + _lst)  # 默认和 app.pyw 里中文 rec() 的提示一致
errs = chars = 0; lines = []; prev = ''
for name, ref in labels:
    prompt = base + (prev if ctx else '')
    segs, _ = wh.transcribe(load(D / f'{name}.wav'), language='zh', beam_size=5, condition_on_previous_text=False, initial_prompt=prompt)
    hyp = ''.join(s.text for s in segs).strip()
    if fix: hyp = ft.one([hyp], 'zh', 'zh') or hyp
    e, n = dist(norm(hyp), norm(ref)), len(norm(ref)); errs += e; chars += n; prev = ref
    lines.append(f'{name}\t{e}/{n}\t{hyp}' + ('' if e == 0 else f'\n\t\t对：{ref}'))
cer = errs / max(chars, 1)
head = f'CER {cer:.2%}（{errs}/{chars} 字，{len(labels)} 段{"，带上文" if ctx else ""}{"，改错后" if fix else ""}）'
outd = HERE / 'eval_out'; outd.mkdir(exist_ok=True)
pre = 'cerauto_' if 'auto' in sys.argv else 'cer_'  # 两套评测集各自和上一次比
old = sorted(outd.glob(pre + '*.txt'))
(outd / f'{pre}{time.strftime("%m%d-%H%M%S")}.txt').write_text(head + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
print('\n'.join(l for l in lines if '对：' in l))
print(head + (f'；上一次 {old[-1].read_text(encoding="utf-8").splitlines()[0]}' if old else ''))
