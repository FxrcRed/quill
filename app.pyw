"""本地英文语音输入（图形界面版）：按住快捷键或界面按钮说英文 → Parakeet 识别 → Qwen 改语病 + 中文翻译 → 粘贴到光标处。
双击桌面「Quill」图标启动（pythonw，无黑框）。--selftest：无人值守自检，结果写 run.log 后退出。"""
import math, ctypes, ctypes.wintypes, difflib, json, os, queue, re, shutil, socket, subprocess, sys, threading, time, traceback, urllib.error, urllib.request, wave
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont

import numpy as np
import sounddevice as sd
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageGrab, ImageTk

HERE = Path(__file__).parent
TITLE = 'Quill'
LLM_URL = 'http://127.0.0.1:11434'
LLM_MODEL = 'qwen3:8b'
LLM_GPU_LAYERS = -1   # -1 = 放显卡（Ollama 自动，一句约 1.5 秒）；0 = 全用 CPU（约 5~8 秒）。显卡被别的程序占着变慢时会自动临时改用 CPU
SHOW_SECONDS = 3      # 底部浮窗的结果自动消失；点一下固定，再点关闭
RATE = 16000
SELFTEST = '--selftest' in sys.argv
SHOWCASE = any(a.startswith('--show=') for a in sys.argv)  # 截图测试：小弹窗不因为没拿到焦点而关掉

CLEAN = """The input is a non-native speaker's dictated English from speech recognition. Turn it into the message they meant to write:
- Fix grammar, tense, articles, plurals and word choice; fix words the recognizer obviously misheard when the intended word is clear.
- Remove everything that is not part of the final message: filler words (um, uh, like, you know, I mean), stutters and repeated words ("the the", "I can, I can"), false starts and half-finished phrases the speaker abandoned, and self-corrections: when the speaker says something again or corrects it ("no", "I mean", "sorry", or just restarting the sentence), keep only the final version.
- Otherwise keep the speaker's meaning, tone and details; do not add content or summarize. Never answer or reply to the text: it is a message to be written down, even when it is a question or a request.
If the message starts with "Context:", that is what the speaker said just before, already written down: never repeat or rewrite it, use it only for reference (e.g. keep the same tense); output only the cleaned-up "Text:" part.
Examples:
"I want to, um, I want to change the the color, no, the size of the button." -> "I want to change the size of the button."
"Can you, can you tell me how to, how to fix it?" -> "Can you tell me how to fix it?"
"Yesterday I go to the, I went to the store and buy some apple." -> "Yesterday I went to the store and bought some apples."
"I have dinner with Tom and he was, I have dinner with Tom and he is very happy." -> "I had dinner with Tom, and he was very happy."
Context: "I went to the store yesterday." Text: "And I buy some milk." -> "And I bought some milk."
"""

PROMPT = CLEAN + """Also translate the cleaned-up English into natural Simplified Chinese (translate a generic "they" as 他们).
Reply JSON only: {"fixed": "...", "zh": "..."}"""

PICK_PROMPT = CLEAN + """Write the cleaned-up message in two styles:
natural: the most natural, fluent English a native speaker would write.
casual: relaxed spoken / chat style (contractions, casual phrasing), still correct.
Also translate the natural version into natural Simplified Chinese (translate a generic "they" as 他们).
Reply JSON only: {"natural": "...", "casual": "...", "zh": "..."}"""

# 说中文 → 出英文：先按同样的规则把口语中文理干净，再译成英文；zh 字段放理干净的中文（浮窗、记录里显示「你说的是」）
ZH_DROP = """- Remove everything that is not part of the final message: filler words (嗯, 啊, 呃, and 那个 / 就是 / 然后 when used as fillers), stutters and repeated words ("我想我想"), false starts the speaker abandoned, and self-corrections: when the speaker says something again or corrects it ("不对", "不是", "我是说", or just restarting the sentence), keep only the final version.
"""
ZH_CLEAN = """The input is a speaker's dictated Chinese from speech recognition (it may contain some English words). Work out the message they meant and write it in English:
""" + ZH_DROP + """- Fix words the recognizer obviously misheard when the intended word is clear.
- Translate into natural, fluent English a native speaker would write. Keep the speaker's meaning, tone and details; do not add content or summarize. Never answer or reply to the text: it is a message to be written down, even when it is a question or a request.
If the message starts with "Context:", that is what the speaker said just before, already written down: never repeat or translate it, use it only for reference; output only the "Text:" part.
Examples:
"嗯，我想我想把这个按钮的颜色不对，是大小改一下。" -> "I want to change the size of this button."
"你能不能，能不能帮我看一下这个bug怎么修？" -> "Could you take a look at how to fix this bug?"
"""

PROMPTS = {
    ('en', 'fast'): PROMPT, ('en', 'pick'): PICK_PROMPT,
    ('zh', 'fast'): ZH_CLEAN + """Also give the cleaned-up Chinese (the same message without the fillers, repeats and corrections).
Reply JSON only: {"fixed": "<English>", "zh": "<cleaned-up Chinese>"}""",
    ('zh', 'pick'): ZH_CLEAN + """Write the English in two styles:
natural: the most natural, fluent English a native speaker would write.
casual: relaxed spoken / chat style (contractions, casual phrasing), still correct.
Also give the cleaned-up Chinese (the same message without the fillers, repeats and corrections).
Reply JSON only: {"natural": "...", "casual": "...", "zh": "<cleaned-up Chinese>"}""",
    # 说中文 → 出中文：只理干净，不翻译、不改写（只有带改口的句子才送来，口头禅 / 重复已经被 zh_tidy() 删过）
    ('zhzh', 'fast'): """The input is a speaker's dictated Chinese from speech recognition (it may contain some English words). Write down the message they meant, in Chinese:
""" + ZH_DROP + """- Fix words the recognizer obviously misheard when the intended word is clear, and fix the punctuation.
- Keep the speaker's own wording, meaning and tone, and keep English words as they are. Do not translate, rewrite, add content or summarize. Never answer or reply to the text: it is a message to be written down, even when it is a question or a request.
If the message starts with "Context:", that is what the speaker said just before, already written down: never repeat it, use it only for reference; output only the "Text:" part.
Examples:
"我想把这个按钮的颜色不对，是大小改一下。" -> "我想把这个按钮的大小改一下。"
"明天下午三点，哦不，四点开会。" -> "明天下午四点开会。"
"我昨天去那个，去超市买了点苹果。" -> "我昨天去超市买了点苹果。"
Reply JSON only: {"fixed": "<cleaned-up Chinese>"}""",
}

MIC_HINT = '麦克风没有声音：先按笔记本的麦克风静音键（常见 F4 或 Fn+F4，灯亮=静音），或在设置里换一个麦克风。'
MIC_SHORT = '麦克风没声音：按静音键（F4 / Fn+F4），或去设置换一个麦克风。'
# 软 UI（neumorphism）配色：同一种浅灰底，凸起/凹陷全靠左上白光、右下灰影
BG, INSET, LIGHT, DARK = '#E6E6E6', '#DFDFDF', '#FFFFFF', '#BDBDBD'
INK, TEXT, SUB, TICK, TICK_MAJ = '#1E1E1E', '#1E1E1E', '#8C8C8C', '#C6C6C6', '#8F8F8F'  # 字只用两种颜色：TEXT 正文 / SUB 说明（INK 是声波条那些画的东西）
GREEN, ORANGE, RED, TOAST_BG = '#2F9E5B', '#D9822B', '#D64541', '#EFEFEF'
FONT, FONT_BOLD = 'Microsoft YaHei UI', None  # 启动时有 fonts\NotoSansSC 就换成 Noto Sans SC Medium / Bold（思源黑体，SIL OFL 开源）
DISPLAY, ICON = 'Bahnschrift', 'Segoe MDL2 Assets'  # 启动时换成项目里的 Michroma 宽体 / Segoe Fluent Icons（有的话）
I_SET, I_COPY, I_HIST, I_OK, I_CHECK = '', '', '', '', ''
I_MIN, I_CLOSE, I_MIC, I_BALL = '\uE921', '\uE8BB', '\uE720', '\uE73F'
I_DOWN, I_UP = '\uE70D', '\uE70E'  # 记录里展开 / 收起原话
I_PIN = '\uE718'  # 浮窗固定后右边的图钉
ICON_C = '#555555'  # 图标按钮平时的颜色
S = 1.0  # 屏幕缩放（150% = 1.5），界面坐标都按 100% 写，画的时候乘 S

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
user32.GetAsyncKeyState.restype = ctypes.c_short

# ---- 快捷键名字 ----
KEYS = {'右 Alt': 0xA5, '右 Ctrl': 0xA3, '左 Alt': 0xA4, 'F8': 0x77, 'F9': 0x78}
NAMES = {0xA5: '右 Alt', 0xA4: '左 Alt', 0xA3: '右 Ctrl', 0xA2: '左 Ctrl', 0xA1: '右 Shift', 0xA0: '左 Shift',
         0x5B: '左 Win', 0x5C: '右 Win', 0x5D: '菜单键', 0x14: '大写锁定', 0x20: '空格', 0x09: 'Tab', 0x1B: 'Esc',
         0x2D: 'Insert', 0x13: 'Pause', 0x91: 'ScrollLock', 0x0D: '回车'}
SCAN = [v for v in range(0x08, 0xFF) if v not in (0x10, 0x11, 0x12, 0xE7, 0xE8)]  # 0x10~0x12 是不分左右的重复项

def key_name(vk):
    if vk in NAMES: return NAMES[vk]
    if 0x70 <= vk <= 0x87: return f'F{vk - 0x6F}'
    if 0x30 <= vk <= 0x5A: return chr(vk)
    return f'按键 {vk:#04x}'

def key_down(vk): return bool(user32.GetAsyncKeyState(vk) & 0x8000)

def tap(vk):
    user32.keybd_event(vk, 0, 0, 0); user32.keybd_event(vk, 0, 2, 0)


# ---- 日志 / 设置 ----
def log(*a):
    line = f'{datetime.now():%H:%M:%S} ' + ' '.join(map(str, a))
    if sys.stdout: print(line, flush=True)
    with open(HERE / 'run.log', 'a', encoding='utf-8') as f: f.write(line + '\n')

cfg = {'hotkey': 0xA5, 'device': None, 'mode': 'fast', 'lang': 'en', 'zh_out': 'en'}  # mode：fast 直接输入 / pick 让我挑选；lang：en 说英文 / zh 说中文；zh_out：说中文时出英文 / 出中文
try: cfg.update(json.loads((HERE / 'settings.json').read_text(encoding='utf-8')))
except Exception: pass

def save_cfg():
    try: (HERE / 'settings.json').write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    except Exception: log('保存设置失败', traceback.format_exc())

ui = queue.Queue()  # 后台线程 → 界面线程
def post(*m): ui.put(m)
def notify(text, level='warn', toast=False): post('notice', text, level, toast)


# ---- 改错（Ollama）----
FIELDS = {'fast': ('fixed', 'zh'), 'pick': ('natural', 'casual', 'zh')}
gpu = {'off_until': 0.0, 'slow': 0}  # 显卡变慢（被别的程序占着）时暂时改用 CPU
last_llm = [0.0]
llm_stat = {}  # 上一次改错：用了几秒、是不是显卡

def llm_opts():
    on_gpu = LLM_GPU_LAYERS != 0 and time.time() > gpu['off_until']
    layers = {} if on_gpu and LLM_GPU_LAYERS < 0 else {'num_gpu': LLM_GPU_LAYERS if on_gpu else 0}
    return {'temperature': 0.2, 'num_ctx': 2048, **layers}, on_gpu

