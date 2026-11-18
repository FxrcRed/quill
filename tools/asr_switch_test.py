"""Local GPU integration check with bundled test audio. No microphone, settings writes or paste."""
from pathlib import Path
import sys
import time
import wave
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fix_test import m


def samples(name):
    with wave.open(str(m.HERE / name)) as source:
        return np.frombuffer(source.readframes(source.getnframes()), np.int16).astype(np.float32) / 32768


def run():
    eng = m.eng
    english, chinese = samples('test.wav'), samples('test_zh.wav')
    m.cfg['asr'] = 'youdao'
    try:
        eng.reconcile(initial=True)
        assert isinstance(eng.wh, m.Youdao), eng.load_error
        silent = m.Job(True, 'zh')
        silent.heard = 'stale preview'
        assert silent.finish(np.zeros(m.RATE, np.float32)) is None
        boundary = m.Job(True, 'en')
        block = english[:12800]
        eng.wh.advance(boundary, block)
        with patch.object(eng.wh, 'decode', return_value='complete sentence') as decode:
            assert eng.wh.advance(boundary, block, final=True) == 'complete sentence'
            decode.assert_called_once()
        for lang, audio, pattern in [('en', english, 'store'), ('zh', chinese, '[一-鿿]'),
                                     ('auto', english, 'store'), ('auto', chinese, '[一-鿿]')]:
            job = m.Job(True, lang)
            model = eng.wh
            for end in range(12800, len(audio), model.step):
                model.advance(job, audio[:end])
            assert job.transcript, (lang, 'no preview')
            seg = job.finish(audio)
            assert seg and m.re.search(pattern, seg.raw, m.re.I), (lang, seg.raw if seg else None)
            assert job.lang == ('en' if audio is english else 'zh')
            print('preview and final OK', lang, job.lang, flush=True)

        m.cfg['asr'] = 'auto'
        eng.rec = True
        real_run = m.subprocess.run
        def low_memory(cmd, *args, **kwargs):
            if '--query-gpu=memory.free' in cmd: return m.subprocess.CompletedProcess(cmd, 0, '64')
            return real_run(cmd, *args, **kwargs)
        with patch.object(m.subprocess, 'run', side_effect=low_memory):
            eng.reconcile()
            assert isinstance(eng.wh, m.Youdao)
            eng.rec = False
            eng.reconcile()
        assert isinstance(eng.wh, m.AsrProc), eng.load_error
        m.cfg['asr'] = 'whisper'
        assert eng.target_model() == 'whisper'
        assert 'store' in eng.recognize(english).lower()
        print('manual Whisper and deferred switch OK', flush=True)

        m.cfg['asr'] = 'auto'
        with patch.object(m, 'llm_vram', return_value=3.0), patch.object(m.subprocess, 'run') as smi:
            smi.return_value.stdout = '1500'
            assert eng.target_model() == 'whisper'
            smi.return_value.stdout = '5400'
            assert eng.target_model() == 'whisper'
            eng.spare_since = time.monotonic() - 30
            assert eng.target_model() == 'youdao'
        with patch.object(eng, 'target_model', return_value='youdao'):
            eng.reconcile()
        assert isinstance(eng.wh, m.Youdao), eng.load_error
        job = m.Job(True, 'en')
        eng.wh.advance(job, english[:12800])
        eng.wh.p.kill(); eng.wh.p.wait(timeout=5)
        text = eng.final_text(job, english)
        assert 'store' in text.lower() and isinstance(eng.wh, m.AsrProc), text
        assert eng.target_model() == 'whisper'
        print('mid-utterance failure replayed on Whisper OK', flush=True)

        eng.retry_youdao = 0
        with patch.object(eng, 'target_model', return_value='youdao'), patch.object(m.Youdao, '__init__', side_effect=RuntimeError('test load failure')):
            eng.reconcile()
        assert isinstance(eng.wh, m.AsrProc) and eng.wh_state == 'ok'
        print('startup fallback OK', flush=True)
    finally:
        if eng.wh: eng.wh.close()


if __name__ == '__main__':
    run()
