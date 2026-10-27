"""推送到公开仓库前的检查：.venv\\Scripts\\python.exe check_public.py
查 git 已跟踪的文件（也就是会被推出去的）里有没有不该公开的东西：
  1. 个人数据 / 本机文件（记录、设置、日志、录音、交接文档）
  2. 模型文件、大文件、许可不允许分发的字体（MiSans）；字体必须有对应的 OFL 许可文件
  3. 文本里带本机用户名的路径
通过返回 0，有问题返回 1（可以挂进 .git/hooks/pre-push：python check_public.py || exit 1）。"""
import re, subprocess, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from pathlib import Path
HERE = Path(__file__).parent

BAD_NAMES = {'history.txt', 'words.txt', 'words_scan.json', 'settings.json', 'run.log', 'selftest.out', 'handoff.md', 'claude.md'}
BAD_DIRS = ('recordings/', 'models/', 'e2e_out/', 'eval_out/', '.venv/', '.python/')
BAD_EXT = ('.onnx', '.bin', '.gguf', '.safetensors', '.pt', '.wav', '.mp3')
BAD_SUBSTR = ('misans',)  # 字体许可不允许分发
MAX_MB = 5
USER = '26' + '935'  # 本机用户名（拆开写，免得这个脚本自己被扫出来）
FONT_EXT = ('.ttf', '.otf', '.woff', '.woff2')

files = subprocess.run(['git', 'ls-files'], cwd=HERE, capture_output=True, text=True, encoding='utf-8').stdout.split('\n')
files = [f for f in files if f]
problems = []
has_ofl = any('ofl' in f.lower() for f in files)
for f in files:
    low, p = f.lower(), HERE / f
    if low.rsplit('/', 1)[-1] in BAD_NAMES: problems.append(f'{f}：个人数据 / 本机文件，不该公开')
    if low.startswith(BAD_DIRS): problems.append(f'{f}：本机目录，不该公开')
    if low.endswith(BAD_EXT): problems.append(f'{f}：模型 / 音频文件，不该公开')
    if any(s in low for s in BAD_SUBSTR): problems.append(f'{f}：许可不允许分发')
    if low.endswith(FONT_EXT) and not has_ofl: problems.append(f'{f}：字体没有对应的 OFL 许可文件')
    if p.is_file():
        if not low.endswith(FONT_EXT) and p.stat().st_size > MAX_MB * 1024 * 1024: problems.append(f'{f}：超过 {MAX_MB}MB')
        if not low.endswith(('.png', '.gif', '.ico', '.ttf', '.otf')):
            try: text = p.read_text(encoding='utf-8')
            except Exception: continue
            for n, line in enumerate(text.splitlines(), 1):
                if re.search(USER, line, re.I): problems.append(f'{f}:{n}：含本机用户名（{line.strip()[:60]}）')

if problems:
    print(f'发现 {len(problems)} 个问题：'); [print(' ✗', x) for x in problems]; sys.exit(1)
print(f'通过（检查了 {len(files)} 个已跟踪文件）')
