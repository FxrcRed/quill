"""改错效果测试（只测文字，不用麦克风，可以和 Quill 同时开着）：.venv\\Scripts\\python.exe fix_test.py
每个用例是按停顿切好的几段原话（和实际一样一段段送去改错），打印拼起来的结果。"""
import importlib.machinery, importlib.util, sys, time
from pathlib import Path
HERE = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)

CASES = [  # 来自 history.txt 的真实说法（段的切法是按当时的停顿猜的）
    ["Well, here's some adaptation I want to", "Challenge Today So in the setting page The record on the right up It's not necessary, so you can remove it."],
    ["In the setting page. I want every round rectangle to", "I want every round rectangle of Keyboard shot cards Has um same distance between each other."],
    ["I have dinner with Alin, and he was", "I have dinner with Alin, and he is Fifteen kilograms. heavier than yes last year he said"],
    ["And I want you to add a a little bar on the side of Record page So I can, I can, uh, quickly go back to the top."],
    ["I want to change the the color, no, I mean the size of the the button."],
    ["Well, it's very weird because it's very late in night, but I'm still live coding.", "Vibe coding is a very Casher things for me."],
    ["I mean heal myself. Do some exercise'cause it my previous job make me The", "make me very tired."],
    ["Can you, can you tell me how to, how to fix this bug?"],
]
ZH_CASES = [  # 说中文模式（fix_test.py zh）
    ["嗯，我想我想把这个按钮的颜色不对，是大小改一下。", "然后那个记录页面也要加一个滚动条，方便我回到最上面。"],
    ["我昨天去那个，去超市买了一些苹果，但是，但是他们没有我想要的那种。"],
    ["你能不能帮我看一下，就是这个，这个app启动的时候为什么点不了？"],
    ["我觉得这个功能挺好的，", "我觉得这个功能挺好的，但是速度有点慢。"],
    ["帮我把这个PR merge一下，然后呃，通知一下team。"],
]

def run(mode='fast', lang='en'):
    t0 = time.time()
    for segs in (ZH_CASES if lang == 'zh' else CASES):
        job, buf = m.Job(False, mode, lang), ''
        for s in segs[:-1]:  # 和 Job.update 一样：停顿前是半句话就不切，连着下一段
            buf = (buf + ' ' + s).strip()
            if m.sentence_end(buf): job.add(buf); buf = ''
        job.add((buf + ' ' + segs[-1]).strip())
        out = m.join_parts([s.get('fixed' if mode == 'fast' else 'natural') for s in job.segs])
        print('说：', ' | '.join(segs)); print('改：', out); print()
    print(f'共 {time.time() - t0:.1f}s')

if __name__ == '__main__':
    import threading; threading.Thread(target=m.fix_worker, daemon=True).start()
    a = sys.argv[1:]  # fix_test.py [fast|pick] [zh]
    run(next((x for x in a if x in ('fast', 'pick')), 'fast'), 'zh' if 'zh' in a else 'en')
