"""识别模型对比：recordings\\*.wav 分别给 Parakeet（现在用的）和 Whisper large-v3-turbo 认一遍，打印结果和耗时。
.venv\\Scripts\\python.exe asr_compare.py [cpu]   （cpu：Whisper 放 CPU 跑；默认显卡）
Whisper 会带上 words.txt 里的正确写法当提示（initial_prompt），这是 Parakeet 做不到的。"""
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

files = sorted((HERE / 'recordings').glob('*.wav')) or [HERE / 'test.wav']
dev = 'cpu' if 'cpu' in sys.argv else 'cuda'

import onnx_asr
pk = onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', HERE / 'models' / 'parakeet', quantization='int8')
from faster_whisper import WhisperModel
t = time.time()
wh = WhisperModel(str(HERE / 'models' / 'whisper'), device=dev, compute_type='int8_float16' if dev == 'cuda' else 'int8')
print(f'Whisper 载入 {time.time() - t:.1f}s（{dev}）')
prompt = 'Glossary: ' + ', '.join(terms()) + '.'
wh.transcribe(load(files[0])[:16000], language='en')  # 预热

tot = {'pk': 0.0, 'wh': 0.0, 'sec': 0.0}
for f in files:
    a = load(f); sec = len(a) / 16000; tot['sec'] += sec
    t = time.time(); p = pk.recognize(a); tp = time.time() - t
    t = time.time()
    segs, _ = wh.transcribe(a, language=None, initial_prompt=prompt, beam_size=1, vad_filter=False, condition_on_previous_text=False)
    w = ' '.join(s.text.strip() for s in segs); tw = time.time() - t
    tot['pk'] += tp; tot['wh'] += tw
    print(f'\n{f.name}  {sec:.1f}s\n  Parakeet {tp:.2f}s: {p}\n  Whisper  {tw:.2f}s: {w}')
print(f"\n合计 {tot['sec']:.0f}s 录音：Parakeet {tot['pk']:.1f}s，Whisper {tot['wh']:.1f}s")
