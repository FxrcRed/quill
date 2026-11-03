"""中文识别字错率（CER）：eval_zh\*.wav 用 Whisper 认一遍，和 eval_zh\labels.txt（用户校对过的正确文字）比。
.venv\Scripts\python.exe cer_eval.py [ctx]   （ctx：把上一句的正确文字也放进提示，模拟「带上文」）
解码参数开关：temp0 / temp02 / beam1 / vad / nots；noprompt 不带提示；后处理默认和 app 一致（apply_glossary + zh_tidy），nopy / notidy 关掉。
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
import fix_test as ft  # 借 app.pyw 的 pinyin_fix / 改错
if fix:
    import threading
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
kw = dict(language='zh', beam_size=5, condition_on_previous_text=False)  # 默认和 app.pyw 中文 rec() 一致；下面几个开关用来试解码参数
if 'temp0' in sys.argv: kw['temperature'] = 0.0  # 关掉温度回退（默认 0→1.0 六档，置信度低时最多重解码 6 次）
if 'temp02' in sys.argv: kw['temperature'] = (0.0, 0.2)
if 'beam1' in sys.argv: kw['beam_size'] = 1
if 'vad' in sys.argv: kw['vad_filter'] = True
if 'nots' in sys.argv: kw['without_timestamps'] = True
errs = chars = 0; lines = []; prev = ''; secs = []
for name, ref in labels:
    prompt = base + (prev if ctx else '')
    a = load(D / f'{name}.wav'); t0 = time.time()
    segs, _ = wh.transcribe(a, initial_prompt=None if 'noprompt' in sys.argv else prompt, **kw)  # noprompt：不带句子 + 词表提示
    hyp = ''.join(s.text for s in segs).strip(); secs.append(time.time() - t0)  # segs 是生成器，取完才算完
    if 'nopy' in sys.argv: pass  # nopy：不换词
    elif 'half1' in sys.argv or 'half2' in sys.argv: hyp = ft.m.pinyin_fix(hyp, [t for t in _t if re.fullmatch(r'[一-鿿]{2,}', t)])
    else: hyp = ft.m.apply_glossary(ft.m.zh_punct(hyp))  # 和 app 一样：标点换全角 → 词表替换 + 拼音换回中文词
    if 'notidy' not in sys.argv: hyp = ft.m.zh_tidy(hyp)  # 和 app「说中文 → 出中文」一样删口头禅 / 重复
    if fix: hyp = ft.one([hyp], 'zh', 'zh') or hyp
    e, n = dist(norm(hyp), norm(ref)), len(norm(ref)); errs += e; chars += n; prev = ref
    lines.append(f'{name}\t{e}/{n}\t{hyp}' + ('' if e == 0 else f'\n\t\t对：{ref}'))
cer = errs / max(chars, 1)
flags = ' '.join(a for a in sys.argv[1:] if a != 'auto')
head = (f'CER {cer:.2%}（{errs}/{chars} 字，{len(labels)} 段{"，带上文" if ctx else ""}{"，改错后" if fix else ""}）'
        f' Whisper 共 {sum(secs):.1f}s 最慢 {max(secs, default=0):.2f}s' + (f' [{flags}]' if flags else ''))
outd = HERE / 'eval_out'; outd.mkdir(exist_ok=True)
pre = 'cerauto_' if 'auto' in sys.argv else 'cer_'  # 两套评测集各自和上一次比
old = sorted(outd.glob(pre + '*.txt'))
(outd / f'{pre}{time.strftime("%m%d-%H%M%S")}.txt').write_text(head + '\n' + '\n'.join(lines) + '\n', encoding='utf-8')
print('\n'.join(l for l in lines if '对：' in l))
print(head + (f'；上一次 {old[-1].read_text(encoding="utf-8").splitlines()[0]}' if old else ''))
