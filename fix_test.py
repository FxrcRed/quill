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

ZHZH_CASES = [  # 说中文 → 出中文（fix_test.py zhzh）：history.txt 里的真实识别结果。前半是该改的（同音字、英文词、假句号），后半是不该动的
    ["卢子门的理解没错，就是这样。"],
    ["不过话要说回来，这个小屋没有窗户合子里吗？"],
    ["我有个疑问，就是现在我做的项目，你可以帮我推送到cloud里面的archK。factsects吗？"],
    ["你可以直接登到我的本地的cloud吗？"],
    ["记录里面的这些设置，不要跟着我的滚轮一起滑。。"],
    ["A组第一条okK我觉得可以保持没有窗户。然后A组第二条。你的建议。okK就是从小溪那边拉电线过来。"],
    ["关于毛巾这个事情，我是有一个疑问的，就是说我之前导入的模型的整一个小屋里面的东西是可以。改动的吗？比如说我现在想把床拿走，我只是假设。"],
    ["关于那个地图，我感觉就可以做成是玩家解锁一点，然后阴影就消除一点，等到全部解锁之后，", "这个地图一整个就可以。看清楚了还是说怎么样。我只是突然想到。这个点。"],
    ["阁楼上衣柜的故事有点。做坐。"],
    ["挂着的毛巾我觉得故事可以，但是这样子的话呢，就是要修改一下这个毛巾的模型里的样子。"],
    ["我们这里应该有窗户吗？"],
    ["是不是我在这里登录了，你就可以帮我用了。"],
    ["我把照片都备份到云盘了，你帮我看看还剩多少空间。"],
    ["明天下午三点，哦不，四点开会。"],
]

TIDY_CASES = [  # 说中文 → 出中文的规则清理：本来就该这么说的不能删
    "我们研究研究这个方案。", "一步一步来，别着急。", "谢谢谢谢，太好了。", "那个人是谁？", "就是这个意思。",
    "我想想，嗯，明天再说吧。", "我我我想去北京。", "这个金额太大了。", "我去北京，去上海，然后回家。", "慢慢来，看看再说。",
]

def run(mode='fast', lang='en', out='en'):
    t0 = time.time()
    if out == 'zh':
        for t in TIDY_CASES: print('规则：', t, '->', m.zh_tidy(t), '（送模型）' if m.zh_needs_llm(m.zh_tidy(t)) else '')
        print()
    for segs in (ZHZH_CASES if out == 'zh' else ZH_CASES if lang == 'zh' else CASES):
        job, buf, t1 = m.Job(False, mode, lang, out), '', time.time()
        for s in segs[:-1]:  # 和 Job.update 一样：停顿前是半句话就不切，连着下一段
            buf = (buf + ' ' + s).strip()
            if m.sentence_end(buf): job.add(buf); buf = ''
        job.add((buf + ' ' + segs[-1]).strip())
        res = (m.zh_join if job.seg_lang == 'zhzh' else m.join_parts)([s.get('fixed' if job.mode == 'fast' else 'natural') for s in job.segs])
        print('说：', ' | '.join(segs)); print('改：', res, f'（{time.time() - t1:.1f}s）'); print()
    print(f'共 {time.time() - t0:.1f}s')

if __name__ == '__main__':
    import threading; threading.Thread(target=m.fix_worker, daemon=True).start()
    a = sys.argv[1:]  # fix_test.py [fast|pick] [zh | zhzh]（zhzh：说中文 → 出中文）
    run(next((x for x in a if x in ('fast', 'pick')), 'fast'), 'zh' if {'zh', 'zhzh'} & set(a) else 'en', 'zh' if 'zhzh' in a else 'en')
