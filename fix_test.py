"""改错效果测试（只测文字，不用麦克风，可以和 Quill 同时开着）：.venv\\Scripts\\python.exe fix_test.py
每个用例是几段原话（原来按停顿切的），拼成一整句送去改错（和实际一样：松开后 Whisper 整段认出来的一句），打印结果。"""
import importlib.machinery, importlib.util, sys, time
from pathlib import Path
HERE = Path(__file__).parent
loader = importlib.machinery.SourceFileLoader('app', str(HERE / 'app.pyw')); spec = importlib.util.spec_from_loader('app', loader)
m = importlib.util.module_from_spec(spec); loader.exec_module(m)

CASES = [  # 造的句子，仿真实识别里的错误类型（重说、切在半句、识别错词、改口；段的切法模拟停顿）
    ["Well, here's some change I want to", "Change Today So in the profile page The button on the right up It's not necessary, so you can remove it."],
    ["In the menu page. I want every round button to", "I want every round button of Short cut list Has um same distance between each other."],
    ["I had lunch with Tom, and he was", "I had lunch with Tom, and he is Ten centimeters. taller than yes last year he said"],
    ["And I want you to add a a little arrow on the side of Search page So I can, I can, uh, quickly go back to the top."],
    ["I want to change the the color, no, I mean the size of the the button."],
    ["Well, it's very weird because it's very late in night, but I'm still studying.", "Studying math is a very Casher things for me."],
    ["I mean relax myself. Do some running'cause it my old school make me The", "make me very tired."],
    ["Can you, can you tell me how to, how to fix this bug?"],
    ["Here is a thing.", "What I want to show you.", "Is the app.", "Quail, not the old one.", "I don't know.", "If there is some.", "Problem or not?"],  # 识别在每个停顿补句号
    ["When I open the window and click the button.", "Okay."],  # 结尾单独一个短词：别把前一句重写一遍
]
ZH_CASES = [  # 说中文模式（fix_test.py zh）
    ["嗯，我想我想把这个按钮的颜色不对，是大小改一下。", "然后那个记录页面也要加一个滚动条，方便我回到最上面。"],
    ["我昨天去那个，去超市买了一些苹果，但是，但是他们没有我想要的那种。"],
    ["你能不能帮我看一下，就是这个，这个app启动的时候为什么点不了？"],
    ["我觉得这个功能挺好的，", "我觉得这个功能挺好的，但是速度有点慢。"],
    ["帮我把这个PR merge一下，然后呃，通知一下team。"],
]

ZHZH_CASES = [  # 说中文 → 出中文（fix_test.py zhzh）：造的句子，仿真实识别结果的错误类型。前半是该改的（同音字、英文词、假句号），后半是不该动的
    ["这个思路的方向没措，就是这样。"],
    ["不过话要说回来，这个阳台没有栏杆合子里吗？"],
    ["我有个疑问，就是现在我写的这份报告，你可以帮我放到cloud里面的archK。factsects吗？"],
    ["你可以直接连到我电脑上的cloud吗？"],
    ["设置页面的这些按钮，不要跟着页面一起滑。。"],
    ["第一个方案okK我觉得可以保留原来的颜色。然后第二个方案。你的建议。okK就是把字体再调大一点。"],
    ["关于酒店这个事情，我是有一个疑问的，就是说我们之前订的那个房间是可以。退掉的吗？比如说我现在想换一家，我只是假设。"],
    ["关于那个相册，我感觉就可以做成是每上传一张，然后封面就换一张，等到全部传完之后，", "这个相册一整个就可以。按时间排好了还是说怎么样。我只是突然想到。这个点。"],
    ["厨房里那个柜子的位置有点。做坐。"],
    ["墙上那幅画我觉得颜色可以，但是这样子的话呢，就是要修改一下这个画框的样子。"],
    ["我们这里应该放个书架吗？"],
    ["是不是我在这里填好了，你就可以帮我提交了。"],
    ["我把照片都备份到云盘了，你帮我看看还剩多少空间。"],
    ["明天下午三点，哦不，四点开会。"],
]

TIDY_CASES = [  # 说中文 → 出中文的规则清理：本来就该这么说的不能删
    "我们研究研究这个方案。", "一步一步来，别着急。", "谢谢谢谢，太好了。", "那个人是谁？", "就是这个意思。",
    "我想想，嗯，明天再说吧。", "我我我想去北京。", "这个金额太大了。", "我去北京，去上海，然后回家。", "慢慢来，看看再说。",
]

def one(segs, lang='en', out='en'):
    """和 Job.finish 一样：整句当一段去改。"""
    return m.Seg(('' if lang == 'zh' else ' ').join(segs), m.Job(False, lang, out).seg_lang).get('fixed')

def run(lang='en', out='en'):
    t0 = time.time()
    if out == 'zh':
        for t in TIDY_CASES: print('规则：', t, '->', m.zh_tidy(t), '（送模型）' if m.zh_needs_llm(m.zh_tidy(t)) else '')
        print()
    for segs in (ZHZH_CASES if out == 'zh' else ZH_CASES if lang == 'zh' else CASES):
        t1 = time.time(); res = one(segs, lang, out)
        print('说：', ' | '.join(segs)); print('改：', res, f'（{time.time() - t1:.1f}s）'); print()
    print(f'共 {time.time() - t0:.1f}s')

if __name__ == '__main__':
    import threading; threading.Thread(target=m.fix_worker, daemon=True).start()
    a = sys.argv[1:]  # fix_test.py [zh | zhzh]（zhzh：说中文 → 出中文）
    run('zh' if {'zh', 'zhzh'} & set(a) else 'en', 'zh' if 'zhzh' in a else 'en')