def llm(text, mode='fast', on_field=None, timeout=60, ctx='', watch=True, lang='en'):  # watch=False：预热那种很短的请求不算进显卡测速
    """流式调 Ollama：JSON 里每个字段一写完就回调 on_field(名字, 内容)，英文不用等中文翻译生成完。返回所有字段。"""
    keys, (opts, on_gpu) = ('fixed',) if lang == 'zhzh' else FIELDS[mode], llm_opts()
    body = {'model': LLM_MODEL, 'stream': True, 'think': False, 'keep_alive': '30m', 'format': 'json', 'options': opts,
            'messages': [{'role': 'system', 'content': PROMPTS[lang, mode]}, {'role': 'user', 'content': f'Context: {ctx}\nText: {text}' if ctx else text}]}
    req = urllib.request.Request(LLM_URL + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    buf, got, stats, t0 = '', {}, {}, time.time()
    def give(k, v):
        got[k] = v
        if on_field: on_field(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            if not line.strip(): continue
            m = json.loads(line); buf += m.get('message', {}).get('content', '')
            for k in keys:
                hit = k not in got and re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % k, buf)
                if hit: give(k, json.loads('"' + hit.group(1) + '"').strip())
            if m.get('done'): stats = m
    try: full = json.loads(buf)
    except Exception: full = {}
    for k in keys:
        if k not in got: give(k, str(full.get(k) or ('' if k == 'zh' else text)).strip())
    last_llm[0] = time.time()
    if watch: llm_stat.update(secs=time.time() - t0, gpu=on_gpu); watch_gpu(stats, on_gpu, time.time() - t0)
    return got

def watch_gpu(st, on_gpu, secs):
    """显卡上每秒不到 15 个字（正常约 45），连着两次就 15 分钟内改用 CPU（CPU 约 9 个/秒，但稳定）。"""
    n, d = st.get('eval_count', 0), st.get('eval_duration', 0)
    if not (on_gpu and n > 10 and d): return
    tps = n / (d / 1e9)
    if tps >= 15: gpu['slow'] = 0; return
    gpu['slow'] += 1; log(f'显卡改错偏慢：{tps:.1f} 字/秒，{secs:.1f} 秒')
    if gpu['slow'] >= 2:
        gpu['off_until'], gpu['slow'] = time.time() + 900, 0
        log('显卡太慢，15 分钟内改用 CPU'); notify('显卡被别的程序占着，改错先换成 CPU（慢一点），15 分钟后再试显卡。', 'warn')

def llm_warm():
    """只把模型载入内存（不生成），说话的同时就能载好。"""
    try:
        body = {'model': LLM_MODEL, 'keep_alive': '30m', 'options': llm_opts()[0]}
        urllib.request.urlopen(urllib.request.Request(LLM_URL + '/api/generate', json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=60).read()
        last_llm[0] = time.time()
    except Exception as e: log('预热改错失败：', repr(e))

def llm_error(e):
    if isinstance(e, (socket.timeout, TimeoutError)) or 'timed out' in str(e): return '改错太慢超时了，已直接用原话。'
    if isinstance(e, urllib.error.HTTPError) and e.code == 404: return f'改错服务里没有 {LLM_MODEL} 模型，已直接用原话。'
    if isinstance(e, (urllib.error.URLError, ConnectionError)): return '改错服务没开（去设置里点「启动」），已直接用原话。'
    return '改错出错了，已直接用原话。'

def ollama_status():
    try:
        with urllib.request.urlopen(LLM_URL + '/api/tags', timeout=2) as r:
            names = [m['name'] for m in json.loads(r.read()).get('models', [])]
        return 'ok' if any(n.split(':latest')[0] == LLM_MODEL for n in names) else 'no_model'
    except Exception:
        return 'down'

def start_ollama():
    exe = shutil.which('ollama') or os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\ollama.exe')
    try: subprocess.Popen([exe, 'serve'], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: log('启动 Ollama 失败', traceback.format_exc()); notify('找不到改错服务 Ollama，可能没装好。', 'err')


# ---- 录音 / 识别 ----
def find_pause(a, pos, min_speech=1.2, gap=0.45):
    """在 pos 之后找「说完一句后的停顿」（≥0.45 秒安静），返回停顿中间的位置；没有就 None。"""
    F = 480; n = (len(a) - pos) // F  # 30ms 一帧
    if n * 0.03 < min_speech + gap: return None
    rms = np.sqrt((a[pos:pos + n * F].reshape(n, F) ** 2).mean(1))
    quiet = rms < max(0.006, float(np.percentile(rms, 10)) * 3)
    need, run = int(gap / 0.03), 0
    for i in range(n - 1, -1, -1):
        if quiet[i]: run += 1; continue
        if run >= need and i * 0.03 >= min_speech and (~quiet[:i + 1]).sum() >= 10: return pos + (i + 1 + run // 2) * F
        run = 0
    return None

def has_speech(a): return len(a) > RATE * 0.3 and float(np.abs(a).max()) > 0.01

# 分段规则：停顿处只有「一句说完了」才切（切在半句话中间，后一段单独改错时模型会把上一段再写一遍）；停顿后从头重说的和上一段合起来改
OPEN_END = set('a an the to of and or but so because that which who whose with for in on at from by as if than then into about '
               'my your his her our their its this these those is are was were be been am do does did have has had will would can could should '
               'i we you he she they very really just'.split())
OPEN_END_ZH = ('的', '和', '跟', '把', '被', '在', '是', '要', '就', '但是', '而且', '或者', '因为', '所以', '如果', '然后', '那个', '这个', '就是')
CJK = re.compile(r'[一-鿿]')
def words(t): return re.findall(r"[a-z0-9']+|[一-鿿]", t.lower())  # 英文按词，中文按字

def sentence_end(text):
    """识别出来的这段像不像一句说完的话：以 . ? ! 。？！ 结尾，而且最后一个词不是 to / the / and、的 / 因为 这种明显没说完的。"""
    t = text.rstrip(); w = words(t)
    if CJK.search(t): return t[-1:] in '。？！.?!' and not t[:-1].endswith(OPEN_END_ZH)
    return t[-1:] in '.?!' and bool(w) and w[-1] not in OPEN_END

FIX_START = re.compile(r"(不对|不是|我是说|我的意思是|应该是|no\b|sorry\b|i mean\b|wait\b|actually\b)", re.I)

# 说中文 → 出中文的快速路线：口头禅、口吃、重复按规则直接删（不到 1 毫秒）；只有带改口的才交给改错模型
ZH_FILLER = re.compile(r'(^|[，,。？！?!、])\s*(?:(?:[嗯呃额啊]|那个|就是)\s*[，,、]\s*)+')  # 句首 / 逗号后单独成一截的「嗯，」「那个，」
ZH_HMM = re.compile(r'[嗯呃]+[，,、]?')
ZH_STUTTER = re.compile(r'([一-鿿])(?:[，,、]?\1){2,}|([一-鿿])[，,、]\2')  # 我我我 / 我，我
ZH_REPEAT = re.compile(r'([一-鿿][^。？！?!]{1,15}?)(?:[，,、]?\1)+')  # 我想我想 / 但是，但是 / 整句重说
ZH_KEEP = set('研究 讨论 休息 考虑 商量 学习 了解 介绍 检查 收拾 打听 活动 锻炼 整理 准备 尝试 体验 熟悉 打扫 认识 比较 练习 哈哈 呵呵 嘿嘿 嘻嘻'.split())  # 本来就叠着说的词（还有「一步一步」这种「一」开头的）
ZH_FIX = re.compile(r'不对|我是说|我的意思是|说错|重说|哦不|噢不|算了|(?:^|[，,。？！?!])\s*(?:不是|应该是)')
ZH_RESTART = re.compile(r'([一-鿿]{1,2})[^，,。？！?!]{0,5}[，,]\s*\1')  # 「去那个，去超市」：说一半从头再来

def zh_tidy(t):
    t = re.sub(r'(?<=[一-鿿，。？！、])\s+(?=[一-鿿])', '', t.strip())  # 中文段之间不要空格
    t = ZH_FILLER.sub(lambda m: m.group(1), t)
    t = ZH_HMM.sub('', t)
    t = ZH_REPEAT.sub(lambda m: m.group(0) if m.group(1) in ZH_KEEP or m.group(1)[0] == '一' else m.group(1), t)  # 先合重复的词，「谢谢谢谢」才不会被当成口吃
    t = ZH_STUTTER.sub(lambda m: m.group(1) or m.group(2), t)
    t = re.sub(r'[，,、]\s*(?=[，,、。？！?!])', '', t)  # 删完剩下的空逗号
    return t.lstrip('，,、。 ') or t

def zh_needs_llm(t): return bool(ZH_FIX.search(t) or ZH_RESTART.search(t))

def restarts(prev, text, n=4):
    """新的一段该不该和上一段合起来改（改错时模型才看得到改口 / 重说）：
    text 开头 n 个词（中文 6 个字）在 prev 里原样出现过（停下来又从头重说）；或者 text 以「不对 / 我是说 / no / I mean」这种改口开头；
    或者上一段太短（「嗯，我想。」这种没说完的）。"""
    if CJK.search(text): n = 6
    a, b = words(prev), words(text)[:n]
    if len(a) < (6 if CJK.search(prev) else 3) or FIX_START.match(text.lstrip(' ，,')): return True
    return len(b) == n and any(a[i:i + n] == b for i in range(len(a) - n + 1))

def join_parts(parts):
    """拼各段改好的英文；后一段开头要是把前一段的结尾又写了一遍（≥3 个词），去掉重复的那截（兜底）。"""
    out = []
    for p in parts:
        if out:
            a, b = words(out[-1]), p.split()
            for n in range(min(len(a), len(b)), 2, -1):
                if a[-n:] == [x for w in b[:n] for x in words(w)]: p = ' '.join(b[n:]); break
        if p: out.append(p)
    return ' '.join(out)


class Seg:
    """一段话：识别好的原文 + 改错结果（后台按顺序改，每个字段一出来就能用）。"""
    def __init__(self, raw, mode, ctx='', lang='en'):
        self.raw, self.mode, self.ctx, self.lang, self.out, self.cv = raw, mode, ctx, lang, {}, threading.Condition()
        self.dead = False  # 被合进后一段了：还没开始改就不用改了
        self.text = raw  # 送去改的文字（出中文时是规则删过口头禅 / 重复的）
        if lang == 'zhzh':  # 说中文 → 出中文：没有翻译那行；没有改口就不用等模型
            self.text, self.out['zh'] = zh_tidy(raw), ''
            if not zh_needs_llm(self.text): self.out['fixed'] = self.text; return
        fixq.put(self)

    def put(self, k, v):
        with self.cv: self.out[k] = v; self.cv.notify_all()

    def get(self, k, timeout=120):
        with self.cv: self.cv.wait_for(lambda: k in self.out, timeout)
        return self.out.get(k, '' if k == 'zh' else self.text)

fixq = queue.Queue()
def fix_worker():
    while True:
        s = fixq.get()
        if s.dead: continue
        try: llm(s.text, s.mode, s.put, ctx=s.ctx, lang=s.lang)
        except Exception as e:
            log('改错失败：', repr(e)); err = '（' + llm_error(e) + '）'
            if s.lang == 'zhzh': notify(llm_error(e), 'warn')  # 没有翻译那行可以放提示
            for k in FIELDS[s.mode]:
                if k not in s.out: s.put(k, err if k == 'zh' else s.text)


class Job:
    """一次录音。说话时每 0.5 秒识别一次给浮窗实时显示；遇到停顿就把前面那句定下来、先送去改错，松开后只剩最后一段要处理。"""
    def __init__(self, by_key, mode, lang='en', out='en'):
        self.by_key, self.mode, self.lang, self.pos, self.segs, self.lock = by_key, mode, lang, 0, [], threading.Lock()
        self.skip = 0  # 这个位置之前的停顿看过了，是半句话，不切
        self.seg_lang = 'zhzh' if lang == 'zh' and out == 'zh' else lang  # 改错用哪套提示词
        if self.seg_lang == 'zhzh': self.mode = 'fast'  # 「让我挑选」是挑英文的说法，出中文时直接输入
        self.spec, self.last_part = None, ''  # 说话停下来时先改着的那段（松开时原话没变就直接用）

    def raw(self, segs): return ('' if self.lang == 'zh' else ' ').join(s.raw for s in segs)  # 中文段之间不加空格

    def plan(self, text):
        """text 加进来会变成哪一段：(被合并掉的上一段, 合并后的原话, 参考的前一句)。"""
        if self.segs and restarts(self.segs[-1].raw, text):  # 停顿后从头重说：和上一段合成一段改，模型只留重说的版本
            prev = self.segs[-1]; ctx = self.segs[-2].raw if len(self.segs) > 1 else ''
            return prev, prev.raw + ('' if self.lang == 'zh' else ' ') + text, ctx
        return None, text, self.segs[-1].raw if self.segs else ''

    def add(self, text):  # 前一句一起送去当参考，分开改也能保持时态一致
        prev, text, ctx = self.plan(text)
        if prev: self.segs.pop(); prev.dead = True
        sp, self.spec = self.spec, None
        if sp and (sp.raw, sp.ctx) == (text, ctx): return self.segs.append(sp)  # 停顿时已经先改上了
        if sp: sp.dead = True
        self.segs.append(Seg(text, self.mode, ctx, self.seg_lang))

    def speculate(self, part):
        """出中文：识别出来的尾巴 0.5 秒没变（停下来了）就先送去改，松开时多半不用再等模型。"""
        stable, self.last_part = part == self.last_part, part
        if not (stable and part): return
        _, text, ctx = self.plan(part)
        if self.spec and (self.spec.raw, self.spec.ctx) == (text, ctx): return
        if self.spec: self.spec.dead = True
        self.spec = Seg(text, self.mode, ctx, self.seg_lang)

    def update(self, a):
        with self.lock:
            cut = find_pause(a, self.pos, gap=1.0 if self.lang == 'zh' else 0.45)  # 中文逗号处常停顿，SenseVoice 又每段都加「。」：停够 1 秒才切
            if cut and cut > self.skip:
                text = eng.recognize(a[self.pos:cut], self.lang)
                if not text: self.pos = cut
                elif sentence_end(text): self.pos = cut; self.add(text)
                else: self.skip = cut  # 半句话：先不切，连着后面一起识别
            tail = a[self.pos:]
            part = eng.recognize(tail, self.lang) if has_speech(tail) else ''
            if self.seg_lang == 'zhzh': self.speculate(part)
            return self.raw(self.segs), part

    def finish(self, a):
        with self.lock:
            tail = a[self.pos:]
            text = eng.recognize(tail, self.lang) if has_speech(tail) or not self.segs and len(tail) > RATE * 0.3 else ''  # 结尾只剩呼吸声时不识别，免得冒出 "Yeah."
            if text: self.add(text)
            self.pos = len(a)
            return list(self.segs)


class Engine:
    def __init__(self):
        self.asr, self.load_error, self.loaded = None, '', threading.Event()
        self.zh, self.zh_error, self.zh_loaded, self.zh_started = None, '', threading.Event(), False  # 中文识别（SenseVoice），第一次切到「中」才载
        self.stream, self.rec, self.src, self.chunks, self.probe, self.peak = None, False, None, [], None, 0.0
        self.key_paused, self.busy, self.picking, self.job, self.tried = False, False, False, None, False  # tried：已经试过打开麦克风

    def _cb(self, d, *_):
        self.peak = max(self.peak, float(np.abs(d).max()))
        if self.rec: self.chunks.append(d.copy())
        if self.probe is not None: self.probe.append(d.copy())

    def devices(self):
        try:
            return [d['name'] for d in sd.query_devices() if d['hostapi'] == 0 and d['max_input_channels'] > 0
                    and '映射' not in d['name'] and 'Mapper' not in d['name']]
        except Exception: return []

    def open(self, name):
        self.tried = True
        if self.stream:
            try: self.stream.close()
            except Exception: pass
            self.stream = None
        dev = None
        if name:
            for i, d in enumerate(sd.query_devices()):
                if d['hostapi'] == 0 and d['max_input_channels'] > 0 and d['name'] == name: dev = i; break
        try:
            self.stream = sd.InputStream(device=dev, samplerate=RATE, channels=1, dtype='float32', callback=self._cb)
            self.stream.start(); log('麦克风：', name or '系统默认'); return True
        except Exception:
            log('麦克风打不开', name, traceback.format_exc()); self.stream = None
            notify('这个麦克风打不开：换一个试试，或检查 Windows 设置 → 隐私和安全性 → 麦克风。', 'err'); return False

    def load(self):
        try:
            self.asr = AsrProc()
            log('识别模型已加载')
        except Exception as e:
            log('识别模型加载失败', e)
            self.load_error = '识别模型加载失败：models\\parakeet 里的文件可能缺了，请让 Claude 检查。'
        finally:
            self.loaded.set()
        post('state', self.idle_state())
        if self.asr and ollama_status() == 'ok':
            try: llm('hello', watch=False, lang=cfg['lang']); log('改错模型已预热', '（显卡）' if llm_opts()[1] else '（CPU）')
            except Exception as e: log('预热改错失败：', e)

    def load_zh(self):
        if self.zh_started: return
        self.zh_started = True; self.zh_loaded.clear(); post('state', self.idle_state())
        try:
            self.zh = AsrProc('zh')
            log('中文识别模型已加载')
        except Exception as e:
            log('中文识别模型加载失败', e); self.zh_started = False  # 下次切到「中」再试
            self.zh_error = '中文识别模型加载失败：models\\sensevoice 里的文件可能缺了，请让 Claude 检查。'
        finally:
            self.zh_loaded.set()
        post('state', self.idle_state())

    # 按住说话用的语言：en 说英文（Parakeet）/ zh 说中文（SenseVoice），出来的都是英文
    def model(self, lang): return self.zh if lang == 'zh' else self.asr
    def ev(self, lang): return self.zh_loaded if lang == 'zh' else self.loaded
    def error(self, lang=None): return self.zh_error if (lang or cfg['lang']) == 'zh' else self.load_error
    def idle_state(self):
        lang = cfg['lang']
        return 'ready' if self.model(lang) else 'error' if self.ev(lang).is_set() else 'loading'

    def recognize(self, a, lang='en'):
        m = self.model(lang)
        return m.recognize(a, sample_rate=RATE).strip() if m else ''

    def audio(self):
        ch = list(self.chunks)
        return np.concatenate(ch)[:, 0] if ch else np.zeros(0, np.float32)

    def start(self, src):
        if self.rec or self.busy: return
        if self.picking: post('choose', None)  # 还有没选的版本：开始新的一句就当放弃
        self.chunks.clear(); self.src, self.rec = src, True
        self.job = job = Job(src == 'key', cfg.get('mode', 'fast'), cfg['lang'], cfg.get('zh_out', 'en'))
        log('开始录音', src); post('state', 'rec', src == 'key')
        threading.Thread(target=self.live, args=(job,), daemon=True).start()
        if time.time() - last_llm[0] > 1200: threading.Thread(target=llm_warm, daemon=True).start()  # 闲了很久：边说边把模型载回来

    def live(self, job):
        while True:
            time.sleep(0.5)
            if not self.rec or self.job is not job: return
            if not self.model(job.lang): continue
            try: done, part = job.update(self.audio())
            except Exception: log('实时识别出错', traceback.format_exc()); return
            if self.rec and self.job is job: post('live', done, part)

    def stop(self):
        if not self.rec: return
        self.rec = False; self.busy = True
        threading.Thread(target=self.work, args=(self.audio(), self.job), daemon=True).start()

    def work(self, audio, job):
        try: self._work(audio, job)
        except Exception:
            log('出错：', traceback.format_exc())
            notify('出了点问题，已记到 run.log。再试一次；还不行就把 run.log 发给 Claude 看。', 'err', True)
        finally:
            if self.busy: self.busy = False; post('state', self.idle_state())

    def _work(self, audio, job):
        peak = float(np.abs(audio).max()) if len(audio) else 0.0
        log(f'录到 {len(audio) / RATE:.1f}s，音量峰值 {peak:.4f}')
        if len(audio) < RATE * 0.3: return notify('按得太短了：按住说完一整句再松开。', 'warn', True)
        if peak < 0.003: post('mic_bad'); return notify(MIC_HINT, 'err', True)
        post('state', 'busy', job.by_key)
        self.ev(job.lang).wait()
        if not self.model(job.lang): return notify(self.error(job.lang), 'err', True)
        t = time.time(); segs = job.finish(audio); raw = job.raw(segs)
        if not raw: return notify('没听清，再说一次（靠近麦克风、说慢一点）。', 'warn', True)
        post('thinking', raw)
        if job.mode == 'pick':
            opts = [join_parts([s.get(k) for s in segs]) for k in ('natural', 'casual')]
            log(f'识别（{len(segs)} 段）：', raw, '| 自然：', opts[0], '| 口语：', opts[1], f'| 松开后 {time.time() - t:.1f}s')
            post('pick', raw, opts, job.by_key)
        else:
            fixed = ''.join(s.get('fixed') for s in segs) if job.seg_lang == 'zhzh' else join_parts([s.get('fixed') for s in segs])
            log(f'识别（{len(segs)} 段）：', raw, '| 改后：', fixed, f'| 松开后 {time.time() - t:.1f}s')
            if job.by_key: paste(fixed)
            else: root.after(0, lambda: set_clip(fixed))
            post('fixed', raw, fixed, job.by_key)
        self.busy = False; post('state', 'ready')  # 中文翻译还在后台出，不耽误说下一句
        post('zh', raw, ''.join(s.get('zh') for s in segs))

    def probe_record(self, secs):
        self.probe = []; time.sleep(secs); p, self.probe = self.probe, None
        return (max((float(np.abs(c).max()) for c in p), default=0.0), sum(len(c) for c in p))

    def wait_any_key(self, timeout):
        """等用户按下任意一个键，返回它的键码（不监听键盘内容，只看哪个键被按住）。"""
        held = {v for v in SCAN if key_down(v)}; end = time.time() + timeout
        while time.time() < end:
            for v in SCAN:
                if key_down(v):
                    if v not in held: return v
                else: held.discard(v)
            time.sleep(0.015)
        return None

    def hotkey_loop(self):
        """每 20ms 查一次快捷键是否按住（GetAsyncKeyState），不用低级键盘钩子，钩子被系统摘掉也不受影响。
        挑版本时：点一下快捷键 = 选第 1 个，Esc = 放弃。"""
        down = esc = False
        while True:
            time.sleep(0.02)
            if self.key_paused: down = False; continue
            vk = cfg['hotkey']; d = key_down(vk)
            if d and not down and vk in (0xA4, 0xA5, 0x5B, 0x5C): tap(0xE8)  # 垫一个空键：单按 Alt 松开会激活菜单栏、单按 Win 会弹开始菜单
            if self.picking:
                e = key_down(0x1B)
                if e and not esc: post('choose', None)
                esc = e
                if not d and down: post('choose', 0)
            elif d and not down:
                self.start('key')
            elif not d and down and self.src == 'key':
                self.stop()
            down = d

# ---- 识别子进程 ----
# import onnxruntime 时它的 C 扩展初始化要占着 GIL 七八秒，放在界面进程里，刚打开的窗口就点不动 → 识别放在单独的子进程里
# （app.pyw --asr-worker），这边只用管道送音频、收文字。父 → 子：4 字节样本数 + float32；子 → 父：4 字节长度 + UTF-8（启动时先回 OK 或出错信息）。
def asr_worker(lang):
    """lang：en = Parakeet（onnx_asr）/ zh = SenseVoice（sherpa_onnx，普通话，夹着的英文词也认）。两种各开一个子进程，中文的第一次切到「中」才开。"""
    inp, out = sys.stdin.buffer, sys.stdout.buffer; sys.stdout = None  # log() 别往管道里打印
    def send(s): b = s.encode('utf-8'); out.write(len(b).to_bytes(4, 'little') + b); out.flush()
    try:
        if lang == 'zh':
            import sherpa_onnx
            d = HERE / 'models' / 'sensevoice'
            r = sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(d / 'model.int8.onnx'), tokens=str(d / 'tokens.txt'),
                                                                language='zh', use_itn=True, num_threads=4)
            def rec(a):
                st = r.create_stream(); st.accept_waveform(RATE, a); r.decode_stream(st); return st.result.text
        else:
            import onnx_asr
            m = onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', HERE / 'models' / 'parakeet', quantization='int8')
            rec = lambda a: m.recognize(a, sample_rate=RATE)
    except Exception:
        log('识别子进程加载失败', lang, traceback.format_exc())
        return send('models\\%s 里的文件可能缺了' % ('sensevoice' if lang == 'zh' else 'parakeet'))
    send('OK')
    while len(h := inp.read(4)) == 4:  # 主程序退出、管道关掉就结束
        a = np.frombuffer(inp.read(int.from_bytes(h, 'little') * 4), np.float32)
        try: send(rec(a).strip() if len(a) else '')
        except Exception: log('识别出错', traceback.format_exc()); send('')

class AsrProc:
    """识别子进程的代理，recognize() 和 onnx_asr 的模型一样用（几个线程都会调，排队）。"""
    def __init__(self, lang='en'):
        self.lang, self.lock = lang, threading.Lock()
        self.spawn()

    def spawn(self):
        exe = Path(sys.executable).with_name('pythonw.exe')
        with open(HERE / 'run.log', 'a', encoding='utf-8') as err:  # 子进程没接住的报错也记进 run.log
            self.p = subprocess.Popen([str(exe), str(HERE / 'app.pyw'), '--asr-worker=' + self.lang], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=err, cwd=HERE, creationflags=0x08000000)  # CREATE_NO_WINDOW
        if (msg := self._recv()) != 'OK': raise RuntimeError(msg)

    def _recv(self):
        h = self.p.stdout.read(4)
        if len(h) < 4: raise RuntimeError('识别子进程退出了')
        return self.p.stdout.read(int.from_bytes(h, 'little')).decode('utf-8')

    def recognize(self, a, sample_rate=RATE):
        a = np.ascontiguousarray(a, np.float32); msg = len(a).to_bytes(4, 'little') + a.tobytes()
        with self.lock:
            try:
                self.p.stdin.write(msg); self.p.stdin.flush(); return self._recv()
            except (OSError, RuntimeError):  # 子进程没了（被杀 / 崩了）：重开一个再试一次，不然之后每句都识别不了
                log('识别子进程断了，重开', self.lang); self.p.kill(); self.spawn()
                self.p.stdin.write(msg); self.p.stdin.flush(); return self._recv()

eng = Engine()

def save_history(raw, fixed, zh):
    with open(HERE / 'history.txt', 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now():%Y-%m-%d %H:%M}\n说：{raw}\n改：{fixed}\n译：{zh}\n\n')


def set_clip(text):
    root.clipboard_clear(); root.clipboard_append(text); root.update()

def paste(text):
    """借剪贴板粘贴到光标处，结束后恢复原剪贴板文字。"""
    ev, box = threading.Event(), {}
    def put():
        try: box['old'] = root.clipboard_get()
        except tk.TclError: box['old'] = None
        set_clip(text); ev.set()
    root.after(0, put); ev.wait(2); time.sleep(0.05)
    for vk, fl in ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)): user32.keybd_event(vk, 0, fl, 0)
    time.sleep(0.5)
    if box.get('old') is not None: root.after(0, lambda: set_clip(box['old']))


# ---- 自检 ----
CHECKS = ['识别模型', '麦克风', '快捷键', '语音识别', '改错服务']

def run_checks(report, interactive):
    """report(序号, 'run'|'ok'|'fail'|'warn', 说明, 附加按钮)；返回是否全部通过。"""
    ok = True
    report(0, 'run', '加载中…'); eng.loaded.wait(180)
    if eng.asr: report(0, 'ok', '已加载')
    else: report(0, 'fail', eng.load_error or '还没加载完'); ok = False

    secs = 3 if interactive else 1
    report(1, 'run', '请对着麦克风说一句话（3 秒）…' if interactive else '录 1 秒…')
    peak, n = eng.probe_record(secs) if eng.stream else (0.0, 0)
    if n == 0: report(1, 'fail', '麦克风打不开：在设置里换一个，或检查 Windows 设置 → 隐私和安全性 → 麦克风。'); ok = False
    elif peak < 0.003: report(1, 'fail', f'几乎没声音（{peak:.4f}）。' + MIC_HINT); ok = False
    elif peak < 0.02: report(1, 'warn', f'有声音但偏小（{peak:.3f}），靠近一点说。')
    else: report(1, 'ok', f'有声音（{peak:.2f}）')

    vk = cfg['hotkey']; name = key_name(vk)
    report(2, 'run', f'请按一下「{name}」（8 秒内）…' if interactive else f'模拟按「{name}」…')
    eng.key_paused = True
    if not interactive: threading.Timer(0.5, lambda: (user32.keybd_event(vk, 0, 0, 0), time.sleep(0.2), user32.keybd_event(vk, 0, 2, 0))).start()
    got = eng.wait_any_key(8); time.sleep(0.4); eng.key_paused = False
    if got == vk: report(2, 'ok', f'收到「{name}」')
    elif got is None: report(2, 'fail', f'8 秒内没收到按键。如果按了「{name}」还是这样：在设置里点「录制」换一个键（比如 F8）。'); ok = False
    else: report(2, 'fail', f'收到的是「{key_name(got)}」，不是「{name}」。', got); ok = False

    report(3, 'run', '识别测试录音…')
    try:
        with wave.open(str(HERE / 'test.wav')) as w:
            a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        t = time.time(); raw = eng.asr.recognize(a, sample_rate=RATE).strip() if eng.asr else ''
        if raw: report(3, 'ok', f'{time.time() - t:.1f} 秒：{raw}')
        else: report(3, 'fail', '没识别出文字'); ok = False
    except Exception:
        log(traceback.format_exc()); report(3, 'fail', '识别出错（test.wav 可能不见了），详情见 run.log'); ok = False

    report(4, 'run', '让 Qwen 改一句话（第一次要十几秒）…')
    st = ollama_status()
    if st == 'down': report(4, 'fail', '改错服务没开：点设置里的「启动」，等几秒再自检。'); ok = False
    elif st == 'no_model': report(4, 'fail', f'改错服务里没有 {LLM_MODEL} 模型，请让 Claude 重新导入。'); ok = False
    else:
        try:
            t = time.time(); r = llm('i has a apple yesterday', timeout=120); fixed, zh = r['fixed'], r['zh']
            report(4, 'ok', f'{time.time() - t:.1f} 秒：{fixed} / {zh}')
        except Exception as e:
            log('自检改错失败', repr(e)); report(4, 'fail', llm_error(e)); ok = False
    return ok


# ---- 界面：软 UI（neumorphism）----
# 凸起 / 凹陷的形状用 PIL 画进一张底图（带模糊阴影），文字和图标是 Canvas 上的文字项。
# 按钮平时的样子画在底图里，按下时在原位置盖一张「凹下去」的小图。
def px(v): return round(v * S)

_fonts = {}
def fnt(size, weight='normal', family=None):
    fam = family or FONT
    if weight == 'bold' and fam == FONT and FONT_BOLD: fam = FONT_BOLD  # Noto 的 Bold 在系统里叫「Noto Sans SC」，按 bold 取
    key = (fam, -px(size), weight)
    if key not in _fonts: _fonts[key] = tkfont.Font(family=key[0], size=key[1], weight=weight)
    return _fonts[key]

def wrap(text, f, width):
    """按像素宽度折行：英文按词、中文按字。width 是物理像素。"""
    out, line = [], ''
    for tok in re.findall(r"[A-Za-z0-9'’.,!?;:()\-\"]+\s*|\s+|.", text):
        if not line or f.measure(line + tok) <= width: line += tok
        else: out.append(line.rstrip()); line = tok.lstrip()
    if line.strip(): out.append(line.rstrip())
    return out

def fit(text, f, width, n=1):
    """折行后最多 n 行，超出的用 … 截断。"""
    ls = wrap(text, f, width)
    if len(ls) > n:
        ls, last = ls[:n], ls[n - 1]
        while last and f.measure(last + '…') > width: last = last[:-1]
        ls[-1] = last.rstrip() + '…'
    return '\n'.join(ls)

def _shape(size, box, r, ss=1, off=0.0):
    """圆角矩形蒙版（ss 倍超采样再按块缩小 = 抗锯齿，比 LANCZOS 快）。"""
    m = Image.new('L', (size[0] * ss, size[1] * ss), 0)
    x0, y0, x1, y1 = (v + off for v in box)
    ImageDraw.Draw(m).rounded_rectangle((x0 * ss, y0 * ss, x1 * ss - 1, y1 * ss - 1), r * ss, fill=255)
    return m if ss == 1 else m.reduce(ss)

def _blur(m, rad):
    """大半径模糊先缩小再放大：快好几倍，阴影本来就是糊的，看不出差别。"""
    f = 4 if rad >= 16 else 2 if rad >= 6 else 1
    if f == 1: return m.filter(ImageFilter.GaussianBlur(rad))
    return m.reduce(f).filter(ImageFilter.GaussianBlur(rad / f)).resize(m.size, Image.BILINEAR)

_sprites = {}
def _sprite(kind, w, h, r, d, fill):
    """凸起 / 凹陷形状连阴影画成一张 RGBA 小图，按尺寸缓存：同样大小的按钮、卡片只画一次，之后直接贴。"""
    key = (kind, round(w * S), round(h * S), r, d, fill, S)
    if key not in _sprites:
        W, H = key[1], key[2]; R, D = min(r, w / 2, h / 2) * S, d * S
        p = math.ceil(D * 3) + 2; size = (W + 2 * p, H + 2 * p); box = (p, p, p + W, p + H)
        if kind == 'raised':  # 左上白光、右下灰影
            sp = Image.new('RGBA', size, (0, 0, 0, 0))
            for off, col in ((D, DARK), (-D, LIGHT)):
                layer = Image.new('RGBA', size, col); layer.putalpha(_blur(_shape(size, box, R, 1, off), D * 1.1)); sp.alpha_composite(layer)
            layer = Image.new('RGBA', size, fill); layer.putalpha(_shape(size, box, R, 4)); sp.alpha_composite(layer)
        else:  # 凹陷：内侧左上灰影、右下白光
            base = Image.new('RGB', size, fill)
            for off, col in ((-D * 0.7, LIGHT), (D, DARK)):
                base.paste(col, (0, 0, *size), _blur(ImageChops.invert(_shape(size, box, R, 1, off)), D))
            sp = base.convert('RGBA'); sp.putalpha(_shape(size, box, R, 4))
        _sprites[key] = (sp, p)
    return _sprites[key]

def _stamp(img, kind, x, y, w, h, r, d, fill):
    sp, p = _sprite(kind, w, h, r, d, fill)
    ox, oy = round(x * S) - p, round(y * S) - p
    patch = img.crop((ox, oy, ox + sp.width, oy + sp.height)).convert('RGBA'); patch.alpha_composite(sp)
    img.paste(patch if img.mode == 'RGBA' else patch.convert(img.mode), (ox, oy))

def raised(img, x, y, w, h, r, d=8, fill=BG): _stamp(img, 'raised', x, y, w, h, r, d, fill)
def inset(img, x, y, w, h, r, d=5, fill=INSET): _stamp(img, 'inset', x, y, w, h, r, d, fill)

def ring(img, cx, cy, rad, color, width=1.1, fill=None):
    """抗锯齿的描边小圆（右上角的状态章）。"""
    ss, X0, Y0 = 4, math.floor((cx - rad - 2) * S), math.floor((cy - rad - 2) * S)
    n = math.ceil((cx + rad + 2) * S) - X0
    m = Image.new('L', (n * ss, n * ss), 0); dr = ImageDraw.Draw(m)
    box = [((cx - rad) * S - X0) * ss, ((cy - rad) * S - Y0) * ss, ((cx + rad) * S - X0) * ss, ((cy + rad) * S - Y0) * ss]
    if fill: dr.ellipse(box, fill=255); img.paste(fill, (X0, Y0, X0 + n, Y0 + n), m.resize((n, n), Image.LANCZOS)); m.paste(0, (0, 0, n * ss, n * ss))
    dr.ellipse(box, outline=255, width=round(width * S * ss))
    img.paste(color, (X0, Y0, X0 + n, Y0 + n), m.resize((n, n), Image.LANCZOS))


class Neu:
    """一块画布：底图 + 文字 + 按钮。build 时先画形状、加文字和按钮，最后 commit。"""
    def __init__(self, master, w, h, view=None):
        self.w, self.h = w, h
        self.c = tk.Canvas(master, width=px(w), height=px(view or h), bg=BG, highlightthickness=0, bd=0)
        self.c.bind('<ButtonPress-1>', self._press); self.c.bind('<ButtonRelease-1>', self._release); self.c.bind('<Motion>', self._motion)
        self.c.bind('<B1-Motion>', self._drag); self.drag = None
        self.btns, self.active = [], None
        self.reset(h)

    def reset(self, h=None):
        if h: self.h = h
        self.c.delete('all'); self.btns.clear(); self.active, self.ready = None, False
        self.img = Image.new('RGB', (px(self.w), px(self.h)), BG)

    def raised(self, *a, **k): raised(self.img, *a, **k)
    def inset(self, *a, **k): inset(self.img, *a, **k)

    def text(self, x, y, text, size=13, color=TEXT, weight='normal', family=None, anchor='nw', width=None, lines=None):
        f = fnt(size, weight, family)
        if width and lines: text, width = fit(text, f, px(width), lines), None
        return self.c.create_text(px(x), px(y), text=text, fill=color, font=f, anchor=anchor, tags='txt', **({'width': px(width)} if width else {}))

    def button(self, *a, **k):
        b = Btn(self, *a, **k); self.btns.append(b)
        if self.ready: b.make(); self.c.tag_raise('txt')
        return b

    def remove(self, b):
        if b in self.btns: self.btns.remove(b); b.destroy()

    def commit(self):
        self.photo = ImageTk.PhotoImage(self.img)
        self.c.tag_lower(self.c.create_image(0, 0, anchor='nw', image=self.photo))
        self.ready = True
        for b in self.btns: b.make()
        self.c.tag_raise('txt')

    def _hit(self, e):
        x, y = self.c.canvasx(e.x) / S, self.c.canvasy(e.y) / S
        return next((b for b in reversed(self.btns) if b.visible and b.contains(x, y)), None)

    def _press(self, e):
        b = self.active = self._hit(e); top = self.c.winfo_toplevel()
        if b:
            b.set_down(True)
            if b.hold: b.hold[0]()
        elif hasattr(top, 'chrome'): self.drag = (e.x_root - top.winfo_x(), e.y_root - top.winfo_y())

    def _drag(self, e):
        if self.active and hasattr(self.active, 'motion'): return self.active.motion(e)
        if self.drag:
            top = self.c.winfo_toplevel(); top.geometry(f'+{e.x_root - self.drag[0]}+{e.y_root - self.drag[1]}'); top.chrome.sync()

    def _release(self, e):
        b, self.active, self.drag = self.active, None, None
        if not b: return
        b.set_down(False)
        if b.hold: b.hold[1]()
        elif b.cmd and self._hit(e) is b: b.cmd()

    def _motion(self, e): self.c.configure(cursor='hand2' if self._hit(e) else '')


class Btn:
    """软 UI 按钮。kind：raised 凸起 / inset 凹槽（底栏「设置」）/ ring 描边小圆章。
    on=True 是选中（画成凹下去）。live=True 的按钮整张是贴图，可以隐藏、换颜色。"""
    def __init__(self, n, x, y, w, h, r=None, cmd=None, text='', icon='', size=13, d=4, color=TEXT, kind='raised',
                 on=False, live=False, hold=None, weight='normal', family=None, align='center'):
        self.n, self.box, self.r = n, (x, y, w, h), min(w, h) / 2 if r is None else r
        self.cmd, self.hold, self.text, self.icon, self.size, self.d, self.color = cmd, hold, text, icon, size, d, color
        self.kind, self.on, self.live, self.weight, self.family, self.align = kind, on, live or n.ready, weight, family, align
        self.visible, self.down, self.tile, self.lab = True, False, None, []
        if not self.live: self.draw(n.img, x, y, False)

    def draw(self, img, x, y, pressed):
        w, h = self.box[2:]
        if self.kind == 'ring':
            ring(img, x + w / 2, y + h / 2, w / 2 - 1, self.color, fill='#D8D8D8' if pressed else None)
        elif self.kind == 'inset' or self.on or pressed:
            inset(img, x, y, w, h, self.r, self.d + 2 if pressed and (self.kind == 'inset' or self.on) else self.d * 0.8)
        else:
            raised(img, x, y, w, h, self.r, self.d)

    def make(self):
        x, y, w, h = self.box
        pad = 1.5 if not self.live or self.kind == 'ring' else self.d * 3 + 3
        X0, Y0 = math.floor((x - pad) * S), math.floor((y - pad) * S)
        self.base = self.n.img.crop((X0, Y0, math.ceil((x + w + pad) * S), math.ceil((y + h + pad) * S)))
        self.lx, self.ly = x - X0 / S, y - Y0 / S
        self.tile = self.n.c.create_image(X0, Y0, anchor='nw')
        self.render(); self.layout()

    def render(self):
        self.ph = []
        for pressed in (False, True):
            if not self.live and not pressed: self.ph.append(None); continue
            im = self.base.copy(); self.draw(im, self.lx, self.ly, pressed); self.ph.append(ImageTk.PhotoImage(im))
        self.sync()

    def layout(self):
        c = self.n.c
        for it in self.lab: c.delete(it)
        x, y, w, h = self.box; cy = px(y + h / 2)
        fi, ft = fnt(self.size + 1, family=ICON), fnt(self.size, self.weight, self.family)
        if self.align == 'left':
            self.lab = [c.create_text(px(x + 18), cy, text=fit(self.text, ft, px(w - 60)), font=ft, fill=self.color, anchor='w', tags='txt')]
            if self.icon: self.lab.append(c.create_text(px(x + w - 32), cy, text=self.icon, font=fi, fill=self.color, anchor='center', tags='txt'))
        else:
            wi, wt = fi.measure(self.icon) if self.icon else 0, ft.measure(self.text) if self.text else 0
            gap = px(8) if self.icon and self.text else 0; left = px(x + w / 2) - (wi + gap + wt) / 2
            self.lab = []
            if self.icon: self.lab.append(c.create_text(left + wi / 2, cy, text=self.icon, font=fi, fill=self.color, anchor='center', tags='txt'))
            if self.text: self.lab.append(c.create_text(left + wi + gap + wt / 2, cy, text=self.text, font=ft, fill=self.color, anchor='center', tags='txt'))
        self.sync()

    def sync(self):
        if self.tile is None: return
        ph = self.ph[1] if self.down else self.ph[0]
        self.n.c.itemconfigure(self.tile, image=ph or '', state='normal' if self.visible and ph else 'hidden')
        for it in self.lab: self.n.c.itemconfigure(it, state='normal' if self.visible else 'hidden')

    def contains(self, px_, py_):
        x, y, w, h = self.box
        if self.kind == 'ring' or self.r >= min(w, h) / 2 - 0.5 and w == h:
            return (px_ - x - w / 2) ** 2 + (py_ - y - h / 2) ** 2 <= (w / 2) ** 2
        return x <= px_ <= x + w and y <= py_ <= y + h

    def set_down(self, v): self.down = v; self.sync()
    def show(self, v): self.visible = v; self.sync()
    def set(self, text=None, icon=None, color=None):
        if text is not None: self.text = text
        if icon is not None: self.icon = icon
        if color is not None and color != self.color:
            self.color = color
            if self.kind == 'ring' and self.tile is not None: self.render()
        if self.tile is not None: self.layout()

    def destroy(self):
        for it in [self.tile, *self.lab]:
            if it is not None: self.n.c.delete(it)


class Switch:
    """一排挨着的小胶囊（主页 EN / 中）：选中的凹下去、文字黑；其他凸起、文字灰。几个胶囊画在同一张贴图里，挨得近也不会互相盖住阴影。
    opts = ((值, 文字), ...)，点了调 cmd(值)。"""
    def __init__(self, n, x, y, w, h, gap, opts, value, cmd):
        self.n, self.x, self.y, self.w, self.h, self.gap, self.opts, self.value, self.on_pick = n, x, y, w, h, gap, opts, value, cmd
        self.visible, self.hold, self.hit, self.pressed, self.down, self.tile, self.lab = True, None, None, None, None, None, []
        self.cmd = lambda: self.hit and self.hit == self.pressed and self.on_pick(self.hit)  # 在哪个上按下就得在哪个上松开

    def box(self, i): return self.x + i * (self.w + self.gap), self.y, self.w, self.h

    def make(self):
        pad = 12; X0, Y0 = math.floor((self.x - pad) * S), math.floor((self.y - pad) * S)
        W = len(self.opts) * (self.w + self.gap) - self.gap
        self.base = self.n.img.crop((X0, Y0, math.ceil((self.x + W + pad) * S), math.ceil((self.y + self.h + pad) * S)))
        self.ox, self.oy = self.x - X0 / S, self.y - Y0 / S
        self.tile = self.n.c.create_image(X0, Y0, anchor='nw'); self.render()

    def render(self):
        im = self.base.copy(); c = self.n.c
        for it in self.lab: c.delete(it)
        self.lab = []
        for i, (v, t) in enumerate(self.opts):
            x, y, w, h = self.box(i); lx, ly = x - self.x + self.ox, y - self.y + self.oy
            if v == self.value or v == self.down: inset(im, lx, ly, w, h, h / 2, 4 if v == self.down else 2.4)
            else: raised(im, lx, ly, w, h, h / 2, 3)
            self.lab.append(c.create_text(px(x + w / 2), px(y + h / 2), text=t, font=fnt(12), fill=TEXT, tags='txt'))
        self.ph = ImageTk.PhotoImage(im); c.itemconfigure(self.tile, image=self.ph)

    def contains(self, x, y):
        self.hit = next((v for i, (v, _) in enumerate(self.opts) if self.box(i)[0] <= x <= self.box(i)[0] + self.w and self.y <= y <= self.y + self.h), None)
        return self.hit is not None

    def set_down(self, d):
        if d: self.pressed = self.hit
        self.down = self.hit if d else None; self.render()
    def select(self, v): self.value = v; self.render()


CHROMES = []

class Chrome:
    """无边框圆角窗口：Tk 窗口本身裁成圆角（SetWindowRgn，边缘有锯齿、往里缩 1 像素），
    后面垫一张跟着走的分层窗口画抗锯齿的圆角边和柔和阴影。阴影窗口当它的「主人」，系统会让阴影一直紧贴在它下面。
    拖窗口里任何空白处都能移动（见 Neu._press）。"""
    M = 30  # 阴影留边（逻辑像素）

    def __init__(self, win, owner=None, taskbar=False, R=48):
        self.R = R  # 圆角半径：= 里面卡片的圆角 + 卡片离窗口边的距离，内外两圈同心
        self.win = win; win.overrideredirect(True); win.update_idletasks()
        self.hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        sh = tk.Toplevel(win); sh.title('Quill shadow'); sh.overrideredirect(True); sh.geometry('1x1+-300+-300'); sh.update()
        self.shwnd = user32.GetParent(sh.winfo_id()) or sh.winfo_id()
        ex = user32.GetWindowLongW(self.shwnd, -20) | 0x08000000 | 0x80 | 0x20  # 不抢焦点 / 不进任务栏 / 鼠标穿透
        user32.ShowWindow(self.shwnd, 0)
        user32.SetWindowLongW(self.shwnd, -20, ex & ~0x80000); user32.SetWindowLongW(self.shwnd, -20, ex | 0x80000)
        if owner: user32.SetWindowLongPtrW(self.shwnd, -8, owner)
        user32.SetWindowLongPtrW(self.hwnd, -8, self.shwnd)
        if taskbar:
            user32.SetWindowLongW(self.hwnd, -20, (user32.GetWindowLongW(self.hwnd, -20) & ~0x80) | 0x40000)  # 任务栏里要有按钮
        self.geo, self.shown, self.taskbar = None, False, taskbar
        win.chrome = self; CHROMES.append(self)

    def sync(self):
        """跟着窗口挪阴影；最小化 / 隐藏时把阴影也藏起来。尺寸变了就重新裁圆角、重画阴影。"""
        h = self.hwnd
        if not user32.IsWindowVisible(h) or user32.IsIconic(h):
            if self.shown: user32.ShowWindow(self.shwnd, 0); self.shown = False
            return
        r = ctypes.wintypes.RECT(); user32.GetWindowRect(h, ctypes.byref(r)); x, y, w, hh = r.left, r.top, r.right - r.left, r.bottom - r.top
        M = px(self.M)
        if self.geo is None or self.geo[2:] != (w, hh):
            R = px(self.R); user32.SetWindowRgn(h, gdi32.CreateRoundRectRgn(1, 1, w - 1, hh - 1, 2 * R - 2, 2 * R - 2), True)
            size, box = (w + 2 * M, hh + 2 * M), (M, M, M + w, M + hh)
            img = Image.new('RGBA', size, (0, 0, 0, 0)); sm = Image.new('L', size, 0)
            ImageDraw.Draw(sm).rounded_rectangle((M + px(6), M + px(12), M + w - px(6), M + hh + px(4)), R, fill=110)
            shadow = Image.new('RGBA', size, (40, 40, 48, 0)); shadow.putalpha(_blur(sm, px(14))); img.alpha_composite(shadow)
            img.paste(Image.new('RGBA', size, BG), (0, 0), _shape(size, box, R, 2))
            ulw(self.shwnd, img, x - M, y - M)
        elif self.geo[:2] != (x, y):
            user32.SetWindowPos(self.shwnd, None, x - M, y - M, 0, 0, 0x15)
        self.geo = (x, y, w, hh)
        if not self.shown: user32.ShowWindow(self.shwnd, 8); self.shown = True
        if self.taskbar: set_win_icon(h)

def sync_chromes():
    for c in CHROMES[:]:
        try:
            if c.win.winfo_exists(): c.sync(); continue
        except tk.TclError: pass
        CHROMES.remove(c)

_icons = []
def set_win_icon(hwnd):
    """任务栏 / Alt+Tab 里的图标。Tk 显示窗口时会把它换回自己的羽毛，所以 sync 里发现被换了就再设一次；
    窗口类的默认图标也换成我们的，Tk 退回默认时也是它。"""
    if not _icons:
        for metric in (11, 49):  # 大：SM_CXICON；小：SM_CXSMICON
            n = user32.GetSystemMetrics(metric)
            _icons.append(user32.LoadImageW(None, str(HERE / 'icon_dial.ico'), 1, n, n, 0x10))  # IMAGE_ICON, LR_LOADFROMFILE
    big, small = _icons
    if not big or user32.SendMessageW(hwnd, 0x7F, 1, 0) == big: return  # WM_GETICON：已经是我们的
    user32.SendMessageW(hwnd, 0x80, 1, big); user32.SendMessageW(hwnd, 0x80, 0, small)  # WM_SETICON 大 / 小
    user32.SetClassLongPtrW(hwnd, -14, big); user32.SetClassLongPtrW(hwnd, -34, small)  # GCLP_HICON / GCLP_HICONSM

def wa_left():
    wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0); return wa.left

