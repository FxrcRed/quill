"""改错模型对比：同一批原话分别给几个 Ollama 模型改（直接输入模式），打印结果和耗时。
.venv\\Scripts\\python.exe llm_compare.py [模型 ...]   （默认 qwen3:8b qwen3:4b）
原话 = recordings\\*.wav 的 Whisper 识别结果（缓存在 recordings\\whisper.json）+ fix_test.py 的英文用例。"""
import importlib.machinery, importlib.util, json, os, sys, time, wave
from pathlib import Path
import numpy as np
HERE = Path(__file__).parent
def load_py(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path)); spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec); loader.exec_module(mod); return mod
m = load_py('app', HERE / 'app.pyw')

def whisper_texts():
    cache = HERE / 'recordings' / 'whisper.json'
    got = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}
    files = [f for f in sorted((HERE / 'recordings').glob('*.wav')) if f.name not in got]
    if files:
        cuda = os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12'); os.environ['PATH'] = cuda + os.pathsep + os.environ['PATH']
        from faster_whisper import WhisperModel
        wh = WhisperModel(str(HERE / 'models' / 'whisper'), device='cuda', compute_type='int8_float16')
        for f in files:
            with wave.open(str(f)) as w: a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
            segs, _ = wh.transcribe(a, language='en', beam_size=1, condition_on_previous_text=False)
            got[f.name] = ' '.join(s.text.strip() for s in segs)
        cache.write_text(json.dumps(got, ensure_ascii=False, indent=1), encoding='utf-8'); del wh
    return list(got.values())

texts = whisper_texts()
ft = (HERE / 'fix_test.py').read_text(encoding='utf-8')  # 只取 CASES，不 import fix_test（它会再载一遍 app）
cases = eval(ft[ft.index('CASES = ['):].split('\n]\n')[0].split('=', 1)[1] + '\n]')
texts += [' '.join(c) for c in cases]

models = sys.argv[1:] or ['qwen3:8b', 'qwen3:4b']
res = {}
for name in models:
    m.LLM_MODEL = name
    m.llm('hello', watch=False)  # 先载入，不算时间
    res[name] = []
    for t in texts:
        t0 = time.time(); out = m.llm(t, watch=False); res[name].append((out.get('fixed', ''), out.get('zh', ''), time.time() - t0))
for i, t in enumerate(texts):
    print(f'\n[{i + 1}] 说：{t}')
    for name in models:
        f, zh, s = res[name][i]; print(f'  {name:9} {s:4.1f}s：{f}\n  {"":9}       译：{zh}')
print()
for name in models: print(f'{name}：共 {sum(r[2] for r in res[name]):.1f}s')
