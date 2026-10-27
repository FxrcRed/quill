"""固定评测集：改错结果自动打分 + 和上一次对比。只测文字，不用麦克风，可以和 Quill 同时开着。
.venv\\Scripts\\python.exe eval_run.py [en|zh|zhzh|all]     （默认 all）
用例复用 fix_test.py 的造句（别放 history.txt 原话）；这里给每条加检查：
  must     = 结果里必须出现的写法（正则，不分大小写）
  must_not = 结果里不能出现的写法（口头禅、没删的重复、没改的错词）
没写检查的用例只记结果和耗时，下次对比时看有没有变。
每次运行存到 eval_out\\时间.json，和上一次比：通过数、耗时、哪些句子的输出变了。"""
import json, re, sys, threading, time
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')
import fix_test as ft
m, HERE = ft.m, ft.HERE
_opts = m.llm_opts
m.llm_opts = lambda: (lambda o, g: ({**o, 'seed': 42}, g))(*_opts())  # 只在评测里固定随机种子（温度仍是 app 的 0.2），同一输入每次结果一样

CHECKS = {
    'en': [  # 和 fix_test.CASES 一一对应
        {'must': [r'make today', r'right top|top right'], 'must_not': [r'Change Today']},
        {'must': [r'Shortcut', r'have the same distance'], 'must_not': [r'\bum\b', r'Has um']},
        {'must': [r'\bwas\b', r'ten centimeters|10 centimeters'], 'must_not': [r'\bis ten\b', r'\byes\b']},
        {'must': [r'(little|small) arrow', r'back to the top'], 'must_not': [r'\ba a\b', r'\buh\b', r'I can, I can']},
        {'must': [r'size of the button'], 'must_not': [r'\bcolor\b', r'\bno, I mean', r'the the']},
        {'must': [r'late at night', r'stressful'], 'must_not': [r'Casher', r'in night']},
        {'must': [r'makes me very tired'], 'must_not': [r'old school make me The']},
        {'must': [r'tell me how to fix this bug'], 'must_not': [r'can you, can you', r'how to, how to']},
        {'must': [r'Quill'], 'must_not': [r'Quail', r'\.\s+Is the app\.']},
        {'must': [r'click the button']},
    ],
    'zh': [{} for _ in ft.ZH_CASES],  # 说中文 → 出英文：只记结果（还没有可靠的断言）
    'zhzh': [  # 和 fix_test.ZHZH_CASES 一一对应，只给有把握的写检查
        {'must': [r'没错|不对'], 'must_not': [r'没措']},
        {}, {}, {},
        {'must_not': [r'。。']},
        {'must_not': [r'okK']},
        {}, {}, {'must_not': [r'做坐']}, {}, {}, {}, {},
        {'must': [r'四点'], 'must_not': [r'三点']},
    ],
}


def cases(which):
    return {'en': ft.CASES, 'zh': ft.ZH_CASES, 'zhzh': ft.ZHZH_CASES}[which]


def one(segs, lang, out):
    job, buf = m.Job(False, 'fast', lang, out), ''
    for s in segs[:-1]:  # 和 fix_test.run / Job.update 一样的喂法
        buf = (buf + ' ' + s).strip()
        if m.sentence_end(buf): job.add(buf); buf = ''
    job.add((buf + ' ' + segs[-1]).strip()); job.whole()
    join = m.zh_join if job.seg_lang == 'zhzh' else m.join_parts
    return join([s.get('fixed') for s in job.segs])


def grade(res, chk):
    bad = [f'缺 {p}' for p in chk.get('must', []) if not re.search(p, res, re.I)]
    bad += [f'不该有 {p}' for p in chk.get('must_not', []) if re.search(p, res, re.I)]
    return bad


def run(which):
    lang, out = ('zh', 'zh') if which == 'zhzh' else (which, 'en')
    rows, t0 = [], time.time()
    for i, segs in enumerate(cases(which)):
        t1 = time.time(); res = one(segs, lang, out); dt = time.time() - t1
        chk = CHECKS[which][i] if i < len(CHECKS[which]) else {}
        rows.append({'i': i, 'say': ' | '.join(segs), 'out': res, 'sec': round(dt, 1), 'checked': bool(chk), 'bad': grade(res, chk)})
    return rows, time.time() - t0


if __name__ == '__main__':
    threading.Thread(target=m.fix_worker, daemon=True).start()
    a = sys.argv[1] if len(sys.argv) > 1 else 'all'
    sets = ['en', 'zh', 'zhzh'] if a == 'all' else [a]
    OUT = HERE / 'eval_out'; OUT.mkdir(exist_ok=True)
    prev_files = sorted(OUT.glob('*.json'))
    prev = json.loads(prev_files[-1].read_text(encoding='utf-8')) if prev_files else {}
    report = {'model': m.LLM_MODEL, 'sets': {}}
    for w in sets:
        rows, total = run(w); report['sets'][w] = {'rows': rows, 'sec': round(total, 1)}
        checked = [r for r in rows if r['checked']]; ok = [r for r in checked if not r['bad']]
        print(f'\n== {w}：检查通过 {len(ok)}/{len(checked)}，共 {len(rows)} 条，总耗时 {total:.1f}s，平均 {total / len(rows):.1f}s/条')
        old = {r['i']: r for r in prev.get('sets', {}).get(w, {}).get('rows', [])}
        for r in rows:
            for b in r['bad']: print(f'  ✗ #{r["i"]} {b}\n      说：{r["say"][:60]}\n      改：{r["out"][:100]}')
            if r['i'] in old and old[r['i']]['out'] != r['out']:
                print(f'  ~ #{r["i"]} 和上次不同\n      上次：{old[r["i"]]["out"][:100]}\n      这次：{r["out"][:100]}')
    f = OUT / (time.strftime('%Y%m%d-%H%M%S') + '.json')
    f.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已存 {f.name}（模型 {m.LLM_MODEL}；上次：{prev_files[-1].name if prev_files else "无"}）')