def sub_window(app, title, w, anchor=None):
    """设置 / 自检 / 记录 / 小弹窗：无边框圆角窗口。小弹窗（anchor=(屏幕x左, 屏幕y顶)）马上显示；其他的先藏着。"""
    t = tk.Toplevel(app.root); t.withdraw(); t.title(title); t.configure(bg=BG)
    Chrome(t, owner=app.chrome.hwnd, R=40 if anchor else 48); t.chrome.side = not anchor  # 子窗口卡片 r28 离边 20；小弹窗按钮 r20 离边 20
    if anchor:
        wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        x = min(max(wa.left + px(8), anchor[0]), wa.right - px(w) - px(8)); y = anchor[1]
        t.geometry(f'+{x}+{y}')
        t.after(10, lambda: (t.deiconify(), t.chrome.sync()))  # 内容画好后再露面
    return t  # 设置 / 记录 / 自检先藏着，由 App._open 摆好位置再显示

def place_side(app, t, w):
    """放在主窗口和已经开着的子窗口左边；左边放不下就退回主窗口旁边、往下错开一点。"""
    side = [c.win for c in CHROMES if getattr(c, 'side', False) and c.win is not t and c.win.winfo_exists() and c.win.winfo_viewable()]
    x, y = min([app.root.winfo_x()] + [v.winfo_x() for v in side]) - px(w) - px(24), app.root.winfo_y()
    if x < wa_left(): x, y = max(wa_left(), app.root.winfo_x() - px(w) - px(24)), app.root.winfo_y() + px(40) * len(side)
    t.geometry(f'+{x}+{y}')

