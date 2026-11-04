"""改完一键检查：.venv\\Scripts\\python.exe check.py [ui] [prompt] [all]
按 git 里改过的 .py / .pyw 挑着跑：语法 + pyflakes → 动了 app.pyw（或 all）就停掉 Quill 跑 e2e 英文 / 中文
→ ui：ui_test en / zh → prompt：eval_run all → check_public → 动了 app.pyw 就重启 Quill 看 run.log。
每步一行「通过 / 没过」，没过的打出输出的最后几行；全过 exit 0。"""
import json, os, re, subprocess, sys, time
from pathlib import Path
HERE = Path(__file__).parent; PY = str(HERE / '.venv' / 'Scripts' / 'python.exe')
ENV = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
args, ok = set(sys.argv[1:]), True

def run(cmd): r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding='utf-8', errors='replace', env=ENV); return r.returncode, r.stdout + r.stderr

def step(name, cmd, good=lambda c, o: c == 0):
    global ok
    c, out = run(cmd); passed = bool(good(c, out)); ok &= passed
    print(('通过  ' if passed else '没过  ') + name, flush=True)
    if not passed: print('\n'.join('      ' + l for l in out.strip().splitlines()[-15:]), flush=True)

def ps(cmd): return run(['powershell', '-NoProfile', '-Command', cmd])

def eval_ok():
    """eval_run 每次存一份 eval_out\\<时间>.json：每套用例没过的条数不比上一次跑这套时多（zhzh 有 3 条不送模型的同音字用例一直没过）。"""
    runs = [json.loads(f.read_text(encoding='utf-8'))['sets'] for f in sorted((HERE / 'eval_out').glob('2*.json'))]
    bad = lambda s: sum(bool(r.get('bad')) for r in s['rows'])
    for w, s in runs[-1].items():
        old = next((r[w] for r in reversed(runs[:-1]) if w in r), None)
        if old is not None and bad(s) > bad(old): return False
    return True

QUILL = f"Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | ? CommandLine -match '{re.escape(HERE.name)}'"
files = [l[3:].strip('"') for l in run(['git', 'status', '--porcelain'])[1].splitlines() if l[:2].strip() != 'D' and l.rstrip('"').endswith(('.py', '.pyw'))]
app = 'app.pyw' in files or 'all' in args
print('改过的：', ' '.join(files) or '（没有）', flush=True)
if files:
    step('语法', [PY, '-m', 'py_compile', *files])
    flakes = next((p for p in ([PY], ['py']) if run([*p, '-c', 'import pyflakes'])[0] == 0), None)  # venv 里没有就借系统 Python 的
    if flakes: step('pyflakes', [*flakes, '-m', 'pyflakes', *files])
    else: print('跳过  pyflakes（没装：py -m pip install pyflakes）')
if app:
    ps(f'{QUILL} | % {{ Stop-Process -Force -Id $_.ProcessId }}')  # e2e / ui_test 要独占麦克风和快捷键
    e2e = lambda c, o: c == 0 and 'release -> english' in o and '出错' not in o
    step('e2e 英文', [PY, 'e2e_test.py'], e2e)
    step('e2e 说中文出中文', [PY, 'e2e_test.py', 'zhzh'], e2e)
if 'ui' in args:
    for lg in ('en', 'zh'): step(f'ui_test {lg}（没对齐若是常用词窗口最底一条，看截图，常是误报）', [PY, 'ui_test.py', lg], lambda c, o: re.search(r'漏翻 0 处，没对齐 0 处', o))
if 'prompt' in args:
    step('eval_run all（和上次比，没过的检查没变多）', [PY, 'eval_run.py', 'all'], lambda c, o: c == 0 and eval_ok())
step('check_public', [PY, 'check_public.py'])
if app:
    root, t0 = str(HERE), time.time()
    ps(f'Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{{ CommandLine = \'"{root}\\.venv\\Scripts\\pythonw.exe" app.pyw\'; CurrentDirectory = \'{root}\' }}')  # 经 WMI 起：不在 Claude 的作业对象里
    log = ''
    for _ in range(30):
        time.sleep(1); f = HERE / 'run.log'  # 等新起的 Quill 清空重写 run.log（e2e 刚写过一份）
        log = f.read_text(encoding='utf-8', errors='replace') if f.exists() and f.stat().st_mtime > t0 else ''
        if '改错模型已预热' in log or '出错' in log: break
    passed = '识别模型已加载' in log and '出错' not in log; ok &= passed
    print(('通过  ' if passed else '没过  ') + '重启 Quill，run.log 没有「出错」')
    if not passed: print('\n'.join('      ' + l for l in log.strip().splitlines()[-15:]))
print('全部通过' if ok else '有没过的，见上面')
sys.exit(0 if ok else 1)
