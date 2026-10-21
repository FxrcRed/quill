# 手动扫一遍全部 history.txt，找可能听错的词（工具名、人名、中英混说的词），列出来。只读不改。
# Quill 自己每天闲着时也会扫新记录，说过 ≥2 次的放在记录窗口的「常用词」卡片里（app.pyw 的 find_candidates / word_sugs）。
# 用法：.venv\Scripts\python.exe find_words.py        约 1~2 分钟（本机 qwen3:8b）
import importlib.machinery, importlib.util, re, sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_loader('app', importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

said = [t for _, t in m.said_lines(m.hist_blocks())]
known = m.glossary_known()

# 一：模型挑的
hits, eg, done = Counter(), {}, 0
for part in m.scan_batches(said):
    try: found = m.find_candidates(part)
    except Exception as e: found = []; print('\n这一批失败：', repr(e))
    for h, c, src in found:
        if (h.lower(), c) not in hits: hits[h.lower(), c] = sum(len(m.term_rx(h).findall(s)) for s in said); eg[h.lower(), c] = src
    done += len(part); print(f'\r扫了 {done}/{len(said)} 句', end='', flush=True)
print('\n\n模型觉得听错的（听到 → 应该是，说过几次，例句）：')
for (h, c), n in hits.most_common(40):
    print(f'  {h} → {c}  ×{n}  「{eg[h, c][:40]}」')

# 二：中文句子里夹着的英文词（中英混说最容易听错），按次数
mix, mix_eg = Counter(), {}
for s in said:
    if not re.search(r'[一-鿿]', s): continue
    for w in re.findall(r'[A-Za-z][A-Za-z0-9]*(?:\s+[A-Za-z][A-Za-z0-9]*)*', s):
        if w.lower() not in known: mix[w] += 1; mix_eg.setdefault(w, s)
print('\n中文里夹着的英文（次数，例句）：')
for w, n in mix.most_common(30):
    print(f'  {w}  ×{n}  「{mix_eg[w][:40]}」')