BAR_BASE = (0.34, 0.68, 1.0, 0.55, 0.84, 0.40)  # 苹果 SpeechAnalyzer 图标那种 6 根圆头声波条，平时的高矮
BAR_COL = {'loading': ('#D4D4D9', '#A9A9AF'), 'error': ('#FF9186', '#D64541')}
BARS, ACCENT = ('#4A4A50', '#0E0E10'), INK  # 声波条上深灰下黑；外圈刻度亮起来的颜色

def bar_heights(state, lv, t, sm):
    """每根条现在的高度（0~1，已平滑）：rec 随音量跳，busy 波浪流过，loading 慢慢起伏，ready 轻轻呼吸。"""
    for i, b in enumerate(BAR_BASE):
        if state == 'rec': g = 0.16 + min(1.0, lv * 1.7) * b * (0.72 + 0.28 * math.sin(t * 9 + i * 1.3))
        elif state == 'busy': g = 0.22 + 0.62 * max(0.0, math.sin(t * 5.5 - i * 0.75))
        elif state == 'loading': g = 0.2 + 0.25 * max(0.0, math.sin(t * 3 - i * 0.7))
        elif state == 'error': g = b * 0.55
        else: g = b * (0.9 + 0.1 * math.sin(t * 1.5 + i * 0.9))
        sm[i] += (g - sm[i]) * (0.45 if g > sm[i] else 0.22)
    return [max(0.12, min(1.0, v)) for v in sm]

_grads = {}
def _grad(size, y0, y1, top, bot):
    """竖向渐变（上 top 下 bot），按尺寸缓存。"""
    key = (size, round(y0), round(y1), top, bot)
    if key not in _grads:
        ys = np.clip((np.arange(size[1]) - y0) / max(1.0, y1 - y0), 0, 1) * 255
        m = Image.fromarray(ys.astype(np.uint8)[:, None]).resize(size, Image.NEAREST)
        _grads[key] = Image.composite(Image.new('RGBA', size, bot), Image.new('RGBA', size, top), m)
    return _grads[key]

def paint_bars(img, cx, cy, height, state, lv, t, sm):
    """在 RGBA 图上画 6 根渐变声波条（主页圆盘和浮窗共用），坐标、高度都是像素。"""
    n = len(BAR_BASE); bw = height * 0.13; gap = bw * 0.78; x0 = cx - (n * bw + (n - 1) * gap) / 2
    m = Image.new('L', img.size, 0); d = ImageDraw.Draw(m)
    for i, h in enumerate(bar_heights(state, lv, t, sm)):
        hh = max(bw, h * height) / 2; x = x0 + i * (bw + gap)
        d.rounded_rectangle((x, cy - hh, x + bw, cy + hh), bw / 2, fill=255)
    img.paste(_grad(img.size, cy - height / 2, cy + height / 2, *BAR_COL.get(state, BARS)), (0, 0), m)



# ---- 底部浮窗：分层窗口（UpdateLayeredWindow），整张图自己用 PIL 画，阴影是真半透明 ----
gdi32 = ctypes.windll.gdi32
_vp, _u32 = ctypes.c_void_p, ctypes.c_uint32
for _fn, _res, _args in ((gdi32.CreateCompatibleDC, _vp, [_vp]), (gdi32.CreateDIBSection, _vp, [_vp, _vp, _u32, ctypes.POINTER(_vp), _vp, _u32]),
                         (gdi32.SelectObject, _vp, [_vp, _vp]), (gdi32.DeleteObject, ctypes.c_int, [_vp]), (gdi32.DeleteDC, ctypes.c_int, [_vp]),
                         (gdi32.CreateRoundRectRgn, _vp, [ctypes.c_int] * 6), (user32.SetWindowRgn, ctypes.c_int, [_vp, _vp, ctypes.c_int]),
                         (user32.SetWindowLongPtrW, _vp, [_vp, ctypes.c_int, _vp]),
                         (user32.LoadImageW, _vp, [_vp, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]),
                         (user32.SendMessageW, _vp, [_vp, ctypes.c_uint, _vp, _vp]), (user32.SetClassLongPtrW, _vp, [_vp, ctypes.c_int, _vp]),
                         (user32.SetWindowPos, ctypes.c_int, [_vp, _vp] + [ctypes.c_int] * 4 + [_u32]),  # HWND_TOPMOST=-1 要按指针传，按 int 传在 64 位上会坏
                         (user32.GetDC, _vp, [_vp]), (user32.ReleaseDC, ctypes.c_int, [_vp, _vp]),
                         (user32.UpdateLayeredWindow, ctypes.c_int, [_vp, _vp, _vp, _vp, _vp, _vp, _u32, _vp, _u32])):
    _fn.restype, _fn.argtypes = _res, _args

class _BLEND(ctypes.Structure): _fields_ = [(n, ctypes.c_ubyte) for n in ('op', 'flags', 'alpha', 'fmt')]
class _BMIH(ctypes.Structure):
    _fields_ = [('size', _u32), ('w', ctypes.c_int32), ('h', ctypes.c_int32), ('planes', ctypes.c_uint16), ('bits', ctypes.c_uint16),
                ('comp', _u32), ('isize', _u32), ('xp', ctypes.c_int32), ('yp', ctypes.c_int32), ('cu', _u32), ('ci', _u32)]

