r"""从「Quill 识别 → 你在 Claude 输入框里改 → 发出去」里自动收样本。
history.txt（粘出去的字）+ recordings\*.wav 和 ~/.claude/projects/**/*.jsonl 里你发出的消息按时间、相似度配对，
配上的录音拷进 eval_zh\auto\，定稿追加到 eval_zh\auto\labels.txt（cer_eval.py auto 用）。
不想要的行在前面加 #，别删（删了下次会再加回来）。报告和「错字 → 正字」统计在 eval_zh\auto\harvest.txt。"""
import json, re, shutil
from collections import Counter
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
HERE = Path(__file__).parent
OUT = HERE / 'eval_zh' / 'auto'
WINDOW = timedelta(minutes=11)  # 识别后多久内发出才算（history 只记到分钟，多给 1 分钟）
PUNCT = re.compile(r'[\s，。、？！；：,.?!;:…“”"（）()]')  # 只差标点的不进统计
MIN_RATIO, MAX_LEN = 0.8, 0.2   # 相似度下限；长度变化上限（大段增删的不要）

def hist():
    out = []
    for b in (HERE / 'history.txt').read_text(encoding='utf-8').split('\n\n'):
        ls = b.strip().splitlines()
        if not ls or not re.fullmatch(r'\d{4}-\d\d-\d\d \d\d:\d\d', ls[0]): continue
        f = {l[0]: l[2:].strip() for l in ls[1:] if len(l) > 1 and l[1] == '：'}
        w = HERE / 'recordings' / f'{f.get("录", "-")}.wav'  # 录：app 写记录时带的录音名（没有就不配录音）
        out.append((datetime.strptime(ls[0], '%Y-%m-%d %H:%M'), f.get('译') or f.get('改') or f.get('说', ''), w if w.exists() else None))
    return out

def msgs(since):
    out = {}
    for f in (Path.home() / '.claude' / 'projects').glob('*/*.jsonl'):
        if datetime.fromtimestamp(f.stat().st_mtime) < since: continue
        for line in f.open(encoding='utf-8', errors='ignore'):
            if '"queue-operation"' not in line and '"type":"user"' not in line: continue
            try: d = json.loads(line)
            except ValueError: continue
            c = d.get('content') if d.get('type') == 'queue-operation' else (d.get('message') or {}).get('content')
            if isinstance(c, list): c = '\n'.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
            if not isinstance(c, str) or not c.strip() or c.lstrip().startswith('<'): continue
            t = datetime.fromisoformat(d['timestamp'].replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
            out.setdefault(c, t)
    return [(t, c) for c, t in out.items() if t >= since]

def align(p, m):
    """在消息 m 里找和 p 对应的那一段：从最长的公共片段往两边连，跳得太远的片段不要。"""
    bl = [b for b in SequenceMatcher(None, p, m, autojunk=False).get_matching_blocks() if b.size >= 2]
    if not bl: return '', 0
    k = max(range(len(bl)), key=lambda i: bl[i].size); i = j = k
    while i > 0 and bl[i].b - bl[i - 1].b - bl[i - 1].size <= bl[i].a - bl[i - 1].a - bl[i - 1].size + 10: i -= 1
    while j < len(bl) - 1 and bl[j + 1].b - bl[j].b - bl[j].size <= bl[j + 1].a - bl[j].a - bl[j].size + 10: j += 1
    s = max(0, bl[i].b - bl[i].a); e = min(len(m), bl[j].b + bl[j].size + len(p) - bl[j].a - bl[j].size)
    span = m[s:e].strip()
    if p[:1] and not PUNCT.match(p[0]): span = span.lstrip('，。、？！；：,.?!;: ')  # 开头认错的字被删了，会多带进前一句的标点
    return span, SequenceMatcher(None, p, span, autojunk=False).ratio()

h = hist()
if not h: raise SystemExit('history.txt 里没有记录')
ms = msgs(h[0][0])
OUT.mkdir(parents=True, exist_ok=True)
lab = OUT / 'labels.txt'
if not lab.exists(): lab.write_text('# 每行：录音名<Tab>你发出去的定稿（harvest.py 自动配的）。不要的行前面加 #，别删。\n', encoding='utf-8')
have = lab.read_text(encoding='utf-8')
rep, subs, new, nch = [], Counter(), [], 0
for t, p, w in h:
    if len(p) < 4: continue
    cand = [align(p, m) for mt, m in ms if t <= mt <= t + WINDOW]
    span, r = max(cand, key=lambda x: x[1], default=('', 0))
    if r < MIN_RATIO or abs(len(span) - len(p)) > MAX_LEN * len(p): continue
    changed = span != p; nch += changed
    if changed:
        for op, a1, a2, b1, b2 in SequenceMatcher(None, p, span, autojunk=False).get_opcodes():
            if op != 'equal' and PUNCT.sub('', p[a1:a2] + span[b1:b2]): subs[f'{p[a1:a2] or "∅"} → {span[b1:b2] or "∅"}'] += 1
    rep.append(f'{t:%m-%d %H:%M}  {r:.2f}  {"改过" if changed else "没改"}  {w.stem if w else "（无录音）"}\n  粘：{p}\n  发：{span}')
    if w and w.stem not in have:
        shutil.copy2(w, OUT / w.name); new.append(f'{w.stem}\t{span}')
if new:
    with lab.open('a', encoding='utf-8') as f: f.write(''.join(l + '\n' for l in new))
top = [f'{n}×  {k}' for k, n in subs.most_common(40)]
(OUT / 'harvest.txt').write_text(f'配上 {len(rep)} 条（改过 {nch}），新加录音 {len(new)} 段\n\n'
                                 '== 错字 → 正字（出现次数）==\n' + '\n'.join(top) + '\n\n== 配对 ==\n' + '\n'.join(rep) + '\n', encoding='utf-8')
print(f'配上 {len(rep)} 条（改过 {nch}），新加录音 {len(new)} 段 → {OUT / "harvest.txt"}')