def ulw(hwnd, img, x, y):
    """把 RGBA 图整张贴成分层窗口的样子（每个像素带透明度）。"""
    w, h = img.size; r, g, b, a = img.split()
    data = Image.merge('RGBA', [ImageChops.multiply(c, a) for c in (r, g, b)] + [a]).tobytes('raw', 'BGRA')  # 要预乘透明度
    sdc = user32.GetDC(None); mdc = gdi32.CreateCompatibleDC(sdc); bits = _vp()
    bi = _BMIH(ctypes.sizeof(_BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bmp = gdi32.CreateDIBSection(mdc, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, data, len(data)); old = gdi32.SelectObject(mdc, bmp)
    pt, sz, src = ctypes.wintypes.POINT(x, y), ctypes.wintypes.SIZE(w, h), ctypes.wintypes.POINT(0, 0)
    if not user32.UpdateLayeredWindow(hwnd, sdc, ctypes.byref(pt), ctypes.byref(sz), mdc, ctypes.byref(src), 0, ctypes.byref(_BLEND(0, 0, 255, 1)), 2) and not ulw.warned:
        ulw.warned = True; log('浮窗画不出来：UpdateLayeredWindow 错误', ctypes.windll.kernel32.GetLastError())
    gdi32.SelectObject(mdc, old); gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(None, sdc)

ulw.warned = False

_pfonts = {}
def pfont(size, kind='r'):
    """PIL 用的字体：r 正文 / b 粗一点 / i 图标（项目里的 Noto Sans SC，没有就用系统雅黑）。"""
    key = (px(size), kind)
    if key not in _pfonts:
        fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        own = HERE / 'fonts'
        path = {'r': own / 'NotoSansSC-Medium.otf' if (own / 'NotoSansSC-Medium.otf').exists() else fonts / 'msyh.ttc',
                'b': own / 'NotoSansSC-Bold.otf' if (own / 'NotoSansSC-Bold.otf').exists() else fonts / 'msyhbd.ttc', 'i': fonts / ('SegoeIcons.ttf' if (fonts / 'SegoeIcons.ttf').exists() else 'segmdl2.ttf')}[kind]
        try: _pfonts[key] = ImageFont.truetype(str(path), key[0])
        except Exception: _pfonts[key] = ImageFont.truetype(str(fonts / 'msyh.ttc'), key[0])
    return _pfonts[key]

def changed_words(raw, fixed):
    """改后的句子按词切开，标出改过 / 新加的词（忽略大小写和标点的差别）。"""
    norm = lambda w: re.sub(r"[^\w']", '', w.lower())
    a, b = raw.split(), fixed.split(); marks = [False] * len(b)
    if CJK.search(raw): return [(w + ' ', INK, False) for w in b]  # 说的是中文：英文是翻出来的，没有「改了哪几个词」
    for op, _, _, j1, j2 in difflib.SequenceMatcher(None, [norm(w) for w in a], [norm(w) for w in b], autojunk=False).get_opcodes():
        if op in ('replace', 'insert'): marks[j1:j2] = [True] * (j2 - j1)
    return [(w + ' ', INK, m) for w, m in zip(b, marks)]

def flow(runs, f, width, max_lines, keep='first'):
    """把 (文字, 颜色, 是否标记) 片段按像素宽度折行，返回每行的 [(x, 片段, 颜色, 标记)]。keep='last' 只留最后几行（实时文字）。"""
    lines, cur, x = [], [], 0.0
    for text, color, mark in runs:
        for tok in re.findall(r"[A-Za-z0-9'’.,!?;:()\-\"]+\s*|\s+|.", text):
            if cur and x + f.getlength(tok.rstrip()) > width:
                lines.append(cur); cur, x, tok = [], 0.0, tok.lstrip()
                if not tok: continue
            cur.append((x, tok, color, mark)); x += f.getlength(tok)
    if cur: lines.append(cur)
    if len(lines) > max_lines:
        if keep == 'last': return lines[-max_lines:]
        lines = lines[:max_lines]; last = lines[-1]
        while len(last) > 1 and last[-1][0] + f.getlength(last[-1][1].rstrip() + '…') > width: last.pop()
        x0, t, c, m = last[-1]; last[-1] = (x0, t.rstrip() + '…', c, m)
    return lines

def draw_lines(d, lines, f, x, y, lh):
    asc, desc = f.getmetrics(); dy = (lh - asc - desc) / 2
    for ln in lines:
        for x0, tok, color, mark in ln:
            if mark and tok.strip():
                d.rounded_rectangle((x + x0 - px(3), y + px(2), x + x0 + f.getlength(tok.rstrip()) + px(3), y + lh - px(2)), px(6), fill='#D3D3D8')
            d.text((x + x0, y + dy), tok, font=f, fill=color)
        y += lh
    return y

OV_SUB = '#3A3A3C'  # 浮窗里第二层的字（还没定下来的实时识别、原话、中文意思、提示）和细线图标：在玻璃上也要看得清，用深灰不用浅灰

class Overlay:
    """屏幕底部中间的浮窗：说话时实时显示识别的字，之后显示改好的结果（标出改动），或者给两个版本让你点。
    不抢焦点：点它光标还留在你原来的输入框里，所以点了就能直接粘贴进去。"""
    W, M, ALPHA, ROW_H = 600, 28, 215, 74  # ROW_H：一行样式的最小高度（英文一行 24 + 中文一行 18 + 间隔 2 + 上下各 15）； 卡片宽、四周给阴影留的边（逻辑像素）、卡片底色不透明度（215/255，半透明，后面再乱也看得清字）

    def __init__(self, app):
        self.app = app
        w = self.w = tk.Toplevel(app.root); w.title('Quill overlay'); w.overrideredirect(True); w.geometry('1x1+-300+-300')
        w.bind('<Motion>', self.motion); w.bind('<Leave>', lambda e: self.motion(None)); w.bind('<ButtonRelease-1>', self.click)
        w.update()
        self.hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
        ex = user32.GetWindowLongW(self.hwnd, -20) | 0x08000000 | 0x80 | 0x8  # 不抢焦点 / 不进任务栏 / 置顶
        user32.ShowWindow(self.hwnd, 0)
        user32.SetWindowLongW(self.hwnd, -20, ex & ~0x80000); user32.SetWindowLongW(self.hwnd, -20, ex | 0x80000)  # 分层：要先清掉再设，否则 UpdateLayeredWindow 报参数错误（87）
        self.mode, self.d, self.visible, self.pinned, self.inside, self.hover, self.timer = None, {}, False, False, False, None, None
        self.mouse_down = False  # 上一帧鼠标键是不是按着（点浮窗外面就关）
        self.cards, self.base, self.hits, self.wgeo, self.bar_c, self.h = {}, None, [], None, (0, 0), self.ROW_H
        self.backdrop = (None, None)  # 浮窗后面那块屏幕的模糊图：(屏幕上的范围, 图)，一次截得比浮窗高，浮窗变高时直接从里面裁
        self.sm, self.lv, self.t0 = [0.0] * 6, 0.0, time.time()

    # 对外：每种状态
    def listen(self, by_key):
        if self.visible: threading.Thread(target=self.refresh_backdrop, args=(True,), daemon=True).start()  # 浮窗还开着就说下一句：背景换新
        self.set('rec', done='', part='', by_key=by_key)
    def live(self, done, part):
        if self.mode == 'rec': self.d.update(done=done, part=part); self.render()
    def think(self, raw):
        self.set('busy', raw=raw)
    def result(self, raw, fixed, zh, by_key):
        self.set('result', raw=raw, fixed=fixed, zh=zh, by_key=by_key); self.arm()
    def pick(self, raw, opts, zh=None):
        self.set('pick', raw=raw, opts=opts, zh=zh)
    def set_zh(self, zh):
        if self.mode in ('result', 'pick'):
            self.d['zh'] = zh; self.render()
            if self.mode == 'result': self.arm()
    def notice(self, text, level):
        self.set('notice', text=text, level=level); self.arm()

    def set(self, mode, **d):
        self.mode, self.d, self.pinned, self.hover = mode, d, False, None
        if self.timer: self.w.after_cancel(self.timer); self.timer = None
        self.render()

    def arm(self, secs=SHOW_SECONDS):
        if self.timer: self.w.after_cancel(self.timer)
        self.timer = self.w.after(int(secs * 1000), self.timeout)

    def timeout(self):
        self.timer = None
        if self.pinned or self.mode == 'pick': return
        if self.inside: return self.arm(2)  # 鼠标还在上面：再等等
        self.hide()

    def hide(self):
        if self.timer: self.w.after_cancel(self.timer); self.timer = None
        self.mode, self.pinned = None, False
        if self.visible: user32.ShowWindow(self.hwnd, 0); self.visible = False

    # 画
    def card(self, h):
        """液态玻璃卡片的固定部分（按高度缓存）：under = 卡片外圈的柔和投影；face = 圆角面的形状（玻璃底每帧贴在这里）；
        over = 玻璃上的光：顶部一层往下淡出的高光、贴着边缘的一圈亮光（上面亮、往下几乎没有，是光不是框）、底部一点点暗。
        没有描边。投影只在卡片外面（卡片是透的，垫在下面会透出来）。"""
        if h not in self.cards:
            M, W = self.M, self.W; size = (px(W + 2 * M), px(h + 2 * M)); box = (px(M), px(M), px(M + W), px(M + h)); R = px(26)
            face = _shape(size, box, R, 2)
            sh = Image.new('L', size, 0); ImageDraw.Draw(sh).rounded_rectangle((px(M + 8), px(M + 10), px(M + W - 8), px(M + h + 6)), R, fill=80)
            under = Image.new('RGBA', size, (30, 30, 40, 0)); under.putalpha(ImageChops.multiply(_blur(sh, px(14)), ImageChops.invert(face)))
            ys = (np.arange(size[1]) - box[1]) / max(1, box[3] - box[1])  # 卡片里从上到下 0→1
            col = lambda v: Image.fromarray(np.repeat(np.clip(v, 0, 255).astype(np.uint8)[:, None], size[0], axis=1))
            sheen = col(80 * np.clip(1 - ys / 0.55, 0, 1) ** 1.6)                    # 顶部高光
            inner = face.filter(ImageFilter.MinFilter(2 * round(1.6 * S) + 1))       # 往里收一点的形状
            rim = ImageChops.subtract(face, inner).filter(ImageFilter.GaussianBlur(0.6 * S))
            rim = ImageChops.multiply(rim, col(40 + 190 * np.clip(1 - ys / 0.4, 0, 1) ** 1.3))  # 边缘亮光：上亮下暗
            light = ImageChops.multiply(ImageChops.lighter(sheen, rim), face)
            over = Image.new('RGBA', size, (255, 255, 255, 0)); over.putalpha(light)
            dark = Image.new('RGBA', size, (40, 40, 50, 0)); dark.putalpha(ImageChops.multiply(col(22 * np.clip((ys - 0.65) / 0.35, 0, 1)), face))
            over.alpha_composite(dark)
            self.cards[h] = {'under': under, 'face': face, 'over': over}
        return self.cards[h]

    def layer(self, h):
        """一张透明的内容层（文字、图标画在上面，blit 时盖在玻璃上）。"""
        self.h = h; self.card(h)
        return Image.new('RGBA', (px(self.W + 2 * self.M), px(h + 2 * self.M)), (0, 0, 0, 0))

    TALL = 420  # 背景一次截多高（逻辑像素，比浮窗最高的时候还高）

    def grab_backdrop(self, box):
        """浮窗后面那块屏幕：截下来 → 缩小模糊 → 稍微加饱和、提亮（上面深色的字才看得清）。只在内存里用。
        make_demo.py 会把它换成固定的底色。"""
        w, h = box[2] - box[0], box[3] - box[1]
        im = ImageGrab.grab(box).convert('RGB')
        im = im.resize((max(1, w // 6), max(1, h // 6)), Image.BILINEAR).filter(ImageFilter.GaussianBlur(2.2)).resize((w, h), Image.BICUBIC)
        im = Image.blend(ImageEnhance.Color(im).enhance(1.3), Image.new('RGB', (w, h), (247, 247, 249)), 0.45)
        return im.convert('RGBA')

    def backdrop_rect(self):
        """浮窗底边往上 TALL 那么高、和浮窗一样宽的一块（屏幕物理像素）。"""
        wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        w = px(self.W + 2 * self.M); x = wa.left + (wa.right - wa.left - w) // 2; bottom = wa.bottom + px(self.M) - px(18)
        return (x, bottom - px(self.TALL), x + w, bottom)

    def refresh_backdrop(self, hide_self=False):
        """截浮窗后面的屏幕当玻璃底。平时在浮窗露面之前截（这时它还没显示，截不到自己）。
        hide_self：浮窗已经开着（接着说下一句）时，只在截的这一下把浮窗排除在截屏外（WDA_EXCLUDEFROMCAPTURE），截完马上恢复，
        所以别人截屏 / 录屏照样看得到浮窗。"""
        r = self.backdrop_rect()
        try:
            if hide_self:
                user32.SetWindowDisplayAffinity(ctypes.c_void_p(self.hwnd), 0x11); ctypes.windll.dwmapi.DwmFlush()
            try: self.backdrop = (r, self.grab_backdrop(r))
            finally:
                if hide_self: user32.SetWindowDisplayAffinity(ctypes.c_void_p(self.hwnd), 0)
        except Exception: self.backdrop = (r, None)  # 截不了（锁屏 / UAC 那种安全桌面）：退回半透明纯色

    def glass(self, box):
        """玻璃底：从截好的背景里裁出浮窗现在这块；对不上就是半透明纯色。"""
        r, im = self.backdrop
        if im is not None and r[0] <= box[0] and r[1] <= box[1] and box[2] <= r[2] and box[3] <= r[3]:
            return im.crop((box[0] - r[0], box[1] - r[1], box[2] - r[0], box[3] - r[1]))
        return Image.new('RGBA', (box[2] - box[0], box[3] - box[1]), (233, 233, 235, self.ALPHA))

    def render_row(self):
        """正在听 / 改语病 / 结果 / 提示：一行。左边声波条（每帧在 blit 里画；提示是彩色「!」），中间文字上下居中，
        结果最右边一个勾（固定后换成图钉，只复制没输入时是复制图标）。"""
        M, W, d, m = self.M, self.W, self.d, self.mode
        right = 56 if m == 'result' else 22  # 文字右边留给图标的宽度
        TX, TW = px(M + 66), px(W - 66 - right)
        blocks = []  # (行, 字体, 行高)
        if m == 'rec':
            f = pfont(14); runs = [(d['done'] + ' ', INK, False)] * bool(d['done']) + [(d['part'], OV_SUB, False)] * bool(d['part'])
            blocks.append((flow(runs or [('请说中文…' if cfg['lang'] == 'zh' else '请说英文…', OV_SUB, False)], f, TW, 3, 'last'), f))
        elif m == 'busy':
            f = pfont(14); blocks.append((flow([(d['raw'] or '识别中…', OV_SUB, False)], f, TW, 3), f))
        elif m == 'result':
            f = pfont(14); blocks.append((flow(changed_words(d['raw'], d['fixed']), f, TW, 3), f))
            f2 = pfont(12)
            if d['zh'] != '': blocks.append((flow([(d['zh'] if d['zh'] is not None else '翻译中…', OV_SUB, False)], f2, TW, 2), f2))  # 出中文：没有翻译那行
        else:
            tcol = {'err': RED, 'ok': GREEN}.get(d.get('level'), ORANGE)
            f = pfont(14); blocks.append((flow([(d['text'], INK, False)], f, TW, 3), f))
        th = sum(len(ls) * f.size * 1.5 for ls, f in blocks) / S + 2 * (len(blocks) - 1)  # 文字块总高（逻辑像素）
        h = max(self.ROW_H, round(th + 30)); self.hits = []  # 最矮也按「英文一行 + 中文一行」：一句话从正在听到出结果一样高，不会突然变大
        img = self.layer(h); dr = ImageDraw.Draw(img); cy = px(M + h / 2)
        y = px(M) + (px(h) - px(th)) / 2
        for ls, f in blocks:
            draw_lines(dr, ls, f, TX, y, f.size * 1.5); y += len(ls) * f.size * 1.5 + px(2)
        self.bar_c = (px(M + 36), cy)
        if m == 'notice':
            cx = px(M + 36); dr.ellipse((cx - px(14), cy - px(14), cx + px(14), cy + px(14)), fill=tcol)
            dr.text((cx, cy), '!', font=pfont(13), fill='white', anchor='mm')
        if m == 'result':
            badge = self.badge('pin' if self.pinned else 'ok' if d.get('by_key') else 'copy')
            img.alpha_composite(badge, (px(M + W - 34) - badge.width // 2, round(cy) - badge.height // 2))
        self.base = img; self.blit()

    _badges = {}
    def badge(self, kind):
        """结果右边的小图标，苹果 checkmark.circle 那种细线：浅灰细圆圈 + 深灰细勾（勾是矢量线画的，4 倍大画再缩小，边缘干净）；
        固定后是图钉、只复制时是复制图标（字形，同样的颜色）。"""
        if kind not in self._badges:
            n, ss = px(30), 4; N = n * ss; im = Image.new('RGBA', (N, N), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
            ring_w = round(1.3 * S * ss); d.ellipse((ring_w, ring_w, N - ring_w, N - ring_w), outline=OV_SUB, width=ring_w)
            if kind == 'ok':
                pts = [(0.31 * N, 0.52 * N), (0.45 * N, 0.65 * N), (0.70 * N, 0.37 * N)]; w = round(1.8 * S * ss)
                d.line(pts, fill=INK, width=w, joint='curve')
                for x, y in (pts[0], pts[-1]): d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=INK)  # 圆头
            else:
                f = ImageFont.truetype(pfont(1, 'i').path, round(11 * S * ss))
                d.text((N / 2, N / 2), I_PIN if kind == 'pin' else I_COPY, font=f, fill=INK, anchor='mm')
            Overlay._badges[kind] = im.resize((n, n), Image.LANCZOS)
        return self._badges[kind]

    def render(self):
        """按当前内容重画卡片和文字（左边的声波条每帧另外画）。"""
        if not self.mode: return
        (self.render_pick if self.mode == 'pick' else self.render_row)()

    def render_pick(self):
        """挑版本：上面标题 + 提示，下面两张小卡（自然 / 口语）和中文意思。"""
        M, W, d = self.M, self.W, self.d
        key = key_name(cfg['hotkey']); L, TW = px(M + 24), px(W - 48)  # 文字左边、宽度（像素）
        title, hint, icon, tcol = '选一个版本', f'点一下输入 · 按「{key}」选 1 · Esc 放弃', '\uE8FD', TEXT
        blocks, y = [], 62  # (画法, 参数)；y 是逻辑像素
        if True:
            f = pfont(14); self.hits = []
            for i, (name, text) in enumerate(zip(('自然', '口语'), d['opts'])):
                lines = flow(changed_words(d['raw'], text), f, TW - px(76), 3); h = len(lines) * 16 * 1.55 + 22
                blocks.append(('option', i, name, lines, f, y, h)); y += h + 10
            f2 = pfont(12); lines = flow([('意思：' + (d['zh'] if d.get('zh') is not None else '翻译中…'), OV_SUB, False)], f2, TW, 2)
            blocks.append(('lines', lines, f2, y + 2)); y += 2 + len(lines) * 12 * 1.55
        h = int(y + 20)

        img = self.layer(h); dr = ImageDraw.Draw(img)
        cx, cy = px(M + 39), px(M + 33)
        dr.ellipse((cx - px(15), cy - px(15), cx + px(15), cy + px(15)), fill=INK)  # 左上角小圆章
        dr.text((cx, cy), icon, font=pfont(13, 'i'), fill='white', anchor='mm')
        dr.text((px(M + 64), cy), title, font=pfont(14), fill=tcol, anchor='lm')
        if hint: dr.text((px(M + W - 24), cy), hint, font=pfont(12), fill=OV_SUB, anchor='rm')
        for b in blocks:
            if b[0] == 'lines':
                _, lines, f, by = b; draw_lines(dr, lines, f, L, px(M + by), f.size * 1.55)
            else:
                _, i, name, lines, f, by, bh = b
                x0, y0, x1, y1 = px(M + 16), px(M + by), px(M + W - 16), px(M + by + bh)
                hov = self.hover == i
                dr.rounded_rectangle((x0, y0, x1, y1), px(18), fill=(255, 255, 255, 175) if hov else (255, 255, 255, 80))  # 玻璃上再叠一层半透明白
                cy2 = y0 + px(11) + f.size * 1.55 / 2
                dr.rounded_rectangle((x0 + px(12), cy2 - px(12), x0 + px(60), cy2 + px(12)), px(12), fill=INK if hov or i == 0 else OV_SUB)
                dr.text((x0 + px(36), cy2), name, font=pfont(12), fill='white', anchor='mm')
                draw_lines(dr, lines, f, x0 + px(72), y0 + px(11), f.size * 1.55)
                self.hits.append((x0, y0, x1, y1, i))
        self.base = img; self.blit()

    def frame(self, level):
        """每帧调：说话 / 思考时让左上角小光球动起来；结果 / 提示显示着时，点屏幕上浮窗以外的地方就关（固定了的、挑版本的不关）。"""
        down = key_down(0x01) or key_down(0x02)
        if down and not self.mouse_down and self.visible and self.wgeo and self.mode in ('result', 'notice') and not self.pinned:
            pt = ctypes.wintypes.POINT(); user32.GetCursorPos(ctypes.byref(pt))
            w, h, x, y = self.wgeo; M = px(self.M)
            if not (x + M <= pt.x <= x + w - M and y + M <= pt.y <= y + h - M): self.hide()
        self.mouse_down = down
        if self.visible and self.mode in ('rec', 'busy', 'result'): self.blit(level)

    def blit(self, level=0.0):
        c = self.cards[self.h]; img = c['under'].copy()
        wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        x, y = wa.left + (wa.right - wa.left - img.width) // 2, wa.bottom - img.height + px(self.M) - px(18)
        geo = (img.width, img.height, x, y)
        if not self.visible: self.refresh_backdrop()  # 露面之前截后面的屏幕（这时还截不到自己）
        img.paste(self.glass((x, y, x + img.width, y + img.height)), (0, 0), c['face'])
        img.alpha_composite(c['over']); img.alpha_composite(self.base)
        if self.mode in ('rec', 'busy', 'result'):
            st = 'ready' if self.mode == 'result' else self.mode; t = time.time() - self.t0
            self.lv += ((level if st == 'rec' else 0) - self.lv) * 0.35
            n, ss = px(34), 3; Lr = Image.new('RGBA', (n * ss, n * ss), (0, 0, 0, 0))
            paint_bars(Lr, n * ss / 2, n * ss / 2, n * ss * 0.78, st, self.lv, t, self.sm)
            img.alpha_composite(Lr.resize((n, n), Image.LANCZOS), (round(self.bar_c[0]) - n // 2, round(self.bar_c[1]) - n // 2))
        if self.wgeo != geo: self.wgeo = geo; self.w.geometry(f'{img.width}x{img.height}+{x}+{y}')
        if not self.visible:
            user32.ShowWindow(self.hwnd, 8); user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x13); self.visible = True  # 显示但不激活
        ulw(self.hwnd, img, x, y)

    # 鼠标
    def hit(self, x, y): return next((i for x0, y0, x1, y1, i in self.hits if x0 <= x <= x1 and y0 <= y <= y1), None)

    def motion(self, e):
        self.inside = e is not None
        if self.mode == 'pick':
            h = self.hit(e.x, e.y) if e else None
            if h != self.hover: self.hover = h; self.render()
            self.w.configure(cursor='hand2' if h is not None else '')

    def click(self, e):
        if self.mode == 'pick':
            i = self.hit(e.x, e.y)
            if i is not None: self.app.choose(i)
        elif self.pinned: self.hide()
        else:
            self.pinned = True; self.render()
            if self.timer: self.w.after_cancel(self.timer); self.timer = None


class Ball:
    """悬浮球：主窗口缩小成屏幕边上的一个小圆球（和图标一个样子），声波条跟着状态动。
    点一下回到主窗口；按住拖可以挪位置（记在 settings.json 的 ball）。不抢焦点，快捷键照常用。"""
    D, M = 60, 16  # 球直径、四周阴影留边（逻辑像素）

    def __init__(self, app):
        self.app = app
        w = self.w = tk.Toplevel(app.root); w.title('Quill ball'); w.overrideredirect(True); w.geometry('1x1+-300+-300')
        w.bind('<ButtonPress-1>', self.press); w.bind('<B1-Motion>', self.drag); w.bind('<ButtonRelease-1>', self.release)
        w.configure(cursor='hand2'); w.update()
        self.hwnd = user32.GetParent(w.winfo_id()) or w.winfo_id()
        ex = user32.GetWindowLongW(self.hwnd, -20) | 0x08000000 | 0x80 | 0x8  # 不抢焦点 / 不进任务栏 / 置顶
        user32.ShowWindow(self.hwnd, 0)
        user32.SetWindowLongW(self.hwnd, -20, ex & ~0x80000); user32.SetWindowLongW(self.hwnd, -20, ex | 0x80000)  # 分层（先清再设，见 Overlay）
        self.visible, self.start, self.moved, self.base = False, None, False, None
        self.sm, self.lv, self.t0, self.x, self.y = [0.0] * 6, 0.0, time.time(), 0, 0

    def make_base(self):
        """球底：柔和投影 + 上亮下暗的浅灰圆盘（只画一次）。"""
        n = px(self.D + 2 * self.M); ss = 3; N = n * ss; C = N / 2; R = px(self.D / 2) * ss
        img = Image.new('RGBA', (N, N), (0, 0, 0, 0))
        sh = Image.new('L', (N, N), 0); ImageDraw.Draw(sh).ellipse((C - R, C - R + N * 0.04, C + R, C + R + N * 0.04), fill=120)
        layer = Image.new('RGBA', (N, N), (40, 40, 48, 255)); layer.putalpha(_blur(sh, px(7) * ss)); img.alpha_composite(layer)
        disc = Image.new('L', (N, N), 0); ImageDraw.Draw(disc).ellipse((C - R, C - R, C + R, C + R), fill=255)
        img.paste(_grad((N, N), C - R, C + R, '#F6F6F7', '#DCDCDF'), (0, 0), disc)
        self.base = img.resize((n, n), Image.LANCZOS)

    def show(self):
        if not self.base: self.make_base()
        n = self.base.width; wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        x, y = cfg.get('ball') or (wa.right - n, (wa.top + wa.bottom) // 2 - n // 2)  # 默认贴在屏幕右边中间
        self.x, self.y = min(max(wa.left, x), wa.right - n), min(max(wa.top, y), wa.bottom - n)
        self.w.geometry(f'{n}x{n}+{self.x}+{self.y}')
        self.visible = True; self.frame(self.app.state, 0.0)
        user32.ShowWindow(self.hwnd, 8); user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, 0x13)  # 显示但不激活、置顶

    def hide(self):
        if self.visible: user32.ShowWindow(self.hwnd, 0); self.visible = False

    def frame(self, state, level):
        if not self.visible: return
        t = time.time() - self.t0; self.lv += ((level if state == 'rec' else 0) - self.lv) * 0.35
        n, ss = self.base.width, 3; L = Image.new('RGBA', (n * ss, n * ss), (0, 0, 0, 0))
        paint_bars(L, n * ss / 2, n * ss / 2, px(self.D) * ss * 0.46, state, self.lv, t, self.sm)
        img = self.base.copy(); img.alpha_composite(L.resize((n, n), Image.LANCZOS)); ulw(self.hwnd, img, self.x, self.y)

    def press(self, e): self.start, self.moved = (e.x_root, e.y_root, self.x, self.y), False

    def drag(self, e):
        if not self.start: return
        dx, dy = e.x_root - self.start[0], e.y_root - self.start[1]
        if abs(dx) + abs(dy) > px(4): self.moved = True
        if self.moved:
            self.x, self.y = self.start[2] + dx, self.start[3] + dy
            self.w.geometry(f'+{self.x}+{self.y}'); user32.SetWindowPos(self.hwnd, None, self.x, self.y, 0, 0, 0x15)

    def release(self, e):
        if self.start and not self.moved: self.app.restore()
        elif self.moved: cfg['ball'] = [self.x, self.y]; save_cfg()
        self.start = None


class Dial:
    """主页中间的圆盘：按住它说话。外圈 60 根刻度随音量伸缩、思考时转圈，中间是 6 根黑色渐变声波条。"""
    def __init__(self, n, cx, cy, rad, hold):
        self.n, self.cx, self.cy, self.rad, self.hold, self.cmd = n, cx, cy, rad, hold, None
        self.visible, self.down, self.photo = True, False, None
        n.btns.append(self)
        self.sm, self.lv, self.tk, self.t0 = [0.0] * 6, 0.0, [0.0] * 60, time.time()

    def contains(self, x, y): return (x - self.cx) ** 2 + (y - self.cy) ** 2 <= self.rad ** 2
    def set_down(self, v): self.down = v

    def make(self):
        r = self.rad; X0, Y0, N = math.floor((self.cx - r) * S), math.floor((self.cy - r) * S), px(2 * r) + 1
        up = self.n.img.crop((X0, Y0, X0 + N, Y0 + N)); dn = up.copy()
        inset(dn, self.cx - r - X0 / S, self.cy - r - Y0 / S, 2 * r, 2 * r, r, d=7)
        self.base, self.N = (up.convert('RGBA'), dn.convert('RGBA')), N
        self.photo = ImageTk.PhotoImage(self.base[0]); self.n.c.create_image(X0, Y0, anchor='nw', image=self.photo)
        self.ox, self.oy = (self.cx - r) * S - X0, (self.cy - r) * S - Y0


    def frame(self, state, level):
        if not self.photo: return
        t, ss = time.time() - self.t0, 2
        N = self.N * ss; u = S * ss; C = (self.rad * S + self.ox) * ss
        L = Image.new('RGBA', (N, N), (0, 0, 0, 0)); d = ImageDraw.Draw(L)
        self.lv += ((level if state == 'rec' else 0) - self.lv) * 0.35
        hot = RED if state == 'error' else ACCENT
        for i in range(60):
            if state == 'rec': g = self.lv * (0.55 + 0.45 * math.sin(t * 7 + i * 0.55))
            elif state in ('busy', 'loading'): g = max(0.0, 1 - ((t * (40 if state == 'busy' else 18) - i) % 60) / 14) * (1 if state == 'busy' else 0.6)
            else: g = 0.0
            self.tk[i] += (g - self.tk[i]) * 0.35; g = self.tk[i]
            major = i % 15 == 0; a = i / 60 * math.tau - math.pi / 2; ca, sa = math.cos(a), math.sin(a)
            r1 = (self.rad - 13) * u; r0 = (self.rad - (23 if major else 19) - (9 * g if state == 'rec' else 0)) * u
            col = mix(TICK_MAJ if major else TICK, hot, min(1.0, g * 1.3) if not (state == 'error' and major) else 1)
            d.line((C + ca * r0, C + sa * r0, C + ca * r1, C + sa * r1), fill=col, width=max(1, round((2.2 if major else 1.4) * u)))
        paint_bars(L, C, C, self.rad * 0.82 * u, state, self.lv, t, self.sm)
        out = self.base[self.down].copy(); out.alpha_composite(L.resize((self.N, self.N), Image.BOX)); self.photo.paste(out)


WORDS = {'loading': 'WAKING', 'ready': 'READY', 'rec': 'LISTENING', 'busy': 'THINKING', 'error': 'ERROR'}

class App:
    W, H = 400, 626

    def __init__(self, root):
        self.root = root; root.title(TITLE); root.configure(bg=BG)
        self.chrome = Chrome(root, taskbar=True, R=48)  # 卡片 r28 + 离边 20
        root.protocol('WM_DELETE_WINDOW', quit_app)
        self.state, self.sil_since, self.shown, self.mic = 'loading', time.time(), 0.0, 'ok'
        self.notice_until, self.ol_last, self.ol_starting, self.last = 0.0, None, 0.0, None
        self.settings = self.check_win = self.history = self.pop = None
        self.pop_closed = (None, 0.0)  # 小弹窗刚因为焦点跑掉被关：同一次点击别又打开
        self.cur, self.pick_job = None, None  # 当前这句 [原话, 输入的英文, 中文]；等着挑的版本

        n = self.n = Neu(root, self.W, self.H); n.c.pack()
        # 能点的都是凸起的，凹下去的只放内容。和设置 / 记录窗口一套网格：左右离边 20，标题行一样，第一张卡片顶边 y=92，
        # 卡片之间都隔 20，卡片圆角 28（窗口圆角 48 = 28 + 20）；「上一句」右边的复制图标和齿轮同样大、圆心在同一竖线（x=348）
        n.text(32, 26, 'QUILL', 18, INK, family=DISPLAY)  # 和 SETTINGS 一样的宽体大字
        n.text(33, 56, '英文语音输入', 11, SUB)
        n.button(274, 30, 30, 30, icon=I_BALL, size=9, d=3, color=ICON_C, cmd=self.to_ball)   # 缩成悬浮球
        n.button(312, 30, 30, 30, icon=I_MIN, size=8, d=3, color=ICON_C, cmd=self.minimize)
        n.button(350, 30, 30, 30, icon=I_CLOSE, size=8, d=3, color=ICON_C, cmd=quit_app)
        n.raised(20, 92, 360, 326, 28, d=7)                      # 状态卡片（凸起，和设置里的卡片一样）：大字 + 提示 + 刻度和声波条；按住中间那圈说话时那圈凹下去
        self.word = n.text(44, 108, '', 22, INK, family=DISPLAY)
        self.sub = n.text(45, 150, '', 11, SUB)
        self.dial = Dial(n, 200, 296, 104, (lambda: eng.start('mouse'), eng.stop))
        # 说哪种语言（出来的都是英文）：两个小胶囊和 READY 同一行、右边对齐卡片内边；选中的凹下去
        self.lang_sw = Switch(n, 276, 110, 40, 28, 4, (('en', 'EN'), ('zh', '中')), cfg['lang'], self.set_lang); n.btns.append(self.lang_sw)
        self.b_copy = n.button(20, 438, 360, 78, r=39, icon=I_COPY, size=16, d=5, color=ICON_C, align='left', cmd=self.copy_last)  # 上一句：整条点一下复制
        self.l_en = n.text(48, 456, '还没有记录', 12, TEXT)  # 不显示「上一句 · 时间」，英文 + 中文两行在条里上下居中
        self.l_zh = n.text(48, 480, '', 11, SUB)
        n.button(20, 536, 276, 64, r=32, icon=I_HIST, text='记录', size=12, color=ICON_C, d=5, cmd=self.open_history)
        n.button(316, 536, 64, 64, icon=I_SET, size=16, d=5, color=ICON_C, cmd=self.open_settings)
        n.commit()

        r = load_history(1)
        if r: self.set_last(*r[-1])
        self.set_state('loading')
        self.overlay = Overlay(self); self.ball = Ball(self)
        root.after(2500, self.prewarm)
        threading.Thread(target=self.ollama_loop, daemon=True).start()
        self.poll()

    # 子窗口
    def _open(self, attr, cls):
        """子窗口第一次打开时建好；关掉只是藏起来，再开直接显示（不用重画，不卡）。"""
        w = getattr(self, attr)
        if not (w and w.w.winfo_exists()): w = cls(self); setattr(self, attr, w)
        if getattr(w, 'dirty', False): w.dirty = False; w.build()
        if not w.w.winfo_viewable(): place_side(self, w.w, w.n.w); w.w.deiconify(); w.w.chrome.sync()
        w.w.lift()

    def prewarm(self):
        """启动后先把设置、记录窗口在后台建好（藏着），第一次点开也不卡。"""
        for attr, cls in (('settings', Settings), ('history', History)):
            if not self.alive(attr): setattr(self, attr, cls(self))

    def open_settings(self): self._open('settings', Settings)
    def open_checks(self): self._open('check_win', Checks)
    def open_history(self): self._open('history', History)
    def alive(self, attr):
        w = getattr(self, attr)
        return w if w and w.w.winfo_exists() else None

    def minimize(self): user32.ShowWindow(self.chrome.hwnd, 6)

    def to_ball(self):
        """主窗口藏起来，换成屏幕边上的悬浮球。"""
        self.root.withdraw(); self.ball.show()

    def restore(self):
        self.ball.hide(); self.root.deiconify(); self.root.lift(); self.root.focus_force(); self.chrome.sync()

    def refresh_settings(self):
        for a in ('settings', 'pop'):
            if self.alive(a): getattr(self, a).build()

    def copy_last(self):
        if not self.last: return
        set_clip(self.last); self.b_copy.set(icon=I_OK)
        self.root.after(1200, lambda: self.b_copy.set(icon=I_COPY))

    def set_last(self, raw, fixed, zh, when=''):
        self.last = fixed; c = self.n.c
        c.itemconfigure(self.l_en, text=fit(fixed, fnt(12), px(270))); c.coords(self.l_en, px(48), px(456 if zh else 467))  # 没有中文那行（出中文）：一行上下居中
        c.itemconfigure(self.l_zh, text=fit(zh, fnt(11), px(270)))

    def set_key(self, vk):
        cfg['hotkey'] = vk; save_cfg(); log('快捷键改为', key_name(vk)); self.refresh_sub()

    def set_lang(self, lang):
        """主页 EN / 中：切换说哪种语言（第一次切到中文时在后台载中文识别模型，十秒左右）。"""
        if cfg['lang'] == lang: return
        cfg['lang'] = lang; save_cfg(); log('说话语言：', lang)
        self.lang_sw.select(lang)
        if lang == 'zh' and not eng.zh_started: threading.Thread(target=eng.load_zh, daemon=True).start()
        def warm():  # 两种语言的提示词不一样，Ollama 只缓存一份：先用新提示词跑一次，切完说的第一句不慢
            if ollama_status() == 'ok':
                try: llm('hello', watch=False, lang=lang)
                except Exception: pass
        threading.Thread(target=warm, daemon=True).start()
        if self.state in ('loading', 'ready', 'error'): self.set_state(eng.idle_state())

    def sub_text(self):
        return {'loading': '第一次打开要十几秒', 'ready': f'按住「{key_name(cfg["hotkey"])}」或按住中间这一圈，说' + ('中文' if cfg['lang'] == 'zh' else '英文'),
                'rec': '说完松开', 'busy': '改语病 + 翻译，几秒钟', 'error': eng.error()}[self.state]

    def refresh_sub(self):
        if time.time() < self.notice_until: return
        self.n.c.itemconfigure(self.sub, text=fit(self.sub_text(), fnt(11), px(312), 2), fill=SUB)

    def show_notice(self, text, level):
        self.notice_until = time.time() + 15
        self.n.c.itemconfigure(self.sub, text=fit(text, fnt(11), px(312), 2), fill={'err': RED, 'ok': GREEN}.get(level, ORANGE))
        self.root.after(15100, self.refresh_sub)

    def set_state(self, s, toast=False):
        self.state = s
        self.n.c.itemconfigure(self.word, text=WORDS[s], fill=RED if s == 'error' else INK)
        self.refresh_sub()
        if s == 'rec': self.overlay.listen(toast)
        if s == 'busy': self.overlay.think('')
        if s == 'error': self.show_notice(eng.error(), 'err')

    def choose(self, i):
        """挑选模式：i = 0 自然 / 1 口语，None = 放弃。"""
        p, self.pick_job, eng.picking = self.pick_job, None, False
        if not p: return
        raw, opts, by_key = p
        if i is None: log('放弃了这句'); self.cur = None; return self.overlay.hide()
        text = opts[i]; log('选了', ('自然', '口语')[i], '：', text)
        if by_key: threading.Thread(target=paste, args=(text,), daemon=True).start()
        else: set_clip(text); self.show_notice('已复制到剪贴板，去要输入的地方按 Ctrl+V。', 'ok')
        self.cur[1] = text; self.overlay.result(raw, text, self.cur[2], by_key); self.finish()

    def finish(self):
        """英文和中文都有了：记下来、更新主页「上一句」。"""
        if not (self.cur and self.cur[1] is not None and self.cur[2] is not None): return
        raw, fixed, zh = self.cur; self.cur = None
        save_history(raw, fixed, zh); self.set_last(raw, fixed, zh)
        h = self.alive('history')
        if h:  # 藏着的记录窗口先记一下，打开时再重画
            if h.w.winfo_viewable(): h.build()
            else: h.dirty = True

    def ollama_loop(self):
        while True:
            post('ollama', ollama_status()); time.sleep(8)

    def poll(self):
        try:
            while not ui.empty(): self.handle(*ui.get())
        except Exception:
            log('界面出错', traceback.format_exc())
        p, eng.peak = eng.peak, 0.0
        v = min(1.0, (p / 0.4) ** 0.5); self.shown = max(v, self.shown * 0.82)
        if p > 0.0003: self.sil_since = time.time()
        mic = 'off' if eng.tried and eng.stream is None else 'silent' if time.time() - self.sil_since > 10 else 'ok'  # 还没开过麦克风时不算「没打开」
        if mic != self.mic:
            self.mic = mic
            if mic == 'silent': self.show_notice(MIC_SHORT, 'warn')
            if mic == 'off': self.show_notice('麦克风没打开：去设置换一个设备。', 'err')
        self.dial.frame(self.state, self.shown)
        for a in ('settings', 'pop'):
            if self.alive(a): getattr(self, a).tick(self.shown, mic)
        sync_chromes()
        self.overlay.frame(self.shown)
        if self.ball.visible:
            if user32.IsWindowVisible(self.chrome.hwnd): self.ball.hide()  # 别处把主窗口叫出来了（比如再双击图标）
            else: self.ball.frame(self.state, self.shown)
        self.root.after(33, self.poll)

    def handle(self, kind, *a):
        if kind == 'state': self.set_state(a[0], *a[1:])
        elif kind == 'notice':
            text, level, toast = a; self.show_notice(text, level)
            if toast: self.overlay.notice(text, level)
        elif kind == 'mic_bad': self.sil_since = 0
        elif kind == 'live': self.overlay.live(*a)
        elif kind == 'thinking': self.overlay.think(a[0])
        elif kind == 'fixed':
            raw, fixed, by_key = a; self.cur = [raw, fixed, None]; self.overlay.result(raw, fixed, None, by_key)
            if not by_key: self.show_notice('已复制到剪贴板，去要输入的地方按 Ctrl+V。', 'ok')
        elif kind == 'pick':
            raw, opts, by_key = a; self.cur, self.pick_job, eng.picking = [raw, None, None], a, True
            self.overlay.pick(raw, opts)
        elif kind == 'zh':
            raw, zh = a
            if self.cur and self.cur[0] == raw: self.cur[2] = zh; self.overlay.set_zh(zh); self.finish()
        elif kind == 'choose': self.choose(a[0])
        elif kind == 'ollama':
            st = a[0]
            if st != self.ol_last:
                log('改错服务：', st)
                if st == 'down' and time.time() - self.ol_starting > 30: self.show_notice('改错服务没开：去设置里点「启动」。', 'warn')
            changed, self.ol_last = st != self.ol_last, st
            if changed: self.refresh_settings()
        elif kind == 'key_recorded':
            if self.alive('settings'): self.settings.key_recorded(a[0])
        elif kind == 'check' and self.alive('check_win'): self.check_win.update(*a)
        elif kind == 'checks_done' and self.alive('check_win'): self.check_win.done(a[0])


class Settings:
    """设置窗口：麦克风 / 快捷键 / 输出方式 / 改错服务 四块卡片。
    only=('mic',) 或 ('ai',) 时是主页右上角 MIC / AI 小圆章点开的小弹窗：只放相关的那几块、没有卡片底，点别处就关。"""
    W, POP_W = 400, 340
    MODES = (('fast', '直接输入', '松开后直接输入改好的句子（最快）'),
             ('pick', '让我挑选', '松开后在屏幕下方给「自然」「口语」两个版本，点一下输入；点一下快捷键 = 选第 1 个，Esc 放弃'))
    ZH_OUTS = (('en', '出英文', ''), ('zh', '出中文', ''))  # 出中文：删掉口头禅 / 重复 / 改口，不翻译
    POPS = {'mic': ('mic',), 'ai': ('ol', 'mode')}

    def __init__(self, app, only=None, anchor=None):
        self.app, self.only, self.recording = app, only, False
        self.key_tip = ('按住快捷键说话，松开自动输入到光标处', SUB)
        self.Wd = self.POP_W if only else self.W
        self.w = sub_window(app, {'mic': '麦克风', 'ai': '改错服务'}.get(only, '设置'), self.Wd, anchor)
        self.n = Neu(self.w, self.Wd, 600); self.n.c.pack(); self.build()
        if only:
            self.w.bind('<Escape>', lambda e: self.close()); self.w.bind('<FocusOut>', lambda e: self.w.after(150, self.check_focus))
            self.w.after(60, self.w.focus_force)

    def close(self):
        if self.w.winfo_exists(): self.w.destroy()
        self.app.pop_closed = (self.only, time.time())

    def check_focus(self):
        """小弹窗：焦点跑到别的窗口就关掉。"""
        if self.w.winfo_exists() and not self.recording and not SHOWCASE and user32.GetForegroundWindow() != self.w.chrome.hwnd: self.close()

    # 每一块：先算高度（要先画卡片底再画内容），返回 (高度, 画法)
    def sec_mic(self, n, x, y, w, draw):
        devs = ['系统默认'] + eng.devices(); cur = cfg['device'] if cfg['device'] in devs else '系统默认'
        dev_y = y + 56; track_y = dev_y + len(devs) * 50 + 6; tip_y = track_y + 22; h = tip_y + 40 - y
        if not draw: return h
        self.label(n, x, y, w, h, '麦克风')
        for i, name in enumerate(devs):
            on = name == cur
            n.button(x + 20, dev_y + i * 50, w - 40, 40, r=20, text=name, icon=I_OK if on else '', size=12, on=on, align='left', cmd=lambda name=name: self.pick_dev(name))
        n.inset(x + 20, track_y, w - 40, 10, 5, d=2.5)
        self.bar_x, self.bar_w = x + 26, w - 52
        self.bar = n.c.create_line(px(self.bar_x), px(track_y + 5), px(self.bar_x), px(track_y + 5), width=px(4), fill=ACCENT, capstyle='round')
        self.tip = n.text(x + 20, tip_y, '', 11, SUB); self.tip_state = None
        return h

    def sec_key(self, n, x, y, w, draw):
        keys = list(KEYS.values()) + ([cfg['hotkey']] if cfg['hotkey'] not in KEYS.values() else [])
        cols, gap = 4, 10; cw = (w - 40 - gap * (cols - 1)) / cols  # 4 列网格：每个一样宽、间距一样
        chips = [(vk, x + 20 + (i % cols) * (cw + gap), (i // cols) * 46, cw) for i, vk in enumerate(keys + [None])]
        cy = chips[-1][2]
        chip_y = y + 56; ktip_y = chip_y + cy + 48; h = ktip_y + 36 - y
        if not draw: return h
        self.label(n, x, y, w, h, '快捷键')
        for vk, cx, dy, cw in chips:
            if vk is None:
                self.rec_btn = n.button(cx, chip_y + dy, cw, 34, text='请按键…' if self.recording else '录制', size=12,
                                        color=ORANGE if self.recording else TEXT, cmd=self.record)
            else:
                on = vk == cfg['hotkey']
                n.button(cx, chip_y + dy, cw, 34, text=key_name(vk), size=12, on=on, cmd=lambda vk=vk: self.set_hot(vk))
        self.ktip = n.text(x + 20, ktip_y, self.key_tip[0], 11, self.key_tip[1], width=w - 40, lines=2)
        return h

    def sec_mode(self, n, x, y, w, draw):
        h = 166
        if not draw: return h
        mode = cfg.get('mode', 'fast'); self.label(n, x, y, w, h, '输出方式')
        rows = ((y + 14, self.MODES, mode, self.set_mode), (y + 112, self.ZH_OUTS, cfg.get('zh_out', 'en'), self.set_zh_out))
        for by, opts, cur, cmd in rows:
            for i, (m, name, _) in enumerate(opts):
                on = m == cur
                n.button(x + w - 210 + i * 100, by, 90, 34, text=name, size=12, on=on, cmd=lambda m=m, cmd=cmd: cmd(m))
        n.text(x + 20, y + 62, dict((m, t) for m, _, t in self.MODES)[mode], 11, SUB, width=w - 40, lines=2)
        n.text(x + 20, y + 122, '说中文时', 12, TEXT)
        return h

    def sec_ol(self, n, x, y, w, draw):
        h = 122
        if not draw: return h
        app = self.app; st = app.ol_last
        if st == 'down' and time.time() - app.ol_starting < 30: st = 'starting'
        self.label(n, x, y, w, h, '改错服务')
        text, color = {'ok': ('正常', GREEN), 'no_model': (f'缺少 {LLM_MODEL}', RED), 'down': ('没有运行', ORANGE),
                       'starting': ('启动中…', ORANGE), None: ('检查中…', SUB)}[st]
        n.text(x + w - 20, y + 22, '● ' + text, 12, color, anchor='ne')
        on_gpu = llm_opts()[1]
        n.text(x + 20, y + 56, f'本机 Ollama · {LLM_MODEL} · ' + ('显卡' if on_gpu else 'CPU') +
               ('\n一句约 1~2 秒；显卡被占着变慢会自动换 CPU' if on_gpu else '\n一句约 5~8 秒' + ('（显卡慢，暂时换成 CPU）' if LLM_GPU_LAYERS else '')), 11, SUB)
        last = f'上一句改错用了 {llm_stat["secs"]:.1f} 秒（{"显卡" if llm_stat["gpu"] else "CPU"}）' if llm_stat else '还没有改过句子'
        n.text(x + 20, y + 94, last, 11, SUB)
        if st == 'down': n.button(x + w - 90, y + 50, 70, 34, text='启动', size=12, cmd=self.start_ol)
        return h

    @classmethod
    def full_height(cls):
        """完整设置窗口的高度（记录窗口也用它，两个窗口一样高）。各块不画只量高度时用不到 self。"""
        secs = (cls.sec_mic, cls.sec_key, cls.sec_mode, cls.sec_ol)
        return 92 + sum(f(None, None, 20, 0, 360, False) for f in secs) + 20 * (len(secs) - 1) + 26

    def label(self, n, x, y, w, h, name):
        if not self.only: n.raised(x, y, w, h, 28, d=7)
        n.text(x + 20, y + 24, name, 12, TEXT)

    def build(self):
        n, app = self.n, self.app
        secs = [getattr(self, 'sec_' + s) for s in (self.POPS[self.only] if self.only else ('mic', 'key', 'mode', 'ol'))]
        if self.only: x, w, y0, gap = 0, self.Wd, 6, 0
        else: x, w, y0, gap = 20, 360, 92, 20
        H = self.full_height() if not self.only else y0 + sum(s(n, x, 0, w, False) for s in secs) + 10
        n.reset(H); n.c.configure(height=px(H))
        if not self.only:
            n.text(32, 26, 'SETTINGS', 18, INK, family=DISPLAY); n.text(33, 56, '设置', 11, SUB)
            n.button(272, 28, 70, 34, r=17, icon=I_CHECK, text='自检', size=12, cmd=app.open_checks)
            close_btn(n, self.Wd, self.w.withdraw)
        y = y0
        for i, s in enumerate(secs):
            if i and self.only: n.img.paste('#D6D6D6', (px(20), px(y), px(w - 20), px(y) + max(1, px(1))))  # 小弹窗里两块之间的细线
            y += s(n, x, y, w, True) + gap
        n.commit()

    def tick(self, level, mic):
        if not hasattr(self, 'bar'): return
        c = self.n.c; _, y0, _, y1 = c.coords(self.bar)
        c.coords(self.bar, px(self.bar_x), y0, px(self.bar_x) + level * px(self.bar_w), y1)
        if mic != self.tip_state:
            self.tip_state = mic
            text, color = {'ok': ('说话时黑条会跳动', SUB), 'silent': ('10 秒没收到声音。' + MIC_HINT, ORANGE),
                           'off': ('麦克风打不开：换一个设备，或检查 Windows 设置 → 隐私和安全性 → 麦克风。', RED)}[mic]
            c.itemconfigure(self.tip, text=fit(text, fnt(11), px(self.bar_w + 12), 2), fill=color)

    def pick_dev(self, name):
        cfg['device'] = None if name == '系统默认' else name; save_cfg()
        self.app.sil_since = time.time(); eng.open(cfg['device']); self.app.refresh_settings()

    def set_hot(self, vk):
        self.app.set_key(vk); self.key_tip = ('按住快捷键说话，松开自动输入到光标处', SUB); self.build()

    def set_mode(self, m):
        cfg['mode'] = m; save_cfg(); log('输出方式改为', m); self.app.refresh_settings()

    def set_zh_out(self, o):
        cfg['zh_out'] = o; save_cfg(); log('说中文时输出', o); self.app.refresh_settings()

    def record(self):
        if self.recording: return
        self.recording = True; self.rec_btn.set(text='请按键…', color=ORANGE)
        self.n.c.itemconfigure(self.ktip, text='现在按一下你想用的键（10 秒内）', fill=ORANGE)
        def run():
            eng.key_paused = True; vk = eng.wait_any_key(10); time.sleep(0.3); eng.key_paused = False
            post('key_recorded', vk)
        threading.Thread(target=run, daemon=True).start()

    def key_recorded(self, vk):
        self.recording = False
        if vk is None: self.key_tip = ('没收到按键，再点一次「录制」。', ORANGE)
        else: self.app.set_key(vk); self.key_tip = (f'已改成「{key_name(vk)}」', GREEN)
        self.build()

    def start_ol(self):
        start_ollama(); self.app.ol_starting = time.time(); self.app.refresh_settings()


def close_btn(n, w, cmd):
    """子窗口右上角的关闭按钮。"""
    n.button(w - 50, 30, 30, 30, icon=I_CLOSE, size=8, d=3, color='#555555', cmd=cmd)


class History:
    """最近 20 条记录：标题行固定不动，下面的卡片可以滚；列表上下边缘渐隐（内容慢慢淡出，不是一刀切）；
    右边一根细滑块，点它或拖它能快速回到顶上。"""
    W, TOP, FADE = 400, 74, 18  # 标题区高度（不跟着滚）、上面渐隐带高度；第一条内容顶边 = 74 + 18 = 92，和设置窗口对齐
    FADE_B, SOLID = 56, 20      # 下面渐隐带：总高、最底下实心的一段（= 窗口边距）

    def __init__(self, app):
        self.app = app; self.w = sub_window(app, '记录', self.W); self.opened = set()  # 展开了原话的记录（时间, 原话）
        self.view = Settings.full_height()  # 和设置窗口一样高
        h = self.head = Neu(self.w, self.W, self.TOP); h.c.pack()
        h.text(32, 26, 'HISTORY', 18, INK, family=DISPLAY); self.count = h.text(33, 56, '', 11, SUB)
        close_btn(h, self.W, self.w.withdraw)
        h.button(236, 28, 104, 36, r=18, text='打开文件', size=12, cmd=lambda: os.startfile(HERE / 'history.txt') if (HERE / 'history.txt').exists() else None)
        h.commit()
        self.n = Neu(self.w, self.W, self.view - self.TOP, view=self.view - self.TOP); self.n.c.pack()
        self.w.bind('<MouseWheel>', lambda e: self.scroll('scroll', -int(e.delta / 120) * 3, 'units'))
        self.build()

    def scroll(self, *a):
        self.n.c.yview(*a); self.bar.update()

    def build(self, keep=False):
        n = self.n; recs = list(reversed(load_history(20)))
        fe, fz, fm = fnt(12), fnt(11), fnt(11)
        oy = n.c.canvasy(0) / S if keep else 0  # 展开 / 收起原话后还停在原来滚到的地方
        cards, y = [], self.FADE
        for raw, fixed, zh, when in recs:  # 卡片上面灰字：时间 · 原话（一行放不下时右边有展开钮）；卡片里只有改后的英文 + 中文，复制按钮 28 对着各自那段字上下居中
            en, cn = wrap(fixed, fe, px(280)), wrap(zh, fz, px(280))
            he, hz = len(en) * fe.metrics('linespace') / S, len(cn) * fz.metrics('linespace') / S
            meta = f'{when}  ·  说：{raw}'; key = (when, raw); full = wrap(meta, fm, px(280))
            lines = full if key in self.opened else [fit(meta, fm, px(280))]
            top = y + len(lines) * fm.metrics('linespace') / S + 10  # 灰字下面隔一点就是卡片
            ey = top + max(14, 24 - he / 2)                  # 字离卡片边 14，按钮离卡片边至少 10
            zy = max(ey + he + 8, ey + he / 2 + 34 - hz / 2)  # 两个按钮之间至少隔 6
            h = (max(zy + hz + 14, zy + hz / 2 + 24) if zh else max(ey + he + 14, ey + he / 2 + 24)) - top  # 出中文的没有中文那行
            cards.append((y, top, h, ey, he, zy, hz, lines, key if len(full) > 1 else None, fixed, zh, en, cn)); y = top + h + 20
        H = max(self.view - self.TOP, y - 20 + self.FADE_B)  # 拉到底时最后一张卡片露在下面的渐隐带上面
        n.reset(H); n.c.configure(scrollregion=(0, 0, px(self.W), px(H)), yscrollincrement=px(20))
        self.head.c.itemconfigure(self.count, text=f'最近 {len(recs)} 条 · 新的在上面')
        if not recs: n.text(200, 100, '还没有记录', 11, SUB, anchor='center')
        for y, top, h, ey, he, zy, hz, lines, key, fixed, zh, en, cn in cards:
            n.text(40, y, '\n'.join(lines), 11, SUB)
            if key:  # 原话一行放不下：小圆钮（和复制按钮同一竖列，对着第一行灰字居中），点一下展开全文，再点收起
                cy = y + fm.metrics('linespace') / S / 2
                n.button(336, cy - 10, 20, 20, icon=I_UP if key in self.opened else I_DOWN, size=7, d=2, color=ICON_C,
                         cmd=lambda key=key: self.toggle(key))
            n.raised(20, top, 360, h, 24, d=6)
            n.text(40, ey, '\n'.join(en), 12, TEXT); n.text(40, zy, '\n'.join(cn), 11, SUB)
            for t, cy in ((fixed, ey + he / 2), (zh, zy + hz / 2))[:2 if zh else 1]:
                b = n.button(332, cy - 14, 28, 28, icon=I_COPY, size=10, d=3, color=ICON_C)
                b.cmd = lambda t=t, b=b: (set_clip(t), b.set(icon=I_OK), b.n.c.after(1200, lambda: b.set(icon=I_COPY)))
        self.bar = ScrollBar(n, H, self.view - self.TOP)
        n.commit()
        for y, top in ((0, True), (self.view - self.TOP - self.FADE_B, False)):  # 上下两条渐隐带，钉在视口里
            self.bar.pin(n.c.create_image(0, 0, anchor='nw', image=self.fade(top)), y)
        n.c.tag_raise(self.bar.thumb); n.c.yview_moveto(oy / H); self.bar.update()

    def toggle(self, key):
        self.opened ^= {key}; self.build(keep=True)

    _fades = {}
    def fade(self, top):
        """底色从不透明慢慢变到全透明（缓动曲线），盖在列表边上：滚动的内容是逐渐淡出的。
        上面一条接着标题区；下面一条最底下 SOLID 是实心底色（和标题区一样，窗口圆角那段不露内容），往上慢慢变透明。"""
        if top not in self._fades:
            w = px(self.W)
            if top: t = np.linspace(0, 1, px(self.FADE))
            else: t = np.concatenate([np.linspace(1, 0, px(self.FADE_B - self.SOLID)), np.zeros(px(self.SOLID))])
            a = (255 * (1 - t) ** 1.8).astype(np.uint8)
            im = Image.new('RGBA', (w, len(t)), BG); im.putalpha(Image.fromarray(np.repeat(a[:, None], w, axis=1)))
            History._fades[top] = ImageTk.PhotoImage(im)
        return self._fades[top]


class ScrollBar:
    """记录窗口右边的细滑块（没有槽；画在可滚的画布里，每次滚动后挪回原位）。点那一竖条 = 跳到那个位置（点最上面 = 回到顶上），拖滑块 = 跟着滚。
    pin() 钉住的东西（上下渐隐带）也跟着挪回原位。"""
    X, PAD = 390, 10  # 竖线中心 x；上下留的空

    def __init__(self, n, total, view):
        self.n, self.total, self.view, self.cmd = n, total, view, None
        self.visible, self.hold = total > view, (self.press, self.release)
        self.pins = []
        self.thumb = n.c.create_line(0, 0, 0, 0, width=px(4), fill='#9A9A9E', capstyle='round', tags='txt')
        self.drag = None; n.btns.append(self)

    def pin(self, item, y): self.pins.append((item, y))

    def make(self): pass  # Neu.commit 会调，这里没有要预先画的
    def set_down(self, v): self.n.c.itemconfigure(self.thumb, fill=INK if v else '#9A9A9E')

    def geom(self):
        """滑块在视口里的上下端（逻辑像素）和轨道长度。"""
        top, bot = self.n.c.yview(); L = self.view - 2 * self.PAD
        tl = max(30.0, L * (bot - top)); t0 = self.PAD + (L - tl) * (top / max(1e-6, 1 - (bot - top)))
        return t0, t0 + tl, L, tl

    def update(self):
        oy = self.n.c.canvasy(0)
        for it, y in self.pins: self.n.c.coords(it, 0, oy + px(y))
        self.n.c.itemconfigure(self.thumb, state='normal' if self.visible else 'hidden')
        if not self.visible: return
        t0, t1, _, _ = self.geom(); x = px(self.X)
        self.n.c.coords(self.thumb, x, oy + px(t0), x, oy + px(t1))

    def contains(self, x, y):
        vy = y - self.n.c.canvasy(0) / S  # 换成视口里的坐标
        return self.visible and abs(x - self.X) <= 9 and self.PAD - 4 <= vy <= self.view - self.PAD + 4

    def press(self):
        e = self.n.c.winfo_pointery() - self.n.c.winfo_rooty()  # 视口里的物理像素
        t0, t1, L, tl = self.geom(); vy = e / S
        if not t0 <= vy <= t1: self.jump(vy - tl / 2)  # 点在轨道上：滑块中心跳到那里
        t0, _, _, _ = self.geom(); self.drag = vy - t0

    def motion(self, e):
        if self.drag is not None: self.jump(e.y / S - self.drag)

    def jump(self, thumb_top):
        _, _, L, tl = self.geom(); top, bot = self.n.c.yview()
        f = min(1.0, max(0.0, (thumb_top - self.PAD) / max(1e-6, L - tl)))
        self.n.c.yview_moveto(f * (1 - (bot - top))); self.update()

    def release(self): self.drag = None


class Checks:
    """自检窗口：一项项打勾。"""
    ICON = {'wait': ('', SUB), 'run': ('…', INK), 'ok': ('✓', GREEN), 'fail': ('✕', RED), 'warn': ('!', ORANGE)}
    W, ROW = 400, 84

    def __init__(self, app):
        self.app, self.running, self.extra = app, False, None
        self.w = sub_window(app, '自检', self.W)
        top = 92; by = top + 5 * self.ROW + 16 + 22
        n = self.n = Neu(self.w, self.W, by + 52 + 26); n.c.pack()
        close_btn(n, self.W, self.w.withdraw)
        n.text(32, 26, 'CHECK', 18, INK, family=DISPLAY); n.text(33, 56, '按提示说话、按键，一项项检查哪里有问题', 11, SUB)
        n.raised(20, top, 360, 5 * self.ROW + 16, 28, d=7)
        self.rows = []
        for i, name in enumerate(CHECKS):
            y = top + 8 + i * self.ROW
            n.inset(36, y + 10, 32, 32, 16, d=3)
            ic = n.text(52, y + 26, '', 13, SUB, anchor='center')
            n.text(84, y + 10, name, 12, TEXT)
            self.rows.append((ic, n.text(84, y + 34, '', 11, SUB), y))
        self.btn = n.button(20, by, 360, 52, r=26, text='开始自检', size=12, cmd=self.start)
        n.commit()
        if SELFTEST: self.start()

    def start(self):
        if self.running: return
        self.running = True; self.btn.set(text='检查中…', color=SUB)
        for ic, d, _ in self.rows: self.n.c.itemconfigure(ic, text=''); self.n.c.itemconfigure(d, text='')
        if self.extra: self.n.remove(self.extra); self.extra = None
        def report(i, st, text, extra=None):
            post('check', i, st, text, extra)
            if st != 'run': log(f'[自检] {CHECKS[i]}：', {'ok': '通过', 'fail': '失败', 'warn': '注意'}[st], text)
        threading.Thread(target=lambda: post('checks_done', run_checks(report, not SELFTEST)), daemon=True).start()

    def update(self, i, st, text, extra):
        ic, d, y = self.rows[i]; sym, color = self.ICON[st]
        self.n.c.itemconfigure(ic, text=sym, fill=color)
        self.n.c.itemconfigure(d, text=fit(text, fnt(11), px(276), 3), fill=RED if st == 'fail' else SUB)
        if extra is not None:
            def use(): self.app.set_key(extra); self.n.remove(self.extra); self.extra = None
            self.extra = self.n.button(254, y + 4, 110, 30, r=15, text=f'改用「{key_name(extra)}」', size=12, cmd=use)

    def done(self, ok):
        self.running = False; self.btn.set(text='全部通过 · 再测一次' if ok else '再测一次', color=INK)
        log('自检结果：', '全部通过' if ok else '有失败项')
        if SELFTEST: self.w.after(1500, quit_app)


def mix(a, b, t):
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(ca, cb))

def load_history(n):
    try: blocks = (HERE / 'history.txt').read_text(encoding='utf-8').strip().split('\n\n')
    except Exception: return []
    out = []
    for b in blocks[-n:]:
        d = {l[:2]: l[2:] for l in b.splitlines()[1:]}
        if '说：' in d: out.append((d.get('说：', ''), d.get('改：', ''), d.get('译：', ''), b.splitlines()[0][5:]))
    return out

def load_fonts():
    """fonts 里的字体只给本进程加载（不装进系统）。"""
    for f in sorted(p for p in (HERE / 'fonts').iterdir() if p.suffix.lower() in ('.ttf', '.ttc', '.otf')):
        try: ctypes.windll.gdi32.AddFontResourceExW(str(f), 0x10, 0)
        except Exception: pass

def setup_fonts(root):
    global DISPLAY, ICON, FONT, FONT_BOLD
    fams = set(tkfont.families(root))
    if 'Michroma' in fams: DISPLAY = 'Michroma'
    if 'Segoe Fluent Icons' in fams: ICON = 'Segoe Fluent Icons'
    if 'Noto Sans SC Medium' in fams: FONT, FONT_BOLD = 'Noto Sans SC Medium', ('Noto Sans SC' if 'Noto Sans SC' in fams else None)  # 正文用 Medium：Regular 在 Windows 小字号下太细

def quit_app():
    try:
        if eng.stream: eng.stream.close()
    except Exception: pass
    os._exit(0)


if __name__ == '__main__':
    w = next((a for a in sys.argv if a.startswith('--asr-worker=')), None)
    if w: asr_worker(w.split('=')[1]); sys.exit(0)
    kernel32.CreateMutexW(None, False, 'QuillVoiceInput')
    if kernel32.GetLastError() == 183:  # 已经开着：把原来的窗口叫到前面
        h = user32.FindWindowW(None, TITLE)
        if h: user32.ShowWindow(h, 9); user32.SetForegroundWindow(h)
        sys.exit(0)
    (HERE / 'run.log').write_text('', encoding='utf-8')
    log('启动' + ('（自检模式）' if SELFTEST else ''))
    try: ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('local.voice-input')  # 任务栏显示自己的图标
    except Exception: pass
    try: ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 高分屏下不模糊；坐标按 S 缩放
    except Exception: pass
    load_fonts()
    root = tk.Tk(); root.withdraw()
    S = root.winfo_fpixels('1i') / 96
    setup_fonts(root)
    root.report_callback_exception = lambda *e: (log('界面出错', ''.join(traceback.format_exception(*e))),
                                                 notify('出了点问题，已记到 run.log。', 'err'))
    threading.excepthook = lambda a: log('后台出错', ''.join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    app = App(root)
    wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)  # 工作区（不含任务栏），物理像素
    root.geometry(f'+{wa.right - px(App.W) - px(60)}+{wa.top + px(12)}')
    root.deiconify(); root.update()  # 先把窗口画出来：冷启动时开麦克风要等声卡驱动一两秒，放在前面窗口会白一阵
    threading.Thread(target=lambda: (eng.open(cfg['device']), eng.load()), daemon=True).start()  # 开麦克风、载识别模型都在后台
    if cfg['lang'] == 'zh': threading.Thread(target=eng.load_zh, daemon=True).start()
    threading.Thread(target=fix_worker, daemon=True).start()
    threading.Thread(target=eng.hotkey_loop, daemon=True).start()
    if SELFTEST: root.after(500, app.open_checks)
    for a in sys.argv:  # 截图测试用：--show=settings,history,checks 启动时直接打开这些子窗口
        if a.startswith('--show='):
            for w in a[7:].split(','):  # pop:mic / pop:ai 打开小弹窗
                name, _, arg = w.partition(':'); root.after(800, lambda f=getattr(app, 'open_' + name), arg=arg: f(arg) if arg else f())
    root.mainloop()
