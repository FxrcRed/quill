"""本地英文语音输入（图形界面版）：按住快捷键或界面按钮说英文 → Whisper 识别 → Qwen 改语病 + 中文翻译 → 粘贴到光标处。
双击桌面「Quill」图标启动（pythonw，无黑框）。--selftest：无人值守自检，结果写 run.log 后退出。"""
import math, ctypes, ctypes.wintypes, difflib, json, os, queue, re, shutil, socket, subprocess, sys, threading, time, traceback, urllib.error, urllib.request, wave
from datetime import datetime, timedelta
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont

import numpy as np
import sounddevice as sd
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageGrab, ImageTk

HERE = Path(__file__).parent
TITLE = 'Quill'
LLM_URL = 'http://127.0.0.1:11434'
LLM_MODEL = 'qwen3:4b'  # 设置「改错模型」里选（cfg['llm']）：4B 约 2.9GB 显存、快约 35%；8B 约 4.5GB，很乱的话（重说、改口）改得更准
LLM_MODELS = (('qwen3:4b', '标准 4B'), ('qwen3:8b', '高质量 8B'))
WHISPER_TIMEOUT = 8   # 松开后 Whisper 整段识别最多等几秒，超时 / 出错就用说话时最后一次预览的结果
PREVIEW_MIN = 1.5     # 说话不到这么久不出预览：Whisper 对刚开口的半个字常编出「请订阅点赞」「优优独播剧场」这类训练数据里的片尾字幕
PHANTOM = re.compile(r'订阅|点赞|打赏|明镜|独播剧场|字幕志愿|字幕由|Amara|谢谢观看|感谢观看|请不吝|Subtitles by|Thanks for watching', re.I)
PREVIEW_EVERY = 1.0  # 说话时每隔几秒用 Whisper 把目前为止的整段认一遍，给浮窗看
LLM_GPU_LAYERS = -1   # （启动时按有没有 NVIDIA 显卡重新定，见 nvidia_gpu）-1 = 放显卡（Ollama 自动，一句约 1.5 秒）；0 = 全用 CPU（约 5~8 秒）。显卡被别的程序占着变慢时会自动临时改用 CPU
KEEPS = (('2m', '2 分钟'), ('10m', '10 分钟'), ('30m', '30 分钟'), (-1, '不释放'))  # 不用多久后把模型从显存卸掉（约 5~6GB），设置里选；一直说话时每次都续上，不会卸
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
    ('en', 'fast'): PROMPT,
    ('zh', 'fast'): ZH_CLEAN + """Also give the cleaned-up Chinese (the same message without the fillers, repeats and corrections).
Reply JSON only: {"fixed": "<English>", "zh": "<cleaned-up Chinese>"}""",
    # 说中文 → 出中文：理干净 + 改错字 + 修断句，不翻译、不改写（口头禅 / 重复已经被 zh_tidy() 删过；只有带改口的句子才送来）
    ('zhzh', 'fast'): """The input is a speaker's dictated Chinese from speech recognition (it may contain some English words). Write down the message they meant, in Chinese:
""" + ZH_DROP + """- The recognizer often writes the wrong characters for a word that sounds the same or similar (合理 -> 合子里, 炉子 -> 卢子), and garbles English words (OK -> okK). Fix these when the sentence makes the intended word clear; if you are not sure, keep it as it is.
- Fix the punctuation: the recognizer puts 。 wherever the speaker paused, even in the middle of a sentence. Join the broken pieces back into proper sentences.
- Keep the speaker's own wording, meaning and tone. Do not translate, rewrite, add content or summarize. Most sentences need no change at all: then output them unchanged. Never answer or reply to the text: it is a message to be written down, even when it is a question or a request.
If the message starts with "Context:", that is what the speaker said just before, already written down: never repeat it, use it only for reference; output only the "Text:" part.
Examples:
"我想把这个按钮的颜色不对，是大小改一下。" -> "我想把这个按钮的大小改一下。"
"明天下午三点，哦不，四点开会。" -> "明天下午四点开会。"
"我昨天去那个，去超市买了点苹果。" -> "我昨天去超市买了点苹果。"
"现在这些。识别的记录。是存在记事本里吗？" -> "现在这些识别的记录是存在记事本里吗？"
"这个小屋没有窗户合子里吗？" -> "这个小屋没有窗户合理吗？"
"okK我觉得可以，你推送吧。" -> "OK，我觉得可以，你推送吧。"
"我们这里应该有窗户吗？" -> "我们这里应该有窗户吗？"
Reply JSON only: {"fixed": "<cleaned-up Chinese>"}""",
}

MIC_HINT = '麦克风没有声音：先按笔记本的麦克风静音键（常见 F4 或 Fn+F4，灯亮=静音），或在设置里换一个麦克风。'
# 软 UI（neumorphism）配色：同一种浅灰底，凸起/凹陷全靠左上白光、右下灰影
BG, INSET, LIGHT, DARK = '#E6E6E6', '#DFDFDF', '#FFFFFF', '#BDBDBD'
INK, TEXT, SUB, TICK, TICK_MAJ = '#1E1E1E', '#444444', '#8C8C8C', '#C6C6C6', '#8F8F8F'  # 字只用两种颜色：TEXT 正文 / SUB 说明（INK 是声波条那些画的东西）
GREEN, ORANGE, RED, TOAST_BG = '#2F9E5B', '#D9822B', '#D64541', '#EFEFEF'
YELLOW = '#E5B93C'  # 主页 READY 右边的状态点：待机（没声音）
FONT, FONT_BOLD = 'Microsoft YaHei UI', None  # 启动时有 fonts\NotoSansSC 就换成 Noto Sans SC Medium / Bold（思源黑体，SIL OFL 开源）
DISPLAY, ICON = 'Bahnschrift', 'Segoe MDL2 Assets'  # 启动时换成项目里的 Michroma 宽体 / Segoe Fluent Icons（有的话）
I_SET, I_COPY, I_HIST, I_OK, I_CHECK = '', '', '', '', ''
I_MIN, I_CLOSE, I_MIC, I_BALL = '\uE921', '\uE8BB', '\uE720', '\uE73F'
I_DOWN, I_UP = '\uE70D', '\uE70E'  # 记录里展开 / 收起原话
I_EDIT, I_DEL = '\uE70F', '\uE74D'  # 常用词窗口：改 / 删
I_PIN = '\uE718'  # 浮窗固定后右边的图钉
I_OPEN, I_SETUP = '', ''  # 记录 / 常用词标题行「用记事本打开」；设置标题行「准备情况」（扳手）
HIST_DY = 4
NAV_DY = -4  # 导航按钮里宽体字比按钮中线低 4 像素（实测：按钮身 656~700，字心 682.5），整排上提
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

cfg = {'hotkey': 0xA5, 'device': None, 'lang': 'en', 'zh_out': 'en', 'keep': '2m', 'hist': True, 'hist_days': 0}  # hist：存不存记录；hist_days：只留最近几天（0 = 永久）；keep：不用多久释放显存（见 KEEPS）；lang：en 说英文 / zh 说中文；zh_out：说中文时出英文 / 出中文
try: cfg.update(json.loads((HERE / 'settings.json').read_text(encoding='utf-8')))
except Exception: pass
if cfg.get('llm') in dict(LLM_MODELS): LLM_MODEL = cfg['llm']

def save_cfg():
    try: (HERE / 'settings.json').write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    except Exception: log('保存设置失败', traceback.format_exc())

# ---- 显卡 / Whisper 用的 cuBLAS ----
_gpu_name = []
def nvidia_gpu():
    """NVIDIA 显卡型号，没有返回 ''（nvidia-smi 查一次，缓存）。QUILL_FORCE_CPU=1：假装没有显卡，测降级用。"""
    if not _gpu_name:
        name = ''
        if not os.environ.get('QUILL_FORCE_CPU'):
            exe = shutil.which('nvidia-smi') or r'C:\Windows\System32\nvidia-smi.exe'
            try:
                r = subprocess.run([exe, '--query-gpu=name', '--format=csv,noheader'], capture_output=True, text=True, timeout=8, creationflags=0x08000000)
                if r.returncode == 0 and r.stdout.strip(): name = r.stdout.strip().splitlines()[0].strip()
            except Exception: pass
        _gpu_name.append(name)
    return _gpu_name[0]

def cublas_dirs():
    """Whisper 要的 cuBLAS 12 在哪：先找 .venv 里 pip 装的（nvidia-cublas-cu12，setup.ps1 会装），找不到再借 Ollama 自带的（老用户）。
    QUILL_HIDE_CUBLAS=1：假装都没有，测缺库提示用。"""
    if os.environ.get('QUILL_HIDE_CUBLAS'): return []
    ds = [Path(p) / 'nvidia' / 'cublas' / 'bin' for p in sys.path]
    ds.append(Path(os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\lib\ollama\cuda_v12')))
    return [d for d in ds if (d / 'cublas64_12.dll').exists()]

LLM_GPU_LAYERS = -1 if nvidia_gpu() else 0  # 没有 NVIDIA 显卡：改错直接用 CPU，别让 watch_gpu 去测速

WORDS_FILE = HERE / 'words.txt'  # 常用词表（人名、项目名、中英混说的词），一行一个，# 开头是注释；识别听错时模型按它的写法改
WORDS_HEAD = ('# 常用词：一行一个（人名、项目名、中英混说的词）。存盘后下一句就生效。\n'
              '# 只写词：大小写统一成这里的写法（github → GitHub），也提示改错模型。\n'
              '# 「正确写法: 听错的写法, 另一种听错的写法」：识别出听错的写法时看上下文决定换不换（cloud 也可能真是云；每次多约 0.2 秒），例如  Claude: cloud\n')
def term_rx(a):
    """词表里一个写法的正则：中间有空格的，识别结果里那处是空格 / 句号 / 逗号都算；英文前后不能挨着别的字母（que 不匹配 queil）。"""
    rx = r'[\s。，,]*'.join(re.escape(p) for p in a.split())
    if re.match(r'[A-Za-z0-9]', a): rx = r'(?<![A-Za-z0-9])' + rx
    if re.search(r'[A-Za-z0-9]$', a): rx += r'(?![A-Za-z0-9])'
    return re.compile(rx, re.I)

_glossary = [None, [], '', []]
def glossary():
    """(词表, 提示词里加的那段, [(正则, 正确写法, 听错的写法 / None)])。按文件修改时间缓存，用户改了存盘下一句就生效。
    听错的写法不靠改错模型（8B 给了词表也不敢把 cloud 改成 Claude，2026-10-21 试过三种放法），每条单独问一次 is_term()；
    正确写法本身（听错的写法 None）只统一大小写，直接换。"""
    try: mt = WORDS_FILE.stat().st_mtime
    except OSError: return [], '', []
    if mt != _glossary[0]:
        terms, subs, asks = [], [], []
        try: lines = WORDS_FILE.read_text(encoding='utf-8-sig').splitlines()
        except Exception: lines = []
        for ln in lines:
            if not (p := parse_word(ln)): continue
            term, alts = p
            terms.append(term)
            for a in [term] + alts:  # 以前要加 ? 才看上下文，现在都看，? 照样认（parse_word 去掉了）
                if a is not term and a.lower() == term.lower(): continue
                if a is term:
                    if term != term.lower(): subs.append((term_rx(a), term, None))  # 全小写的（skill）不统一，不然句首的 Skill 也变小写
                else: asks.append((term_rx(a), term, a))
        asks.sort(key=lambda x: -len(x[2]))  # 长的先：cloud code 先判，不然 cloud 先被挖掉
        hint = ('\nThe speaker often talks about these names and terms: ' + ', '.join(terms) + '. Keep them written exactly like this.') if terms else ''
        _glossary[:] = [mt, terms, hint, asks + subs]
    return _glossary[1], _glossary[2], _glossary[3]

# 把听错的词挖空让模型填：直接问「这个 cloud 是不是 Claude」它会认死字面全答否（term_test.py：直接问 9/18，挖空 + 拿不准选名字 26/28）
TERM_PROMPT = ('One word in this sentence was replaced by ___. It is either "{term}" (a name: a tool, app, product or person the speaker uses, talks to or works with every day) '
               'or the ordinary word "{heard}". The speaker talks about {term} far more often than about {heard}. '
               'Choose "{heard}" only when the sentence clearly uses its ordinary meaning; if unsure, choose "{term}". '
               'Answer only JSON {{"word": "{term}"}} or {{"word": "{heard}"}}.')
_term_memo = {}
def is_term(t, rx, term, heard):
    """句子里的 heard 是不是 term（cloud 可能真是云）。约 0.2 秒；同一句只问一次；出错算是 term（和以前直接替换一样）。"""
    q = rx.sub('___', t)
    if (q, term) not in _term_memo:
        # format 只给 'json' 时模型常自己编一大段 JSON（复述题目 + 分析，几百 token、4~13 秒，读不到 word 才碰巧按 term 算）；限死成两个选项
        body = {'model': LLM_MODEL, 'stream': False, 'think': False, 'keep_alive': cfg.get('keep', '2m'),
                'format': {'type': 'object', 'properties': {'word': {'enum': [term, heard]}}, 'required': ['word']},
                'options': {**llm_opts()[0], 'temperature': 0, 'num_predict': 20},  # num_ctx 要和改错一样：不一样 Ollama 每次都重新载入模型（踩过，一句慢到 10 秒）
                'messages': [{'role': 'system', 'content': TERM_PROMPT.format(term=term, heard=heard)}, {'role': 'user', 'content': q}]}
        try:
            with urllib.request.urlopen(urllib.request.Request(LLM_URL + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=10) as r:
                w = str(json.loads(json.loads(r.read())['message']['content']).get('word', ''))
        except Exception as e: log('判断易混词失败：', repr(e)); return True
        if len(_term_memo) > 200: _term_memo.clear()
        _term_memo[q, term] = w.lower() != heard.lower()
        log(f'易混词 {heard} → {term if _term_memo[q, term] else heard}')
    return _term_memo[q, term]

def apply_glossary(t):
    for rx, term, heard in glossary()[2]:
        if heard is None or (rx.search(t) and is_term(t, rx, term, heard)): t = rx.sub(lambda m: term, t)  # 用函数：写法里有 \ 时 sub 会当转义
    return pinyin_fix(t, [w for w in glossary()[0] if re.fullmatch(r'[一-鿿]{2,}', w)])

_PY_INI = ('zh', 'ch', 'sh', 'b', 'p', 'm', 'f', 'd', 't', 'n', 'l', 'g', 'k', 'h', 'j', 'q', 'x', 'r', 'z', 'c', 's', 'y', 'w')
def _py_split(s):
    """'zhong1' → ('z', 'on', '1')：平翘舌、前后鼻音（zh/z、ing/in、eng/en、ang/an）当一样。"""
    tone, s = s[-1], s[:-1]
    ini = next((i for i in _PY_INI if s.startswith(i)), '')
    fin = s[len(ini):]
    ini = {'zh': 'z', 'ch': 'c', 'sh': 's'}.get(ini, ini)
    return ini, re.sub(r'(?<=[aei])ng$', 'n', fin), tone

_py_cache = {}
def pinyin_fix(t, words):
    """中文词表的同音字：识别成「临终小屋」「老云山」时按拼音换回「林中小屋」「云杉」（Whisper 的提示只是倾向，同音时照样选错）。
    两字词要拼音连声调一模一样；三字以上允许一个字声调不同。思路取自 CapsWriter-Offline 的音素热词。"""
    if not words or not re.search(r'[一-鿿]', t): return t
    from pypinyin import lazy_pinyin, Style  # 用到才载（约 0.2 秒）
    py = lambda s: _py_cache.get(s) or _py_cache.setdefault(s, [_py_split(p) for p in lazy_pinyin(s, style=Style.TONE3, neutral_tone_with_five=True)])
    for w in sorted(words, key=len, reverse=True):  # 长的先
        wp, n = py(w), len(w)
        def sub(m):
            s, sp, out, i = m[0], py(m[0]), [], 0
            while i < len(s):
                if i + n <= len(s) and s[i:i + n] != w:
                    cost = 0
                    for a, b in zip(sp[i:i + n], wp):
                        if a != b: cost += 1 if n > 2 and a[:2] == b[:2] else 9  # 只对上声母的不算（「在对话框」会换成「子对话框」）
                    if cost <= (1 if n > 2 else 0): out.append(w); i += n; continue
                out.append(s[i]); i += 1
            return ''.join(out)
        t = re.sub(r'[一-鿿]+', sub, t)
    return t

def parse_word(ln):
    """词表一行 → (正确写法, [听错的写法])；注释 / 空行 / 没有正确写法的返回 None。中文冒号、逗号、顿号也认，结尾 ? 是旧写法。"""
    ln = ln.strip()
    if not ln or ln.startswith('#'): return None
    term, _, alts = ln.replace('：', ':').partition(':')
    return (term.strip(), [a for a in (a.strip().rstrip('?？').strip() for a in re.split(r'[,，、]', alts)) if a]) if term.strip() else None

def glossary_known():
    """词表里已有的写法（正确的、听错的），小写。"""
    return {t.lower() for t in glossary()[0]} | {h.lower() for _, _, h in glossary()[2] if h}


# ---- 找可能听错的词：闲着时每天扫一次新记录，说过 ≥2 次的放到记录窗口的「常用词」卡片里，用户点「加入 / 忽略」（不自动加：模型猜的正确写法会错，如奥拉玛 → 阿拉马）----
SCAN_FILE = HERE / 'words_scan.json'  # 扫到哪条记录了、找到的候选、忽略过的（不进 git）
scan_lock = threading.Lock()  # 后台扫词和界面点「忽略」都读改写 SCAN_FILE
SCAN_CHARS, SCAN_MIN = 1200, 2  # 每批约 1200 字：太多会超出 num_ctx，模型停不下来；说过几次才提示
SCAN_PROMPT = ('These are raw speech-recognition transcripts from one speaker, a programmer who mixes Chinese and English and often talks about '
               'AI tools, apps, coding and their own projects. Find words that are probably MISRECOGNIZED: names of tools / apps / products / '
               'people / technical terms written wrong (e.g. "cloud" for Claude, "archK factsects" for Artifacts), or Chinese homophone errors. '
               '"heard" must be copied exactly from the text. Skip ordinary words that are already correct. Only list ones you are fairly sure about. '
               'Answer only JSON {"items": [{"heard": "...", "correct": "..."}]}.')

def scan_load():
    try: return {'done': '', 'at': 0, 'cands': {}, 'ignored': [], **json.loads(SCAN_FILE.read_text(encoding='utf-8'))}
    except Exception: return {'done': '', 'at': 0, 'cands': {}, 'ignored': []}

def scan_save(st):
    try: SCAN_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception: log('存候选词失败', traceback.format_exc())

def scan_batches(items, key=lambda x: x):
    """按字数分批（每批约 SCAN_CHARS 字）。"""
    out, cur, n = [], [], 0
    for it in items:
        k = min(len(key(it)), SCAN_CHARS)
        if cur and n + k > SCAN_CHARS: out.append(cur); cur, n = [], 0
        cur.append(it); n += k
    return out + ([cur] if cur else [])

def find_candidates(part):
    """一批识别原话 → 模型觉得听错的 [(听到的写法, 正确写法, 例句)]。听到的写法要真在原话里（模型会编），已在词表里的不要。"""
    body = {'model': LLM_MODEL, 'stream': False, 'think': False, 'format': 'json', 'keep_alive': cfg.get('keep', '2m'),
            'options': {**llm_opts()[0], 'temperature': 0, 'num_predict': 400},  # num_ctx 和改错一样，不然要重新载入模型
            'messages': [{'role': 'system', 'content': SCAN_PROMPT}, {'role': 'user', 'content': '\n'.join(t[:SCAN_CHARS] for t in part)}]}
    with urllib.request.urlopen(urllib.request.Request(LLM_URL + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=60) as r:
        reply = json.loads(r.read())['message']['content']
    known, out = glossary_known(), []
    for h, c in re.findall(r'"heard"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"correct"\s*:\s*"((?:[^"\\]|\\.)*)"', reply):  # 不整个 json.loads：输出到 num_predict 被截断时前面写完的几条照样能用
        h, c = json.loads(f'"{h}"').strip(), json.loads(f'"{c}"').strip()
        if not h or not c or h.lower() == c.lower() or h.lower() in known: continue
        if ' ' in c and c == c.lower(): continue  # 全小写的短语（round → run out）是语法，改错会管，不是听错的名字
        if len(h) > 12 or re.search(r'[。，？！,.?!]', h): continue  # 模型有时把整句中文的改写当成听错
        src = next((s for s in part if term_rx(h).search(s)), None)
        if src: out.append((h, c, src))
    return out

def said_lines(blocks):
    return [(b[:16], l[2:].strip()) for b in blocks for l in b.split('\n') if l.startswith('说：') and len(l) > 4]

_sugs = [None, []]
def word_sugs():
    """记录窗口常用词卡片里要提示的候选词 [(听到, 正确写法, 说过几次, 例句)]：说过 ≥ SCAN_MIN 次、没忽略过、还没进词表的，次数多的在前。"""
    if not cfg.get('hist', True): return []
    try: key = (SCAN_FILE.stat().st_mtime, WORDS_FILE.stat().st_mtime if WORDS_FILE.exists() else 0)
    except OSError: return []
    if _sugs[0] != key:
        st, known = scan_load(), glossary_known()
        said, out = [t for _, t in said_lines(hist_blocks())], []
        for k, (h, c, src) in st['cands'].items():
            if k in st['ignored'] or h.lower() in known: continue
            n = sum(len(term_rx(h).findall(s)) for s in said)
            if n >= SCAN_MIN: out.append((h, c, n, src))
        _sugs[:] = [key, sorted(out, key=lambda x: -x[2])]
    return _sugs[1]

ui = queue.Queue()  # 后台线程 → 界面线程
def post(*m): ui.put(m)
def notify(text, level='warn', toast=False): post('notice', text, level, toast)


# ---- 改错（Ollama）----
FIELDS = ('fixed', 'zh')
gpu = {'off_until': 0.0, 'slow': 0}  # 显卡变慢（被别的程序占着）时暂时改用 CPU
last_llm = [0.0]
llm_stat = {}  # 上一次改错：用了几秒、是不是显卡
lat = {}  # 松开后各阶段的时间点（分段计时日志用，见 Engine._work）
def mark(k): lat[k] = time.time()

def llm_opts():
    on_gpu = LLM_GPU_LAYERS != 0 and time.time() > gpu['off_until']
    layers = {} if on_gpu and LLM_GPU_LAYERS < 0 else {'num_gpu': LLM_GPU_LAYERS if on_gpu else 0}
    return {'temperature': 0.2, 'num_ctx': 2048, **layers}, on_gpu

def with_glossary(p):
    """常用词表插在例子前面（放最后模型常不理）。"""
    g = glossary()[1]; i = p.find('\nExamples:')
    return p if not g else p + g if i < 0 else p[:i] + g + p[i:]

def llm(text, on_field=None, timeout=60, watch=True, lang='en'):  # watch=False：预热那种很短的请求不算进显卡测速
    """流式调 Ollama：JSON 里每个字段一写完就回调 on_field(名字, 内容)，英文不用等中文翻译生成完。返回所有字段。"""
    keys, (opts, on_gpu) = ('fixed',) if lang == 'zhzh' else FIELDS, llm_opts()
    body = {'model': LLM_MODEL, 'stream': True, 'think': False, 'keep_alive': cfg.get('keep', '2m'), 'format': 'json', 'options': opts,
            'messages': [{'role': 'system', 'content': with_glossary(PROMPTS[lang, 'fast'])}, {'role': 'user', 'content': text}]}
    req = urllib.request.Request(LLM_URL + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    buf, got, stats, t0 = '', {}, {}, time.time()
    def give(k, v):
        got[k] = v
        if k == 'fixed': mark('llm_en')
        if on_field: on_field(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for line in r:
            if not line.strip(): continue
            m = json.loads(line); buf += m.get('message', {}).get('content', '')
            if buf and 'llm_first' not in lat: mark('llm_first')
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

def llm_hello():
    """预热：用下一句真会用的那套提示词（语言 + 输出，和 Job 一样算）跑一次；Ollama 只缓存一份提示词，用错了第一句照样慢。"""
    if ollama_status() != 'ok': return
    sl = 'zhzh' if cfg['lang'] == 'zh' and cfg.get('zh_out', 'en') == 'zh' else cfg['lang']
    try: llm('hello', watch=False, lang=sl); return True
    except Exception as e: log('预热改错失败：', e)

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

def keep_secs():
    k = cfg.get('keep', '2m')
    try: return float('inf') if k == -1 else float(str(k).rstrip('mh')) * (3600 if str(k).endswith('h') else 60)
    except ValueError: return 120.0

def llm_warm():
    """只把模型载入内存（不生成），说话的同时就能载好。"""
    try:
        body = {'model': LLM_MODEL, 'keep_alive': cfg.get('keep', '2m'), 'options': llm_opts()[0]}
        urllib.request.urlopen(urllib.request.Request(LLM_URL + '/api/generate', json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=60).read()
        last_llm[0] = time.time()
    except Exception as e: log('预热改错失败：', repr(e))

def apply_keep():
    """释放显存的时间改了：模型正在显存里就用新时间重新计时（没载着就不去载）。"""
    try:
        with urllib.request.urlopen(LLM_URL + '/api/ps', timeout=2) as r:
            if not any(m['name'].split(':latest')[0] == LLM_MODEL for m in json.loads(r.read()).get('models', [])): return
        body = {'model': LLM_MODEL, 'keep_alive': cfg.get('keep', '2m')}
        urllib.request.urlopen(urllib.request.Request(LLM_URL + '/api/generate', json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=30).read()
    except Exception as e: log('更新释放显存时间失败：', repr(e))

def set_llm(name):
    """换改错模型：先把旧的从显存卸掉（两个一起载着 8GB 显存放不下），再把新的载上。"""
    global LLM_MODEL
    old, LLM_MODEL = LLM_MODEL, name; cfg['llm'] = name; save_cfg(); log('改错模型：', name)
    def go():
        try: urllib.request.urlopen(urllib.request.Request(LLM_URL + '/api/generate', json.dumps({'model': old, 'keep_alive': 0}).encode(), {'Content-Type': 'application/json'}), timeout=30).read()
        except Exception as e: log('卸载旧改错模型失败：', repr(e))
        llm_warm()
    threading.Thread(target=go, daemon=True).start()

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

ollama_proc = [None]  # Quill 自己拉起的 Ollama 进程；退出时只关它，不动用户本来开着的
ollama_owned = [False]  # 这台机器上 Ollama 只给 Quill 用：启动时已经在跑的也算 Quill 的，退出一起关
ollama_retry = [0.0]  # 自动启动失败后，这个时间之前不再自动试（没装 Ollama 的电脑别每 28 秒拉一次、弹一次提示）
def start_ollama(quiet=False):
    """拉起 ollama serve，成功启动进程返回 True。quiet：自动重试时不再弹提示（第一次失败已经提示过）。"""
    exe = shutil.which('ollama') or os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\ollama.exe')
    try: ollama_proc[0] = subprocess.Popen([exe, 'serve'], creationflags=0x08000000, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return True
    except Exception:
        log('启动 Ollama 失败', traceback.format_exc())
        if not quiet: notify('找不到改错服务 Ollama，可能没装好。', 'err')
        return False

def stop_ollama():
    """退出时关掉 Ollama。只关 Quill 拉起的那个；启动时就已经在跑的（ollama_owned）也关——这机器上只有 Quill 用它。"""
    p = ollama_proc[0]
    try:
        if p is not None and p.poll() is None:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(p.pid)], creationflags=0x08000000, capture_output=True, timeout=10)
        elif ollama_owned[0]:
            subprocess.run(['taskkill', '/F', '/T', '/IM', 'ollama.exe'], creationflags=0x08000000, capture_output=True, timeout=10)
        log('已关闭改错服务')
    except Exception: log('关闭 Ollama 失败', traceback.format_exc())

def ensure_ollama(wait=20):
    """改错服务没开就自己拉起（用户不该管这个）。返回等待后的最新状态。失败后 10 分钟内不再自动试，只第一次弹提示。"""
    st = ollama_status()
    if st != 'down': ollama_owned[0] = True; ollama_retry[0] = 0.0; return st
    if time.time() < ollama_retry[0]: return st
    log('改错服务没开，自动启动')
    if start_ollama(quiet=ollama_retry[0] > 0):
        end = time.time() + wait
        while time.time() < end:
            time.sleep(1); st = ollama_status()
            if st != 'down': ollama_owned[0] = True; ollama_retry[0] = 0.0; log('改错服务已自动启动'); return st
    log('自动启动改错服务没成功，10 分钟后再试'); ollama_retry[0] = time.time() + 600; return st


# ---- 录音 / 识别 ----
def gpu_note():
    """识别慢的时候记一笔：显存、显卡占用率、占 CPU 最多的程序（查是谁把显卡 / 整机占住了）。"""
    try:
        g = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.total,utilization.gpu', '--format=csv,noheader'], capture_output=True, text=True, timeout=3, creationflags=0x08000000).stdout.strip()
        p = subprocess.run(['powershell', '-NoProfile', '-Command', "(Get-Process | Sort-Object CPU -Descending | Select-Object -First 4 | ForEach-Object { $_.ProcessName }) -join ','"], capture_output=True, text=True, timeout=5, creationflags=0x08000000).stdout.strip()
        return f'| 显卡 {g} | CPU 累计最多 {p}'
    except Exception as e: return f'| 查显卡失败 {e!r}'

def has_speech(a): return len(a) > RATE * 0.3 and float(np.abs(a).max()) > 0.01

CJK = re.compile(r'[一-鿿]')

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

def zh_needs_llm(t):
    """出中文这段要不要送模型：只送带改口的。每句都送改错字反而更差（cer_eval：CER 2.37% → 6.19%，会删原话、改出错字）。"""
    return bool(ZH_FIX.search(t) or ZH_RESTART.search(t))

def zh_punct(t):
    """Whisper 出中文时标点是英文半角（逗号、问号），换成全角；中文字之间的空格去掉。数字里的小数点不动。"""
    t = re.sub(r'(?<=[一-鿿])\s*([,?!:;])\s*', lambda m: {',': '，', '?': '？', '!': '！', ':': '：', ';': '；'}[m.group(1)], t)
    return re.sub(r'(?<=[一-鿿，。？！：；])\s+(?=[一-鿿])', '', t).strip()


class Seg:
    """一句话：识别好的原文 + 改错结果（后台改，每个字段一出来就能用）。"""
    def __init__(self, raw, lang='en'):
        self.raw, self.lang, self.out, self.cv = raw, lang, {}, threading.Condition()
        self.text = raw  # 送去改的文字，prep() 在后台换词 / 理干净
        if lang == 'zhzh': self.out['zh'] = ''  # 说中文 → 出中文：没有翻译那行
        fixq.put(self)

    def prep(self):
        """后台（fix_worker）里做：先按常用词表换掉听错的词，出中文再用规则删口头禅 / 重复。返回还要不要送模型改。
        不放在 __init__：易混词要问一次模型（is_term），Ollama 正忙 / 在载入时要排队。"""
        self.text = apply_glossary(self.raw)
        if self.lang == 'zhzh':
            self.text = zh_tidy(self.text)
            if not zh_needs_llm(self.text): self.put('fixed', self.text); return False  # 没有改口、不用改错字：不等模型
        return True

    def put(self, k, v):
        with self.cv: self.out[k] = v; self.cv.notify_all()

    def get(self, k, timeout=120):
        with self.cv: self.cv.wait_for(lambda: k in self.out, timeout)
        return self.out.get(k, '' if k == 'zh' else self.text)

fixq = queue.Queue()
def fix_worker():
    while True:
        s = fixq.get()
        try:
            if s.prep(): llm(s.text, s.put, lang=s.lang)
        except Exception as e:
            log('改错失败：', repr(e)); err = '（' + llm_error(e) + '）'
            if s.lang == 'zhzh': notify(llm_error(e), 'warn')  # 没有翻译那行可以放提示
            for k in FIELDS:
                if k not in s.out: s.put(k, err if k == 'zh' else s.text)


REC_DIR, REC_KEEP = HERE / 'recordings', 1000
def save_rec(audio):
    """settings.json 里 "save_audio": true 时把每句录音存成 wav（拿来对比识别模型；不进 git，只留最近 REC_KEEP 句）。"""
    try:
        REC_DIR.mkdir(exist_ok=True); name = f'{datetime.now():%Y%m%d-%H%M%S}'
        with wave.open(str(REC_DIR / f'{name}.wav'), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
            w.writeframes((np.clip(audio.reshape(-1), -1, 1) * 32767).astype(np.int16).tobytes())
        for old in sorted(REC_DIR.glob('*.wav'))[:-REC_KEEP]: old.unlink()
        return name
    except Exception: log('存录音失败', traceback.format_exc())
REC_OF = {}  # 识别原文 → 录音名，写记录时带上（harvest.py 靠它配录音）

class Job:
    """一次录音。说话时每隔约 1 秒用 Whisper 把目前为止的整段认一遍给浮窗看（heard）；松开后整段再认一次，结果当一句去改。"""
    def __init__(self, by_key, lang='en', out='en'):
        self.by_key, self.lang, self.heard = by_key, lang, ''
        self.seg_lang = 'zhzh' if lang == 'zh' and out == 'zh' else lang  # 改错用哪套提示词

    def finish(self, a):
        """松开后整段给 Whisper（最多等 WHISPER_TIMEOUT 秒），出错 / 超时就用最后一次预览的字。没声音不送（Whisper 对着静音会编一句）。"""
        text = (eng.whisper_start(a, self.lang)() if has_speech(a) else '') or self.heard
        mark('wh')
        return Seg(text, self.seg_lang) if text else None


WH_ERRORS = {'nogpu': '没有 NVIDIA 显卡：Quill 用 Whisper 识别，要 NVIDIA 显卡。', 'nolib': '缺显卡运行库（cuBLAS）：设置 → 准备情况里点「修复」。',
             'nomodel': '没有识别模型（models\\whisper）：设置 → 准备情况里点「下载」。', 'error': '识别模型加载失败，详情见 run.log。'}

class Engine:
    def __init__(self):
        self.wh, self.load_error, self.loaded = None, '', threading.Event()  # Whisper 识别子进程，载不了就是 None
        self.wh_state = 'loading'  # loading / ok / nogpu 没有 NVIDIA 显卡 / nolib 缺 cuBLAS / nomodel 没下 Whisper 模型 / error
        self.stream, self.rec, self.src, self.chunks, self.probe, self.peak = None, False, None, [], None, 0.0
        self.key_paused, self.busy, self.job, self.tried = False, False, None, False  # tried：已经试过打开麦克风

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
        """Whisper large-v3-turbo（显卡）：说话时的预览和松开后的结果都是它认的。"""
        t = time.time()
        try:
            if not nvidia_gpu(): self.wh_state = 'nogpu'
            elif not (HERE / 'models' / 'whisper' / 'model.bin').exists(): self.wh_state = 'nomodel'
            else:
                try: self.wh = AsrProc(); self.wh_state = 'ok'; log(f'识别模型已加载 {time.time() - t:.1f}s')
                except Exception as e: self.wh_state = {'NOGPU': 'nogpu', 'NOLIB': 'nolib'}.get(str(e), 'error')
            if not self.wh: self.load_error = WH_ERRORS[self.wh_state]; log('识别模型没加载：', self.wh_state)
        finally:
            self.loaded.set()
        post('state', self.idle_state()); post('mode')
        if self.wh and llm_hello(): log('改错模型已预热', '（显卡）' if llm_opts()[1] else '（CPU）')

    def idle_state(self): return 'ready' if self.wh else 'error' if self.loaded.is_set() else 'loading'

    def recognize(self, a, lang='en'):
        """lang：en 说英文 / zh 说中文（Whisper language=zh，标点换全角）。"""
        if not self.wh: return ''
        t = self.wh.recognize(a, zh=lang == 'zh')
        return zh_punct(t) if lang == 'zh' else t

    def whisper_start(self, a, lang='en'):
        """在后台开跑，返回 wait()：最多等 WHISPER_TIMEOUT 秒；出错 / 超时返回 ''。"""
        got = []; t = time.time()
        def run():
            try: got.append(self.recognize(a, lang))
            except Exception as e: log('Whisper 出错：', repr(e))
        th = threading.Thread(target=run, daemon=True); th.start()
        def wait():
            th.join(max(0.0, WHISPER_TIMEOUT - (time.time() - t)))
            if not got: log(f'Whisper 没出结果（{time.time() - t:.1f}s），用预览的', gpu_note()); return ''
            dt = time.time() - t; log(f'Whisper {dt:.1f}s', gpu_note() if dt > 3 else ''); return got[0]
        return wait

    def audio(self):
        ch = list(self.chunks)
        return np.concatenate(ch)[:, 0] if ch else np.zeros(0, np.float32)

    def start(self, src):
        if self.rec or self.busy: return
        self.chunks.clear(); self.src, self.rec = src, True
        self.job = job = Job(src == 'key', cfg['lang'], cfg.get('zh_out', 'en'))
        log('开始录音', src); post('state', 'rec', src == 'key')
        threading.Thread(target=self.live, args=(job,), daemon=True).start()
        if time.time() - last_llm[0] > min(1200, keep_secs() - 10): threading.Thread(target=llm_warm, daemon=True).start()  # 闲到快被卸掉（或已卸掉）：边说边把模型载回来；释放显存设 2 分钟时原来要闲 20 分钟才预热，2~20 分钟之间松开后要现载模型（多 2~5 秒）

    def live(self, job):
        """说话时的预览：每隔 PREVIEW_EVERY 秒把目前为止的整段给 Whisper 认一遍（显卡上一次零点几秒）。"""
        t = time.time()
        while True:
            time.sleep(max(0.05, t + PREVIEW_EVERY - time.time()))
            if not self.rec or self.job is not job: return
            t = time.time(); a = self.audio()
            if not self.wh or not has_speech(a) or len(a) < RATE * PREVIEW_MIN: continue
            try: text = self.recognize(a, job.lang)
            except Exception: log('预览识别出错', traceback.format_exc()); return
            if PHANTOM.search(text) or not re.search(r'\w', text): continue
            if self.rec and self.job is job: job.heard = text; post('live', text, '')

    def stop(self):
        if not self.rec: return
        self.rec = False; self.busy = True; lat.clear(); mark('rel')
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
        rec = save_rec(audio) if cfg.get('save_audio') else None
        post('state', 'busy', job.by_key)
        self.loaded.wait()
        if not self.wh: return notify(self.load_error, 'err', True)
        t = time.time(); seg = job.finish(audio)
        if not seg: return notify('没听清，再说一次（靠近麦克风、说慢一点）。', 'warn', True)
        raw = seg.raw; post('thinking', raw)
        if rec: REC_OF[raw] = rec
        fixed = seg.get('fixed')
        log('识别：', *((raw, '| 改后：', fixed) if cfg.get('hist', True) else ('（记录已关，不记内容）',)), f'| 松开后 {time.time() - t:.1f}s')
        if job.by_key: paste(fixed)
        else: root.after(0, lambda: set_clip(fixed))
        if not job.by_key: mark('paste')
        r0 = lat.get('rel', t)  # 分段计时（松开后第几秒）：Whisper / 改错首字 / 英文写完 / 粘贴
        log('计时 ' + ' '.join(f'{n}={lat[k] - r0:.2f}' for n, k in (('Whisper', 'wh'), ('首字', 'llm_first'), ('英文', 'llm_en'), ('粘贴', 'paste')) if k in lat))
        post('fixed', raw, fixed, job.by_key)
        self.busy = False; post('state', 'ready')  # 中文翻译还在后台出，不耽误说下一句
        post('zh', raw, seg.get('zh'))

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
        """每 20ms 查一次快捷键是否按住（GetAsyncKeyState），不用低级键盘钩子，钩子被系统摘掉也不受影响。"""
        down = False
        while True:
            time.sleep(0.02)
            if self.key_paused: down = False; continue
            vk = cfg['hotkey']; d = key_down(vk)
            if d and not down and vk in (0xA4, 0xA5, 0x5B, 0x5C): tap(0xE8)  # 垫一个空键：单按 Alt 松开会激活菜单栏、单按 Win 会弹开始菜单
            if d and not down:
                self.start('key')
            elif not d and down and self.src == 'key':
                self.stop()
            down = d

# ---- 识别子进程 ----
# CUDA / ctranslate2 初始化慢、占 GIL，放在界面进程里刚打开的窗口就点不动 → 识别放在单独的子进程里
# （app.pyw --asr-worker），这边只用管道送音频、收文字。父 → 子：4 字节样本数 + float32；子 → 父：4 字节长度 + UTF-8（启动时先回 OK 或出错信息）。
def asr_worker():
    """Whisper large-v3-turbo（faster-whisper，显卡，约 1GB 显存），带上常用词当提示。"""
    inp, out = sys.stdin.buffer, sys.stdout.buffer; sys.stdout = None  # log() 别往管道里打印
    def send(s): b = s.encode('utf-8'); out.write(len(b).to_bytes(4, 'little') + b); out.flush()
    try:
        if not nvidia_gpu(): return send('NOGPU')
        ds = cublas_dirs()  # 要放进 PATH（只 add_dll_directory 不够）
        if ds: os.environ['PATH'] = str(ds[0]) + os.pathsep + os.environ['PATH']
        from faster_whisper import WhisperModel
        m = WhisperModel(str(HERE / 'models' / 'whisper'), device='cuda', compute_type='int8_float16')
        list(m.transcribe(np.random.default_rng(0).normal(0, 0.05, RATE * 2).astype(np.float32), language='en')[0])  # 先真跑一次（transcribe 返回生成器，不取结果就什么都没算）：缺 cuBLAS 在这里就报出来，第一句也不用等编译
        for lg, bm in (('en', 1), ('zh', 5)):  # 再各用 20 秒的静音跑一次英文 / 中文的解码设置：第一句真话不用再等 CUDA 初始化（结果不要）
            try: list(m.transcribe(np.zeros(RATE * 20, np.float32), language=lg, beam_size=bm, condition_on_previous_text=False)[0])
            except Exception: pass
        def rec(a, zh=False):
            terms = [t for t in glossary()[0] if zh or t.isascii()]  # 中文提示带中文词，英文提示只带英文词
            if zh: prompt, kw = '以下是普通话的句子，使用简体中文。' + ('常用词：' + '、'.join(terms) + '。' if terms else ''), dict(language='zh', beam_size=5)
            else: prompt, kw = ('Glossary: ' + ', '.join(terms) + '.') if terms else None, dict(language='en', beam_size=1)
            segs, _ = m.transcribe(a, condition_on_previous_text=False, initial_prompt=prompt, **kw)
            return ('' if zh else ' ').join(s.text.strip() for s in segs)
    except Exception as e:
        log('识别子进程加载失败', traceback.format_exc())
        return send('NOLIB' if 'cublas' in str(e).lower() else 'models\\whisper 里的文件可能缺了')
    send('OK')
    while len(h := inp.read(4)) == 4:  # 主程序退出、管道关掉就结束
        n = int.from_bytes(h, 'little'); zh = bool(n >> 31); n &= 0x7FFFFFFF  # 长度最高位 = 这段是中文
        a = np.frombuffer(inp.read(n * 4), np.float32)
        try: send(rec(a, zh).strip() if len(a) else '')
        except Exception: log('识别出错', traceback.format_exc()); send('')

class AsrProc:
    """识别子进程的代理（几个线程都会调，排队）。"""
    def __init__(self):
        self.lock = threading.Lock()
        self.spawn()

    def spawn(self):
        exe = Path(sys.executable).with_name('pythonw.exe')
        with open(HERE / 'run.log', 'a', encoding='utf-8') as err:  # 子进程没接住的报错也记进 run.log
            self.p = subprocess.Popen([str(exe), str(HERE / 'app.pyw'), '--asr-worker'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=err, cwd=HERE, creationflags=0x08000000)  # CREATE_NO_WINDOW
        if (msg := self._recv()) != 'OK': raise RuntimeError(msg)

    def _recv(self):
        h = self.p.stdout.read(4)
        if len(h) < 4: raise RuntimeError('识别子进程退出了')
        return self.p.stdout.read(int.from_bytes(h, 'little')).decode('utf-8')

    def recognize(self, a, zh=False):
        a = np.ascontiguousarray(a, np.float32); msg = (len(a) | zh << 31).to_bytes(4, 'little') + a.tobytes()  # 最高位 = 说中文（见 asr_worker）
        with self.lock:
            try:
                self.p.stdin.write(msg); self.p.stdin.flush(); return self._recv()
            except (OSError, RuntimeError):  # 子进程没了（被杀 / 崩了）：重开一个再试一次，不然之后每句都识别不了
                log('识别子进程断了，重开'); self.p.kill(); self.spawn()
                self.p.stdin.write(msg); self.p.stdin.flush(); return self._recv()

eng = Engine()

def mode_summary():
    """当前是哪种模式，对速度 / 质量有什么影响（自检和准备情况用）：返回 (ok|warn, 一句人话)。"""
    g, st = nvidia_gpu(), eng.wh_state
    ai = 'AI 改错用显卡' if g and LLM_GPU_LAYERS else 'AI 改错用 CPU，每句约 5~8 秒' if not g else 'AI 改错暂时用 CPU'
    if st == 'ok': return 'ok', f'Whisper 识别（{g}），{ai}'
    if st == 'loading': return 'ok', '识别模型（Whisper）载入中…'
    return 'warn', eng.load_error

HIST = HERE / 'history.txt'
HIST_DAYS = ((7, '7 天'), (30, '30 天'), (0, '永久'))

def save_history(raw, fixed, zh):
    if not cfg.get('hist', True): return  # 用户关了记录：什么都不写
    with open(HIST, 'a', encoding='utf-8') as f:
        rec = REC_OF.pop(raw, '')
        f.write(f'{datetime.now():%Y-%m-%d %H:%M}\n说：{raw}\n改：{fixed}\n译：{zh}\n' + (f'录：{rec}\n' if rec else '') + '\n')


def set_clip(text):
    root.clipboard_clear(); root.clipboard_append(text); root.update()

_last_paste = {'hwnd': None, 'text': '', 't': 0.0}

def paste(text):
    """借剪贴板粘贴到光标处，结束后恢复原剪贴板文字。
    英文接着上一句说（同一个窗口、2 分钟内、上一句末尾不是空白、两头都是英文）时，前面自动补一个空格。"""
    hwnd, last = user32.GetForegroundWindow(), _last_paste
    prev = last['text']
    if (hwnd == last['hwnd'] and time.time() - last['t'] < 120 and prev and text
            and prev[-1].isascii() and not prev[-1].isspace() and text[0].isascii() and not text[0].isspace()):
        text = ' ' + text
    last.update(hwnd=hwnd, text=text, t=time.time())
    ev, box = threading.Event(), {}
    def put():
        try: box['old'] = root.clipboard_get()
        except tk.TclError: box['old'] = None
        set_clip(text); ev.set()
    root.after(0, put); ev.wait(2); time.sleep(0.05)
    for vk, fl in ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)): user32.keybd_event(vk, 0, fl, 0)
    mark('paste')
    def restore():  # 等目标程序读完剪贴板再还原；放后台，不拖着浮窗出结果 / 回到 ready
        time.sleep(0.5)
        if box.get('old') is not None: root.after(0, lambda: set_clip(box['old']))
    threading.Thread(target=restore, daemon=True).start()


# ---- 自检 ----
CHECKS = ['识别模型', '麦克风', '快捷键', '语音识别', '改错服务']

def run_checks(report, interactive):
    """report(序号, 'run'|'ok'|'fail'|'warn', 说明, 附加按钮)；返回是否全部通过。"""
    ok = True
    report(0, 'run', '加载中…'); eng.loaded.wait(180)
    if eng.wh:
        lv, msg = mode_summary(); report(0, lv, '已加载 · ' + msg)
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
        t = time.time(); raw = eng.recognize(a)
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
        self.bg = self.c.create_image(0, 0, anchor='nw', image=self.photo); self.c.tag_lower(self.bg)
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


class CardBtn:
    """整张卡片当一个按钮（主页常用词卡片）：按下时把画布底图换成卡片凹下去的那张，里面的按钮在那张图里照样凸着。
    不用 Btn：Btn 按下是贴一块图盖住，会把卡片里画在底图上的按钮也盖掉。放在 btns 最前面，里面的按钮先被点到。"""
    hold, visible = None, True
    def __init__(self, n, box, down, cmd): self.n, self.box, self.cmd, self.ph = n, box, cmd, (n.photo, ImageTk.PhotoImage(down))
    def make(self): pass
    def contains(self, x, y): bx, by, w, h = self.box; return bx <= x <= bx + w and by <= y <= by + h
    def set_down(self, v): self.n.c.itemconfigure(self.n.bg, image=self.ph[v])


class Btn:
    """软 UI 按钮。kind：raised 凸起 / inset 凹槽（底栏「设置」）/ ring 描边小圆章。
    on=True 是选中（画成凹下去）。live=True 的按钮整张是贴图，可以隐藏、换颜色。"""
    def __init__(self, n, x, y, w, h, r=None, cmd=None, text='', icon='', size=13, d=None, color=TEXT, kind='raised',
                 on=False, live=False, hold=None, weight='normal', family=None, align='center'):
        self.n, self.box, self.r = n, (x, y, w, h), min(w, h) / 2 if r is None else r
        self.cmd, self.hold, self.text, self.icon, self.size, self.color = cmd, hold, text, icon, size, color
        self.d = ELEV if d is None else d  # 默认和主页一样高
        self.kind, self.on, self.live, self.weight, self.family, self.align = kind, on, live or n.ready, weight, family, align
        self.visible, self.down, self.tile, self.lab = True, False, None, []
        if not self.live: self.draw(n.img, x, y, False)

    def draw(self, img, x, y, pressed):
        w, h = self.box[2:]
        if self.kind == 'ring':
            ring(img, x + w / 2, y + h / 2, w / 2 - 1, self.color, fill='#D8D8D8' if pressed else None)
        elif self.kind == 'inset' or self.on or pressed:
            inset(img, x, y, w, h, self.r, self.d * INSET_K)  # 凹下去的深度 = 凸起的 INSET_K 倍（同样的深度，凹的比凸的看着更重）
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
            if v == self.value or v == self.down:  # 和按钮按下去同一个逻辑（Btn.draw）：先画凸起的边，再在里面压出凹面，所以外沿还留着一圈凸边
                raised(im, lx, ly, w, h, h / 2, ELEV); inset(im, lx, ly, w, h, h / 2, ELEV * INSET_K)
            else: raised(im, lx, ly, w, h, h / 2, ELEV)
            self.lab.append(c.create_text(px(x + w / 2), px(y + h / 2), text=t, font=fnt(11), fill=ICON_C, tags='txt'))
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
            bg = Image.new('RGBA', size, BG); bg.putalpha(_shape(size, box, R, 2))
            img.alpha_composite(bg)  # 不能用 paste(mask)：它把底色和阴影层的深色 RGB 直接按 mask 混，圆角边缘会出一圈深灰（阴影全透明处也会）
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

def sub_window(app, title, w):
    """设置 / 自检 / 记录 / 常用词：无边框圆角窗口，先藏着，由 App._open 摆好位置再显示。"""
    t = tk.Toplevel(app.root); t.withdraw(); t.title(title); t.configure(bg=BG)
    Chrome(t, owner=app.chrome.hwnd, R=48); t.chrome.side = True  # 子窗口卡片 r28 离边 20
    return t

def place_side(app, t, w):
    """新开的子窗口放在最左边，和开着的子窗口、主窗口排成一排（从左到右按现在的位置，间隔 20，顶边对齐主窗口）。
    左边放不下就整排挪到屏幕正中；还放不下就缩小间隔。四个窗口（主页 / 设置 / 记录 / 常用词）都开着也不重叠。"""
    side = sorted((c.win for c in CHROMES if getattr(c, 'side', False) and c.win is not t and c.win.winfo_exists() and c.win.winfo_viewable()),
                  key=lambda v: v.winfo_x())
    row = [(t, px(w))] + [(v, v.winfo_width()) for v in side] + [(app.root, app.root.winfo_width())]
    wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
    total, gap, k = sum(ww for _, ww in row), px(20), len(row) - 1
    right = app.root.winfo_x() + app.root.winfo_width()
    if right - total - gap * k < wa.left:
        gap = max(0, min(gap, (wa.right - wa.left - total) // max(1, k)))
        right = (wa.left + wa.right + total + gap * k) // 2
    x, y, moves = right, app.root.winfo_y(), []
    for v, ww in reversed(row):
        x -= ww
        if v is t: t.geometry(f'+{x}+{y}')
        elif v.winfo_x() != x: moves.append((v, x, v.winfo_y()))
        x -= gap
    move_together(moves)

def move_together(moves):
    """几个窗口连阴影一次挪好（DeferWindowPos，系统一起合成）。一个个 geometry() 会闪：Tk 等空闲才真挪，
    阴影紧跟着 sync 时窗口还在原地，下一帧才追上，几个窗口先后跳一下。"""
    if not moves: return
    d = user32.BeginDeferWindowPos(2 * len(moves))
    for v, x, y in moves:
        c = v.chrome; M = px(c.M)
        d = user32.DeferWindowPos(d, c.hwnd, None, x, y, 0, 0, 0x15)  # NOSIZE | NOZORDER | NOACTIVATE
        if c.shown: d = user32.DeferWindowPos(d, c.shwnd, None, x - M, y - M, 0, 0, 0x15)
    user32.EndDeferWindowPos(d)
    for v, x, y in moves:
        v.geometry(f'+{x}+{y}')  # Tk 记的位置也改过来（已经在那儿了，不会再挪），不然它以后重排时会挪回去
        if v.chrome.geo: v.chrome.geo = (x, y) + v.chrome.geo[2:]

BAR_BASE = (0.34, 0.68, 1.0, 0.55, 0.84, 0.40)  # 苹果 SpeechAnalyzer 图标那种 6 根圆头声波条，平时的高矮
BAR_COL = {'error': ('#FF9186', '#D64541')}
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
                         (user32.BeginDeferWindowPos, _vp, [ctypes.c_int]), (user32.DeferWindowPos, _vp, [_vp, _vp, _vp] + [ctypes.c_int] * 4 + [_u32]),
                         (user32.EndDeferWindowPos, ctypes.c_int, [_vp]),
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
    """屏幕底部中间的浮窗：说话时实时显示识别的字，之后显示改好的结果（标出改动）。
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
        self.mode, self.d, self.visible, self.pinned, self.inside, self.timer = None, {}, False, False, False, None
        self.mouse_down = False  # 上一帧鼠标键是不是按着（点浮窗外面就关）
        self.cards, self.base, self.wgeo, self.bar_c, self.h, self.ck = {}, None, None, (0, 0), self.ROW_H, None
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
    def set_zh(self, zh):
        if self.mode == 'result':
            self.d['zh'] = zh; self.render(); self.arm()
    def notice(self, text, level):
        self.set('notice', text=text, level=level); self.arm()

    def set(self, mode, **d):
        self.mode, self.d, self.pinned = mode, d, False
        if self.timer: self.w.after_cancel(self.timer); self.timer = None
        self.render()

    def arm(self, secs=SHOW_SECONDS):
        if self.timer: self.w.after_cancel(self.timer)
        self.timer = self.w.after(int(secs * 1000), self.timeout)

    def timeout(self):
        self.timer = None
        if self.pinned: return
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
        key = self.ck = h
        if key not in self.cards:
            M, W = self.M, self.W; size = (px(W + 2 * M), px(h + 2 * M)); box = (px(M), px(M), px(M + W), px(M + h))
            R = px(h / 2)  # 胶囊：两头是半圆
            face = _shape(size, box, R, 2)
            sh = Image.new('L', size, 0); ImageDraw.Draw(sh).rounded_rectangle((px(M + 8), px(M + 10), px(M + W - 8), px(M + h + 6)), min(R, px((h - 4) / 2)), fill=80)
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
            self.cards[key] = {'under': under, 'face': face, 'over': over}
        return self.cards[key]

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
        h = max(self.ROW_H, round(th + 30))  # 最矮也按「英文一行 + 中文一行」：一句话从正在听到出结果一样高，不会突然变大
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
        self.render_row()

    def frame(self, level):
        """每帧调：说话 / 思考时让左上角小光球动起来；结果 / 提示显示着时，点屏幕上浮窗以外的地方就关（固定了的不关）。"""
        down = key_down(0x01) or key_down(0x02)
        if down and not self.mouse_down and self.visible and self.wgeo and self.mode in ('result', 'notice') and not self.pinned:
            pt = ctypes.wintypes.POINT(); user32.GetCursorPos(ctypes.byref(pt))
            w, h, x, y = self.wgeo; M = px(self.M)
            if not (x + M <= pt.x <= x + w - M and y + M <= pt.y <= y + h - M): self.hide()
        self.mouse_down = down
        if self.visible and self.mode in ('rec', 'busy', 'result'): self.blit(level)

    def blit(self, level=0.0):
        c = self.cards[self.ck]; img = c['under'].copy()
        wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        x, y = wa.left + (wa.right - wa.left - img.width) // 2, wa.bottom - img.height + px(self.M) - px(18)
        geo = (img.width, img.height, x, y)
        if not self.visible: self.refresh_backdrop()  # 露面之前截后面的屏幕（这时还截不到自己）
        g = self.glass((x, y, x + img.width, y + img.height)); g.putalpha(ImageChops.multiply(g.getchannel('A'), c['face']))
        img.alpha_composite(g)  # 不能用 paste(玻璃, 蒙版)：会把投影层的深色 RGB 混进圆角边缘，一圈黑边
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
    def motion(self, e):
        self.inside = e is not None

    def click(self, e):
        if self.pinned: self.hide()
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
        inset(dn, self.cx - r - X0 / S, self.cy - r - Y0 / S, 2 * r, 2 * r, r, d=ELEV * INSET_K)
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


INSET_K = 0.5  # 凹下去的阴影深度 / 凸起的深度
ELEV = 5  # 主页所有凸起（卡片、导航、右上角三个图标、候选词按钮）的凸起高度：阴影深度都用它，感觉一样高
NAV = ((33, 140), (185, 140), (337, 30))  # 主页导航一排：历史 / 常用词 / 设置，高 30，间距 12；和顶行一样 33~367：HISTORY 左端对 QUILL，设置圆心 (352,487) 落在窗口右下圆角圆心上（和 × 上下对齐）
WORDS = {'loading': 'WAKING', 'ready': 'READY', 'rec': 'LISTENING', 'busy': 'THINKING', 'error': 'ERROR'}

class App:
    W, H = 400, 508  # 主画布到导航一排下面 6；卡片顶上的光（约 10 高）要整个落在 wn 里，接缝才不出线；再往下是常用词那块画布（wn），两块的接缝在两排卡片中间的空隙里

    def __init__(self, root):
        self.root = root; root.title(TITLE); root.configure(bg=BG)
        self.chrome = Chrome(root, taskbar=True, R=48)  # 卡片 r28 + 离边 20
        root.protocol('WM_DELETE_WINDOW', quit_app)
        self.state, self.sil_since, self.shown, self.mic = 'loading', time.time(), 0.0, 'ok'
        self.notice_until, self.ol_last, self.ol_starting = 0.0, None, 0.0
        self.first_shown = False
        self.settings = self.check_win = self.ready_win = self.history = self.words = self.pop = None
        self.cur = None  # 当前这句 [原话, 输入的英文, 中文]
        self.waiting = {}  # 英文出了、中文还没出就开始说下一句的那些：原话 → [原话, 英文, None]，中文到了再记

        n = self.n = Neu(root, self.W, self.H); n.c.pack()
        # 能点的都是凸起的，凹下去的只放内容。和设置 / 记录窗口一套网格：左右离边 20，标题行一样，第一张卡片顶边 y=92，
        # 卡片之间都隔 20，卡片圆角 28（窗口圆角 48 = 28 + 20）
        n.text(33, 26, 'QUILL', 18, INK, family=DISPLAY)  # 和 SETTINGS 一样的宽体大字（不加粗，见 HANDOFF §5）
        n.button(261, 33, 30, 30, icon=I_BALL, size=9, d=ELEV, color=ICON_C, cmd=self.to_ball)   # 缩成悬浮球
        n.button(299, 33, 30, 30, icon=I_MIN, size=9, d=ELEV, color=ICON_C, cmd=self.minimize)
        n.button(337, 33, 30, 30, icon=I_CLOSE, size=9, d=ELEV, color=ICON_C, cmd=quit_app)
        n.raised(20, 92, 360, 360, 28, d=ELEV)                      # 状态卡片（凸起，和设置里的卡片一样）：大字 + 提示 + 刻度和声波条；按住中间那圈说话时那圈凹下去
        self.dots = {}; self.dot = n.c.create_image(0, 0, anchor='center', tags='txt')  # 状态点（PIL 抗锯齿画的圆，Tk 自带的 oval 没有抗锯齿看着不圆）：绿 = 麦克风有输入，黄 = 没声音（待机），红 = 不可用
        self.word = n.text(40, 109, '', 15, INK, family=DISPLAY)  # 左边缘都对在 40：QUILL / READY / HISTORY / 候选词
        self.sub = n.text(200, 416, '', 11, SUB, anchor='n'); n.c.itemconfigure(self.sub, justify='center')  # 提示放圆盘下面居中
        n.raised(200 - 124, 148, 248, 248, 124, d=ELEV)  # 圆盘本身是凸起的大圆（能点 = 凸起），整页的视觉主角；按住时凹下去
        self.dial = Dial(n, 200, 272, 124, (lambda: eng.start('mouse'), eng.stop))
        # 说哪种语言（出来的都是英文）：两个小胶囊和 READY 同一行、右边对齐卡片内边；选中的凹下去
        self.lang_sw = Switch(n, 299, 105, 30, 30, 8, (('en', 'EN'), ('zh', 'CN')), cfg['lang'], self.set_lang); n.btns.append(self.lang_sw)
        n.button(33, 472, 140, 30, r=15, d=ELEV, cmd=self.open_history)
        n.button(185, 472, 140, 30, r=15, d=ELEV, cmd=self.edit_words)
        n.button(337, 472, 30, 30, icon=I_SET, size=9, d=ELEV, color=ICON_C, cmd=self.open_settings)
        self.nav_label(n, 103, I_HIST, 'HISTORY'); self.nav_label(n, 255, I_EDIT, 'WORDS')
        n.commit()
        self.wn = Neu(root, self.W, 122); self.wn.c.pack(); self.build_words()
        self.set_state('loading')
        self.overlay = Overlay(self); self.ball = Ball(self)
        root.after(2500, self.prewarm)
        threading.Thread(target=self.ollama_loop, daemon=True).start()
        threading.Thread(target=self.words_loop, daemon=True).start()
        self.poll()

    def nav_label(self, n, cx, icon, label):
        """导航按钮里的字：图标 + 宽体小标题，一起在按钮里居中；图标和文字的垂直中心对齐（HIST_DY 实测补偿）。"""
        fi, ft = fnt(11, family=ICON), fnt(11, family=DISPLAY); gap = px(6)
        x0 = px(cx) - (fi.measure(icon) + gap + ft.measure(label)) / 2
        n.c.create_text(x0, px(487) + HIST_DY + NAV_DY, text=icon, font=fi, fill=ICON_C, anchor='w', tags='txt')
        n.c.create_text(x0 + fi.measure(icon) + gap, px(487) + NAV_DY, text=label, font=ft, fill=INK, anchor='w', tags='txt')  # 宽体字和 READY 同色

    # 子窗口
    def _open(self, attr, cls):
        """子窗口第一次打开时建好；关掉只是藏起来，再开直接显示（不用重画，不卡）。"""
        w = getattr(self, attr)
        if not (w and w.w.winfo_exists()): w = cls(self); setattr(self, attr, w)
        if getattr(w, 'dirty', False): w.dirty = False; w.build()
        if not w.w.winfo_viewable(): place_side(self, w.w, w.n.w); w.w.deiconify(); w.w.chrome.sync()
        w.w.lift()

    def first_run(self):
        """启动后看一眼：有缺的东西（红 / 橙）就弹出「准备情况」，每次启动最多一次；没有 N 卡属正常降级，不弹。"""
        if self.ol_last is None: return self.root.after(3000, self.first_run)
        if any(lv in ('fail', 'warn') for lv, _, _ in readiness(self.ol_last)[:3]): log('有缺的东西，打开准备情况'); self.open_ready()

    def prewarm(self):
        """启动后先把设置、记录窗口在后台建好（藏着），第一次点开也不卡。"""
        for attr, cls in (('settings', Settings), ('history', History), ('words', Words)):
            if not self.alive(attr): setattr(self, attr, cls(self))

    def open_settings(self): self._open('settings', Settings)
    def open_checks(self): self._open('check_win', Checks)
    def open_ready(self): self._open('ready_win', Ready)
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

    def build_words(self):
        """主页最下面的常用词卡片：标题 + 灰字词表，整张卡片能点（打开常用词窗口，CardBtn）；Quill 从记录里找到可能听错的词时，
        下面多一行「听到 → 正确写法」+ 加入 / 忽略（右对齐卡片内边）和灰字「可能听错 · 说过几次 · 例句」，最多 3 条，处理完补上下一条。
        卡片变高窗口跟着变高（Chrome.sync 会重画圆角和阴影）。"""
        wn = self.wn; sugs = word_sugs(); x, y, w = 20, 14, 360
        show = sugs[:1]; extra = len(sugs) - 1  # 只显示第一条，其余写「还有 N 条」，点卡片进常用词窗口；没有候选就没有这张卡片
        f12, f11 = fnt(12), fnt(11); lh12, lh11 = f12.metrics('linespace') / S, f11.metrics('linespace') / S
        # 「听到」「→ 正确写法」永远分两行（听到的灰字、正确写法黑字，太长用省略号）；下面灰字例句最多 2 行
        rows = [([fit(hd, f12, px(w - 170)), fit(f'→ {c}', f12, px(w - 170))], fit(f'可能听错 · 说过 {k} 次 · {src}', f11, px(w), 2).split('\n')) for hd, c, k, src in show]
        BH = 34  # 按钮高；文字块（可能折成两行）和按钮组在 m 高的格子里各自垂直居中
        mm = [BH for a, b in rows]
        rh = [m + 4 + len(b) * lh11 + 16 for (a, b), m in zip(rows, mm)]  # 一行 = 格子 + 灰字 + 16 间距（分隔线画在间距正中）
        h = round(20 + sum(rh) + 4 + (18 if extra > 0 else 0)) if show else 0; H = y + h + 20 if show else 27  # 没有卡片时导航下面留 33（主画布已有 6）：和顶上一样，按钮圆心离窗口边 48 = 窗口圆角，同心
        wn.reset(H); wn.c.configure(height=px(H))
        for nx, nw in NAV: wn.raised(nx, 472 - self.H, nw, 30, 15, d=ELEV)  # 上面三个导航按钮的阴影伸过来
        if not show: wn.commit(); return
        down = wn.img.copy()  # 试验：候选词不要卡片框，直接放在窗口底色上（点这一块仍打开常用词窗口，按下时没有凹陷效果）；想要框回来：inset(down, x, y, w, h, 28, 5) + wn.raised(x, y, w, h, 28, d=7)
        if extra > 0: wn.text(x + w, y + h - 30, f'还有 {extra} 条', 11, SUB, anchor='ne')
        ry = y + 20
        for i, ((hd, c, k, src), (a, b), r, m) in enumerate(zip(show, rows, rh, mm)):
            if i: wn.c.create_line(px(x), px(ry - 8), px(x + w), px(ry - 8), fill='#DCDCDC', width=1)
            ty = ry + (m - 2 * lh12) / 2; wn.text(x, ty, a[0], 12, SUB); wn.text(x, ty + lh12, a[1], 12, TEXT)
            for bx, bw, t, fn in ((158, 56, '加入', self.add_word), (96, 56, '修改', self.fix_word)):  # 两列等宽 56、间距 6
                wn.button(x + w - bx, ry + (m - BH) / 2, bw, BH, text=t, size=11, d=ELEV, color=ICON_C, cmd=lambda fn=fn, hd=hd, c=c: fn(hd, c))
            wn.button(x + w - 34, ry + (m - BH) / 2, BH, BH, icon=I_CLOSE, size=8, d=ELEV, color=ICON_C, cmd=lambda hd=hd, c=c: self.ignore_word(hd, c))  # 忽略 = 一个叉（圆），右边缘 = 卡片内边 20
            wn.text(x, ry + m + 4, '\n'.join(b), 11, SUB)  # 灰字和上面的文字同一左边缘
            ry += r
        for b in wn.btns: b.draw(down, *b.box[:2], False)
        wn.commit(); wn.btns.insert(0, CardBtn(wn, (x, y, w, h), down, self.edit_words))  # 点卡片任何地方 = 打开常用词窗口

    def edit_words(self): self._open('words', Words)
    open_words = edit_words  # 截图测试 --show=words 用

    def add_word(self, heard, term):
        """候选词「加入」：词表里已有这个正确写法就接在那一行后面，没有就加一行。"""
        lines = Words.lines()
        for i, ln in enumerate(lines):
            t, _, alts = ln.replace('：', ':').partition(':')
            if not ln.startswith('#') and t.strip().lower() == term.lower():
                lines[i] = f'{t.strip()}: {alts.strip()}, {heard}' if alts.strip() else f'{t.strip()}: {heard}'; break
        else: lines.append(f'{term}: {heard}')
        try: WORDS_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8'); log(f'常用词加入：{term}: {heard}')
        except Exception: log('写常用词失败', traceback.format_exc())
        self.words_changed()

    def fix_word(self, heard, term):
        """候选词「修改」：打开常用词窗口，输入框里预填「建议写法: 听到的」，用户把冒号前改成对的（如 Dongdaqiao / 东大桥），Enter 就按他写的加入。"""
        self._open('words', Words); ws = self.words
        ws.editing = None; ws.ent.delete(0, 'end'); ws.ent.insert(0, f'{term}: {heard}')
        ws.build(keep=True); ws.focus(); ws.ent.selection_range(0, len(term)); ws.ent.icursor(len(term))

    def ignore_word(self, heard, term):
        with scan_lock: st = scan_load(); st['ignored'].append(f'{heard.lower()}\t{term}'); scan_save(st)
        self.words_changed()

    def words_changed(self):
        self.build_words()
        if self.alive('words'): self.words.build(keep=True)

    def set_key(self, vk):
        cfg['hotkey'] = vk; save_cfg(); log('快捷键改为', key_name(vk)); self.refresh_sub()

    def set_lang(self, lang):
        """主页 EN / 中：切换说哪种语言（Whisper 按 language=en / zh 认）。"""
        if cfg['lang'] == lang: return
        cfg['lang'] = lang; save_cfg(); log('说话语言：', lang)
        self.lang_sw.select(lang)
        threading.Thread(target=llm_hello, daemon=True).start()  # 两种语言的提示词不一样：先用新提示词跑一次，切完说的第一句不慢
        if self.state in ('loading', 'ready', 'error'): self.set_state(eng.idle_state())

    def sub_text(self):
        return {'loading': 'Initializing...', 'ready': f'按住「{key_name(cfg["hotkey"])}」或按住中间这一圈',
                'rec': '说完松开', 'busy': '改语病 + 翻译，几秒钟', 'error': eng.load_error}[self.state]

    def refresh_sub(self):
        if time.time() < self.notice_until: return
        self.n.c.itemconfigure(self.sub, text=fit(self.sub_text(), fnt(11), px(312), 2), fill=SUB)

    def show_notice(self, text, level):
        self.notice_until = time.time() + 15
        self.n.c.itemconfigure(self.sub, text=fit(text, fnt(11), px(312), 2), fill={'err': RED, 'ok': GREEN}.get(level, ORANGE))
        self.root.after(15100, self.refresh_sub)

    def update_dot(self):
        """READY 右边的状态点：红 = 出错 / 麦克风没打开，黄 = 麦克风 10 秒没声音（待机），绿 = 正常有输入。"""
        c = self.n.c; x0, y0, x1, y1 = c.bbox(self.word); cy = (y0 + y1) / 2
        col = RED if self.state == 'error' or self.mic == 'off' else YELLOW if self.mic == 'silent' else GREEN
        if col not in self.dots:
            n = px(6) + 2; ss = 8; im = Image.new('RGBA', (n * ss, n * ss), (0, 0, 0, 0))
            ImageDraw.Draw(im).ellipse((ss, ss, (n - 1) * ss, (n - 1) * ss), fill=col)
            self.dots[col] = ImageTk.PhotoImage(im.resize((n, n), Image.LANCZOS))
        c.coords(self.dot, x1 + px(15), cy); c.itemconfigure(self.dot, image=self.dots[col])

    def set_state(self, s, toast=False):
        self.state = s
        self.n.c.itemconfigure(self.word, text=WORDS[s], fill=RED if s == 'error' else INK)
        self.refresh_sub(); self.update_dot()
        if s == 'rec': self.overlay.listen(toast)
        if s == 'busy': self.overlay.think('')
        if s == 'error': self.show_notice(eng.load_error, 'err')

    def finish(self):
        """英文和中文都有了：记下来。"""
        if not (self.cur and self.cur[1] is not None and self.cur[2] is not None): return
        self.record(*self.cur); self.cur = None

    def park(self):
        """新的一句要占 cur 了：上一句英文已经输入、中文还在出，先挂起来，中文到了照样记进记录（以前直接被盖掉，记录里少一句）。"""
        if self.cur and self.cur[1] is not None and self.cur[2] is None: self.waiting[self.cur[0]] = self.cur

    def record(self, raw, fixed, zh):
        save_history(raw, fixed, zh)
        h = self.alive('history')
        if h:  # 藏着的记录窗口先记一下，打开时再重画
            if h.w.winfo_viewable(): h.build()
            else: h.dirty = True

    def ollama_loop(self):
        while True:
            st = ollama_status()
            if st == 'down': st = ensure_ollama()  # 没开就自己拉起，不用用户管
            post('ollama', st); time.sleep(8)

    def idle(self):
        """闲着：没在说话 / 改错，2 分钟内没说过话，改错服务正常且在显卡上（CPU 上一批要十几秒，会挡住下一句）。"""
        return self.state == 'ready' and time.time() - last_llm[0] > 120 and self.ol_last == 'ok' and llm_opts()[1]

    def words_loop(self):
        """每 5 分钟看一次：距上次扫满一天、闲着，就扫新记录找可能听错的词。每批约 0.5~3 秒，用户一开始说话就停，下次接着扫。"""
        time.sleep(120)
        while True:
            st = scan_load()
            if cfg.get('hist', True) and time.time() - st['at'] > 86400 and self.idle():
                new = [e for e in said_lines(hist_blocks()) if e[0] >= st['done']]  # 记录时间只到分钟：用 >，和上次最后一句同一分钟后说的就永远扫不到；重扫那几句无妨（候选 setdefault）
                try:
                    for part in scan_batches(new, key=lambda e: e[1]):
                        if not self.idle(): break
                        for h, c, src in find_candidates([t for _, t in part]): st['cands'].setdefault(f'{h.lower()}\t{c}', [h, c, src])
                        st['done'] = part[-1][0]
                    else: st['at'] = time.time()
                except Exception as e: log('找候选词失败，1 小时后再试：', repr(e)); st['at'] = time.time() - 86400 + 3600  # 别每 5 分钟重试一次（每次最多占模型 60 秒）
                with scan_lock: st['ignored'] = scan_load()['ignored']; scan_save(st)  # 扫的时候用户可能点了「忽略」，别用开扫前读的盖掉
                if st['at'] > time.time() - 60: log(f'找候选词：扫了 {len(new)} 句，{len(word_sugs())} 个待确认'); post('words')
            time.sleep(300)

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
            self.update_dot()
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
        elif kind == 'words': self.build_words()  # 找到新的可能听错的词：放在主页常用词卡片里
        elif kind == 'notice':
            text, level, toast = a; self.show_notice(text, level)
            if toast: self.overlay.notice(text, level)
        elif kind == 'mic_bad': self.sil_since = 0
        elif kind == 'live': self.overlay.live(*a)
        elif kind == 'thinking': self.overlay.think(a[0])
        elif kind == 'fixed':
            raw, fixed, by_key = a; self.park(); self.cur = [raw, fixed, None]; self.overlay.result(raw, fixed, None, by_key)
            if not by_key: self.show_notice('已复制到剪贴板，去要输入的地方按 Ctrl+V。', 'ok')
        elif kind == 'zh':
            raw, zh = a
            if self.cur and self.cur[0] == raw: self.cur[2] = zh; self.overlay.set_zh(zh); self.finish()
            elif raw in self.waiting: w = self.waiting.pop(raw); self.record(w[0], w[1], zh)
        elif kind == 'ollama':
            st = a[0]
            if st != self.ol_last:
                log('改错服务：', st)
                if st == 'down' and time.time() - self.ol_starting > 30: self.show_notice('改错服务没开：去设置里点「启动」。', 'warn')
            changed, self.ol_last = st != self.ol_last, st
            if changed: self.refresh_settings()
        elif kind == 'key_recorded':
            if self.alive('settings'): self.settings.key_recorded(a[0])
        elif kind == 'mode':  # Whisper 载完 / 载不了：第一次启动有缺的东西就自己弹出准备情况窗口
            self.refresh_sub()
            if not self.first_shown: self.first_shown = True; self.root.after(5000, self.first_run)
        elif kind == 'check' and self.alive('check_win'): self.check_win.update(*a)
        elif kind == 'checks_done' and self.alive('check_win'): self.check_win.done(a[0])


class Settings:
    """设置窗口：麦克风 / 快捷键 / 说中文时 / 改错服务 四块卡片。"""
    W = 400
    ZH_OUTS = (('en', '出英文', ''), ('zh', '出中文', ''))  # 出中文：删掉口头禅 / 重复 / 改口，不翻译

    def __init__(self, app):
        self.app, self.recording = app, False
        self.key_tip, self.tip_state = ('', SUB), 'ok'
        self.Wd = self.W
        self.w = sub_window(app, '设置', self.Wd)
        self.n = Neu(self.w, self.Wd, 600); self.n.c.pack(); self.build()
        self.w.bind('<MouseWheel>', lambda e: self.scroll(-int(e.delta / 120) * 3))

    # 每一块：先算高度（要先画卡片底再画内容），返回 (高度, 画法)
    def sec_mic(self, n, x, y, w, draw):
        devs = ['系统默认'] + eng.devices(); cur = cfg['device'] if cfg['device'] in devs else '系统默认'
        dev_y = y + 56; h = dev_y + len(devs) * 44 + 10 - y  # 音量条放在标题那行右边，设置一页放得下不用滚
        if not draw: return h
        self.label(n, x, y, w, h, '麦克风')
        for i, name in enumerate(devs):
            on = name == cur
            n.button(x + 20, dev_y + i * 44, w - 40, 34, r=17, text=name, icon=I_OK if on else '', size=12, on=on, align='left', cmd=lambda name=name: self.pick_dev(name))
        self.bar = None
        if self.tip_state != 'ok':  # 出问题时标题行右边换成一句橙 / 红字（不画音量条）；正常时不放字，黑条会跳就是好的
            text, color = {'silent': ('10 秒没声音 · 静音键常是 F4', ORANGE), 'off': ('打不开：换一个设备', RED)}[self.tip_state]
            n.text(x + w - 20, y + 25, text, 11, color, anchor='ne', width=w - 110, lines=1)
            return h
        track_y = y + 28; n.inset(x + 100, track_y, w - 120, 10, 5, d=2.5)
        self.bar_x, self.bar_w = x + 106, w - 132
        self.bar = n.c.create_line(px(self.bar_x), px(track_y + 5), px(self.bar_x), px(track_y + 5), width=px(4), fill=ACCENT, capstyle='round')
        return h

    def sec_key(self, n, x, y, w, draw):
        keys = list(KEYS.values()) + ([cfg['hotkey']] if cfg['hotkey'] not in KEYS.values() else [])
        cols, gap = 4, 10; cw = (w - 40 - gap * (cols - 1)) / cols  # 4 列网格：每个一样宽、间距一样
        chips = [(vk, x + 20 + (i % cols) * (cw + gap), (i // cols) * 46, cw) for i, vk in enumerate(keys + [None])]
        cy = chips[-1][2]
        chip_y = y + 56; h = chip_y + cy + 34 + 20 - y  # 提示字放在标题那行右边
        if not draw: return h
        self.label(n, x, y, w, h, '快捷键')
        for vk, cx, dy, cw in chips:
            if vk is None:
                self.rec_btn = n.button(cx, chip_y + dy, cw, 34, text='请按键…' if self.recording else '录制', size=12,
                                        color=ORANGE if self.recording else TEXT, cmd=self.record)
            else:
                on = vk == cfg['hotkey']
                n.button(cx, chip_y + dy, cw, 34, text=key_name(vk), size=12, on=on, cmd=lambda vk=vk: self.set_hot(vk))
        self.ktip = n.text(x + w - 20, y + 25, self.key_tip[0], 11, self.key_tip[1], anchor='ne', width=w - 110, lines=1)
        return h

    def sec_mode(self, n, x, y, w, draw):
        h = 62  # 一排按钮，上下各留 14
        if not draw: return h
        self.label(n, x, y, w, h, '说中文时')
        for i, (m, name, _) in enumerate(self.ZH_OUTS):
            n.button(x + w - 210 + i * 100, y + 14, 90, 34, text=name, size=12, on=m == cfg.get('zh_out', 'en'), cmd=lambda m=m: self.set_zh_out(m))
        return h

    def sec_ol(self, n, x, y, w, draw):
        h = 220  # 常用词在主页；改错模型一排（和「说中文时」那排一样右对齐）+ 释放显存一排
        if not draw: return h
        app = self.app; st = app.ol_last
        if st == 'down' and time.time() - app.ol_starting < 30: st = 'starting'
        self.label(n, x, y, w, h, '改错服务')
        text, color = {'ok': ('正常', GREEN), 'no_model': (f'缺少 {LLM_MODEL}', RED), 'down': ('没有运行', ORANGE),
                       'starting': ('启动中…', ORANGE), None: ('检查中…', SUB)}[st]
        n.text(x + w - 20, y + 22, '● ' + text, 12, color, anchor='ne')
        on_gpu = llm_opts()[1]  # 说明只留一行：在哪跑 + 上一句用了几秒
        info = f'Ollama · {LLM_MODEL} · ' + ('显卡' if on_gpu else 'CPU' + ('（显卡慢，暂时换）' if LLM_GPU_LAYERS else ''))
        if llm_stat: info += f' · 上一句 {llm_stat["secs"]:.1f} 秒'
        n.text(x + 20, y + 56, info, 11, SUB, width=w - (130 if st == 'down' else 40), lines=1)
        if st == 'down': n.button(x + w - 90, y + 46, 70, 34, text='启动', size=12, cmd=self.start_ol)
        n.text(x + 20, y + 96, '改错模型', 12, TEXT)
        for i, (k, name) in enumerate(LLM_MODELS):
            n.button(x + w - 210 + i * 100, y + 86, 90, 34, text=name, size=12, on=k == LLM_MODEL, cmd=lambda k=k: self.set_llm(k))
        n.text(x + 20, y + 138, '不用时释放显存', 12, TEXT)
        cols, gap = len(KEEPS), 10; cw = (w - 40 - gap * (cols - 1)) / cols  # 和快捷键那排一样：等宽、等间距
        for i, (k, name) in enumerate(KEEPS):
            n.button(x + 20 + i * (cw + gap), y + 166, cw, 34, text=name, size=12, on=k == cfg.get('keep', '2m'), cmd=lambda k=k: self.set_keep(k))
        return h

    @classmethod
    def full_height(cls):
        """完整设置窗口的高度（记录窗口也用它，两个窗口一样高）。各块不画只量高度时用不到 self。"""
        secs = (cls.sec_mic, cls.sec_key, cls.sec_mode, cls.sec_ol)
        return 92 + sum(f(None, None, 20, 0, 360, False) for f in secs) + 20 * (len(secs) - 1) + 20  # 最后一张卡片离底边 20 = 卡片圆角和窗口圆角同心

    @classmethod
    def view_height(cls):
        """窗口实际多高：最多到屏幕工作区高度减上下各 20，超出的内容滚着看（麦克风多 / 屏幕矮时会超）。"""
        wa = ctypes.wintypes.RECT(); user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        return min(cls.full_height(), int((wa.bottom - wa.top) / S) - 40)

    def scroll(self, k):
        self.n.c.yview('scroll', k, 'units'); self.sbar.update()

    def label(self, n, x, y, w, h, name):
        n.raised(x, y, w, h, 28, d=ELEV)
        n.text(x + 20, y + 24, name, 12, TEXT)

    def build(self):
        n, app = self.n, self.app
        secs = [getattr(self, 'sec_' + s) for s in ('mic', 'key', 'mode', 'ol')]
        x, w, y0, gap = 20, 360, 92, 20
        H = self.full_height()
        V, oy = self.view_height(), n.c.canvasy(0) / S  # 改了设置重画时停在原来滚到的位置
        n.reset(H); n.c.configure(height=px(V), scrollregion=(0, 0, px(self.Wd), px(H)), yscrollincrement=px(20))
        n.text(33, 26, 'SETTINGS', 18, INK, family=DISPLAY)
        n.button(261, 33, 30, 30, icon=I_SETUP, size=9, color=ICON_C, cmd=app.open_ready)  # 准备情况；和主页右上角三个圆同位置
        n.button(299, 33, 30, 30, icon=I_CHECK, size=9, color=ICON_C, cmd=app.open_checks)  # 自检
        close_btn(n, self.Wd, self.w.withdraw)
        y = y0
        for s in secs: y += s(n, x, y, w, True) + gap
        self.sbar = ScrollBar(n, H, V)
        n.commit()
        n.c.tag_raise(self.sbar.thumb); n.c.yview_moveto(oy / H); self.sbar.update()

    def tick(self, level, mic):
        if mic != self.tip_state:
            self.tip_state = mic; self.build(); return
        if not self.bar: return
        c = self.n.c; _, y0, _, y1 = c.coords(self.bar)
        c.coords(self.bar, px(self.bar_x), y0, px(self.bar_x) + level * px(self.bar_w), y1)

    def pick_dev(self, name):
        cfg['device'] = None if name == '系统默认' else name; save_cfg()
        self.app.sil_since = time.time(); eng.open(cfg['device']); self.app.refresh_settings()

    def set_hot(self, vk):
        self.app.set_key(vk); self.key_tip = ('', SUB); self.build()

    def set_keep(self, k):
        cfg['keep'] = k; save_cfg(); log('释放显存：', k); self.app.refresh_settings()
        threading.Thread(target=apply_keep, daemon=True).start()

    def set_llm(self, k):
        if k == LLM_MODEL: return
        set_llm(k); self.app.refresh_settings()

    def set_zh_out(self, o):
        cfg['zh_out'] = o; save_cfg(); log('说中文时输出', o); self.app.refresh_settings()
        if cfg['lang'] == 'zh': threading.Thread(target=llm_hello, daemon=True).start()  # 换了提示词，同上


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
    """子窗口右上角的关闭按钮：和主页 × 一样，圆心 = 窗口圆角圆心（离上、右都是 48）。"""
    n.button(w - 63, 33, 30, 30, icon=I_CLOSE, size=9, color=ICON_C, cmd=cmd)


class History:
    """最近 20 条记录：标题行和「记录设置」卡片固定不动，下面的卡片可以滚；列表上下边缘渐隐（内容慢慢淡出，不是一刀切）；
    右边一根细滑块，点它或拖它能快速回到顶上。"""
    W, FADE, PAD = 400, 22, 20  # 上面渐隐带高度（短了卡片滚上去会被一刀切出半截圆角）；固定区里卡片下面留给阴影的边（阴影约 20 才淡完，再少会被切一刀）
    CARD_Y = 92                 # 设置卡片顶边，和设置窗口第一块对齐
    FADE_B, SOLID = 56, 20      # 下面渐隐带：总高、最底下实心的一段（= 窗口边距）

    def __init__(self, app):
        self.app = app; self.w = sub_window(app, '记录', self.W); self.opened = set()  # 展开了原话的记录（时间, 原话）
        self.arm_clear = False  # 「清空记录」点了第一下：再点一下才真删
        self.view = Settings.view_height()  # 和设置窗口一样高
        self.head = Neu(self.w, self.W, 100); self.head.c.pack()  # 固定区（标题 + 设置卡片），高度 build 时按卡片定
        self.n = Neu(self.w, self.W, 100); self.n.c.pack()
        self.w.bind('<MouseWheel>', lambda e: self.scroll('scroll', -int(e.delta / 120) * 3, 'units'))
        self.build()

    def scroll(self, *a):
        self.n.c.yview(*a); self.bar.update()

    def build(self, keep=False):
        on = cfg.get('hist', True)
        n = self.n; recs = list(reversed(load_history(20))) if on else []
        fe, fz, fm = fnt(12), fnt(11), fnt(11)
        oy = n.c.canvasy(0) / S if keep else 0  # 展开 / 收起原话后还停在原来滚到的地方
        ch = 160 if on else 68  # 「记录设置」卡片：保存记录 开 / 关；开着时再加 保留几天、清空
        h = self.head; top_h = self.CARD_Y + ch + self.PAD
        self.lv = lv = self.view - top_h  # 固定区高度、列表视口高度
        h.reset(top_h); h.c.configure(height=px(top_h))
        h.text(33, 26, 'HISTORY', 18, INK, family=DISPLAY)
        close_btn(h, self.W, self.w.withdraw)
        h.button(299, 33, 30, 30, icon=I_OPEN, size=9, color=ICON_C, cmd=lambda: os.startfile(HIST) if HIST.exists() else None)  # 用记事本打开
        self.prefs(h, self.CARD_Y, ch, on); h.commit()
        n.c.configure(height=px(lv))
        cards, y = [], 14  # 停着时第一行灰字在渐隐带下面，不被盖淡
        for raw, fixed, zh, when in recs:  # 卡片上面灰字：时间 · 原话（一行放不下时右边有展开钮）；卡片里只有改后的英文 + 中文，复制按钮 28 对着各自那段字上下居中
            en, cn = wrap(fixed, fe, px(280)), wrap(zh, fz, px(280))
            he, hz = len(en) * fe.metrics('linespace') / S, len(cn) * fz.metrics('linespace') / S
            meta = f'{when}  ·  说：{raw}'; key = (when, raw); full = wrap(meta, fm, px(280))
            lines = full if key in self.opened else [fit(meta, fm, px(280))]
            top = y + len(lines) * fm.metrics('linespace') / S + 10  # 灰字下面隔一点就是卡片
            ey = top + max(14, 24 - he / 2)                  # 字离卡片边 14，按钮离卡片边至少 10
            zy = max(ey + he + 8, ey + he / 2 + 36 - hz / 2)  # 两个按钮之间至少隔 6
            h = (max(zy + hz + 14, zy + hz / 2 + 24) if zh else max(ey + he + 14, ey + he / 2 + 24)) - top  # 出中文的没有中文那行
            cards.append((y, top, h, ey, he, zy, hz, lines, key if len(full) > 1 else None, fixed, zh, en, cn)); y = top + h + 20
        H = max(lv, y - 20 + self.FADE_B)  # 拉到底时最后一张卡片露在下面的渐隐带上面
        n.reset(H); n.c.configure(scrollregion=(0, 0, px(self.W), px(H)), yscrollincrement=px(20))
        if not recs: n.text(200, y + 10, '还没有记录' if on else '记录已关闭：说的话不会存到电脑上', 11, SUB, anchor='center')
        for y, top, h, ey, he, zy, hz, lines, key, fixed, zh, en, cn in cards:
            n.text(40, y, '\n'.join(lines), 11, SUB)
            if key:  # 原话一行放不下：小圆钮（和复制按钮同一竖列，对着第一行灰字居中），点一下展开全文，再点收起
                cy = y + fm.metrics('linespace') / S / 2
                n.button(346, cy - 10, 20, 20, icon=I_UP if key in self.opened else I_DOWN, size=7, d=ELEV * INSET_K, color=ICON_C,
                         cmd=lambda key=key: self.toggle(key))
            n.raised(20, top, 360, h, 24, d=ELEV)
            n.text(40, ey, '\n'.join(en), 12, TEXT); n.text(40, zy, '\n'.join(cn), 11, SUB)
            for t, cy in ((fixed, ey + he / 2), (zh, zy + hz / 2))[:2 if zh else 1]:  # 复制钮圆心 x=356 = 卡片圆角（24）圆心
                b = n.button(341, cy - 15, 30, 30, icon=I_COPY, size=9, color=ICON_C)
                b.cmd = lambda t=t, b=b: (set_clip(t), b.set(icon=I_OK), b.n.c.after(1200, lambda: b.set(icon=I_COPY)))
        self.bar = ScrollBar(n, H, lv)
        n.commit()
        for y, top in ((0, True), (lv - self.FADE_B, False)):  # 上下两条渐隐带，钉在视口里
            self.bar.pin(n.c.create_image(0, 0, anchor='nw', image=self.fade(top)), y)
        n.c.tag_raise(self.bar.thumb); n.c.yview_moveto(oy / H); self.bar.update()

    def toggle(self, key):
        self.opened ^= {key}; self.build(keep=True)

    def prefs(self, n, y, h, on):
        """记录设置卡片：右边是 3 列网格（每格 70 宽、间隔 10，右对齐），开 / 关占后两列，清空跨后两列，上下对齐。"""
        x, w = 20, 360; col = lambda i: x + w - 20 - (3 - i) * 70 - (2 - i) * 10
        n.raised(x, y, w, h, 28, d=ELEV)
        n.text(x + 20, y + 24, '保存记录', 12, TEXT)
        for i, (v, name) in enumerate(((True, '开'), (False, '关'))):
            n.button(col(i + 1), y + 14, 70, 34, text=name, size=12, on=v == on, cmd=lambda v=v: self.set_hist(v))
        if not on: return
        n.text(x + 20, y + 70, '只留最近', 12, TEXT)
        for i, (d, name) in enumerate(HIST_DAYS):
            n.button(col(i), y + 60, 70, 34, text=name, size=12, on=d == cfg.get('hist_days', 0), cmd=lambda d=d: self.set_days(d))
        n.text(x + 20, y + 116, f'共 {len(hist_blocks())} 条', 11, SUB)
        self.clear_btn = n.button(col(1), y + 106, 150, 34, text='再点一下确认' if self.arm_clear else '清空记录', size=12,
                                  color=RED if self.arm_clear else TEXT, cmd=self.clear)

    def set_hist(self, v):
        cfg['hist'] = v; save_cfg(); log('保存记录：', '开' if v else '关'); self.arm_clear = False; self.build()

    def set_days(self, d):
        cfg['hist_days'] = d; save_cfg(); log('记录只留：', d or '永久'); trim_history(); self.build()

    def clear(self):
        """删掉 history.txt（撤不回）：第一下变红「再点一下确认」，3 秒内再点才删。"""
        if not self.arm_clear:
            self.arm_clear = True; self.build(keep=True)
            self.w.after(3000, lambda: self.arm_clear and (setattr(self, 'arm_clear', False), self.build(keep=True)))
            return
        self.arm_clear = False
        try: HIST.unlink(missing_ok=True); log('记录已清空')
        except Exception: log('清空记录失败', traceback.format_exc())
        self.build()

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


class Words:
    """常用词窗口（主页常用词卡片的「编辑」）：固定区是输入框（「正确写法: 听错的写法, …」，Enter 加入），下面每条一张卡片，
    右边 改 / 删（删要连点两下）；词多了只有列表滚、边缘渐隐，和记录窗口一样。写回 words.txt 只动那一行，注释和手写的格式原样留着。"""
    W, FADE, FADE_B, SOLID, CARD_Y, PAD = 400, 22, 56, 20, 92, 20
    fade, scroll, _fades = History.fade, History.scroll, History._fades  # 渐隐带和记录窗口一样宽、一样高，共用

    def __init__(self, app):
        self.app = app; self.w = sub_window(app, '常用词', self.W)
        self.editing = self.arm_del = self.arm_job = None  # 正在改的那一行（原文）；点了一下删除的那一行；它 3 秒后变回来的计时
        self.view = Settings.view_height()  # 和设置窗口一样高
        self.head = Neu(self.w, self.W, 100); self.head.c.pack()
        self.n = Neu(self.w, self.W, 100); self.n.c.pack()
        self.ent = tk.Entry(self.head.c, bd=0, relief='flat', highlightthickness=0, bg=INSET, fg=TEXT, insertbackground=TEXT, font=fnt(12))
        self.ent.bind('<Return>', lambda e: self.submit()); self.ent.bind('<Escape>', lambda e: self.cancel())
        self.w.bind('<MouseWheel>', lambda e: self.scroll('scroll', -int(e.delta / 120) * 3, 'units'))
        self.w.bind('<Map>', lambda e: e.widget is self.w and self.w.after(60, self.focus))  # 打开就能直接打字
        self.build()

    @staticmethod
    def lines():
        try: return WORDS_FILE.read_text(encoding='utf-8-sig').splitlines()
        except OSError: return WORDS_HEAD.rstrip('\n').split('\n')

    def build(self, keep=False):
        rows = [(ln, p) for ln in self.lines() if (p := parse_word(ln))][::-1]  # 新加的在上面
        n, h = self.n, self.head; fe, fz = fnt(12), fnt(11)
        le, lz = fe.metrics('linespace') / S, fz.metrics('linespace') / S
        oy = n.c.canvasy(0) / S if keep else 0
        y, ch = self.CARD_Y, 110; top_h = y + ch + self.PAD; self.lv = lv = self.view - top_h
        h.reset(top_h); h.c.configure(height=px(top_h))
        h.text(33, 26, 'WORDS', 18, INK, family=DISPLAY)
        close_btn(h, self.W, self.w.withdraw)
        h.button(299, 33, 30, 30, icon=I_OPEN, size=9, color=ICON_C, cmd=self.open_file)  # 用记事本改：复杂写法 / 一次改很多
        h.raised(20, y, 360, ch, 28, d=ELEV)
        h.inset(40, y + 20, 230, 40, 20, d=ELEV * INSET_K)  # 输入框凹下去，按钮和它上下居中、右对齐卡片内边
        h.c.create_window(px(56), px(y + 40), window=self.ent, anchor='w', width=px(198))
        h.button(290, y + 23, 70, 34, text='保存' if self.editing else '加入', size=12, cmd=self.submit)
        tip = (f'正在改「{parse_word(self.editing)[0]}」 · Enter 保存 · Esc 取消' if self.editing
               else '例：Claude: cloud, 克劳德（冒号后面是听错的写法，可不写）')
        h.text(40, y + 72, tip, 11, SUB, width=320, lines=1)
        h.commit()
        n.c.configure(height=px(lv))
        cards, y = [], 14 + lz + 10  # 最上面一行灰字（几条），下面隔一点是卡片
        for ln, (term, alts) in rows:
            al = wrap('听错：' + '、'.join(alts), fz, px(240)) if alts else []
            ch = max(48, 28 + le + (4 + len(al) * lz if al else 0))
            cards.append((y, ch, ln, term, al)); y += ch + 20
        H = max(lv, y - 20 + self.FADE_B)  # 拉到底时最后一张卡片露在下面的渐隐带上面
        n.reset(H); n.c.configure(scrollregion=(0, 0, px(self.W), px(H)), yscrollincrement=px(20))
        n.text(40, 14, f'{len(rows)} 条' if rows else '还没有常用词：在上面输入，Enter 加入', 11, SUB)
        for y, ch, ln, term, al in cards:  # 卡片里正确写法一行，听错的写法灰字；改 / 删两个 28 的圆钮对着卡片上下居中
            n.raised(20, y, 360, ch, 24, d=ELEV)
            ty = y + 14 if al else y + (ch - le) / 2
            n.text(40, ty, fit(term, fe, px(240)), 12, TEXT)
            if al: n.text(40, ty + le + 4, '\n'.join(al), 11, SUB)
            cy = y + ch / 2  # 删钮圆心 x=356 = 卡片圆角（24）圆心，改钮在它左边隔 8（和主页右上角一样）
            n.button(303, cy - 15, 30, 30, icon=I_EDIT, size=9, color=ICON_C, on=ln == self.editing, cmd=lambda ln=ln: self.edit(ln))
            n.button(341, cy - 15, 30, 30, icon=I_DEL, size=9, color=RED if ln == self.arm_del else ICON_C, cmd=lambda ln=ln: self.delete(ln))
        self.bar = ScrollBar(n, H, lv)
        n.commit()
        for y, top in ((0, True), (lv - self.FADE_B, False)):  # 上下两条渐隐带，钉在视口里
            self.bar.pin(n.c.create_image(0, 0, anchor='nw', image=self.fade(top)), y)
        n.c.tag_raise(self.bar.thumb); n.c.yview_moveto(oy / H); self.bar.update()

    def focus(self):
        try: self.w.focus_force(); self.ent.focus_set(); self.ent.icursor('end')
        except tk.TclError: pass

    def open_file(self):
        if not WORDS_FILE.exists(): WORDS_FILE.write_text(WORDS_HEAD, encoding='utf-8')
        os.startfile(WORDS_FILE)

    def edit(self, ln):
        """改：这一行放进输入框，「加入」变「保存」；再点一下改钮 = 取消。"""
        self.editing = None if self.editing == ln else ln; self.ent.delete(0, 'end')
        if self.editing: t, alts = parse_word(ln); self.ent.insert(0, f'{t}: {", ".join(alts)}' if alts else t)
        self.build(keep=True); self.focus()

    def cancel(self):
        self.editing = None; self.ent.delete(0, 'end'); self.build(keep=True)

    def delete(self, ln):
        """删：第一下变红，3 秒内再点一下才删（和清空记录一样）。"""
        if self.arm_del != ln:
            if self.arm_job: self.w.after_cancel(self.arm_job)  # 换了一行或重新点：前一个 3 秒计时作废，不然它会提前把这次的红色撤掉
            self.arm_del = ln; self.build(keep=True)
            self.arm_job = self.w.after(3000, self.disarm)
            return
        self.disarm(build=False)
        if self.editing == ln: self.editing = None; self.ent.delete(0, 'end')
        if self.save(ln, None): log(f'常用词删除：{ln.strip()}')

    def disarm(self, build=True):
        if self.arm_job: self.w.after_cancel(self.arm_job)
        self.arm_del = self.arm_job = None
        if build: self.build(keep=True)

    def submit(self):
        p = parse_word(self.ent.get())
        if not p: return
        term, alts = p; old, drop = self.editing, None
        other = next((ln for ln in self.lines() if ln != self.editing and (q := parse_word(ln)) and q[0].lower() == term.lower()), None)
        if other:  # 别的行已有这个正确写法（新加的，或改成了它）：听错的写法并进那一行，写法用那行的；正在改的那行删掉，免得两行重复
            term, have = parse_word(other); alts = have + [a for a in alts if a.lower() not in {b.lower() for b in have}]
            old, drop = other, self.editing
        new = f'{term}: {", ".join(alts)}' if alts else term
        self.editing = None; self.ent.delete(0, 'end')
        if self.save(old, new, drop): log(f'常用词{"修改" if old else "加入"}：{new}')

    def save(self, old, new, drop=None):
        """原文是 old 的那一行换成 new（new=None 删掉；找不到 old 就加在最后），drop 那一行删掉，别的行原样留着。glossary() 按修改时间自己重读。"""
        lines = self.lines()
        if old in lines:
            i = lines.index(old)
            if new is None: del lines[i]
            else: lines[i] = new
        elif new is not None: lines.append(new)
        if drop in lines: lines.remove(drop)
        try: WORDS_FILE.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        except Exception: log('写常用词失败', traceback.format_exc()); return False
        self.app.words_changed(); return True


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
        n = self.n = Neu(self.w, self.W, by + 52 + 20); n.c.pack()
        close_btn(n, self.W, self.w.withdraw)
        n.text(33, 26, 'CHECK', 18, INK, family=DISPLAY)
        n.raised(20, top, 360, 5 * self.ROW + 16, 28, d=ELEV)
        self.rows = []
        for i, name in enumerate(CHECKS):
            y = top + 8 + i * self.ROW
            n.inset(36, y + 10, 32, 32, 16, d=ELEV * INSET_K)
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


SETUP_PS1 = HERE / 'setup.ps1'

def readiness(ol):
    """准备情况两行：[(lv, 一句人话, 按钮字 或 None)]，lv = ok / warn / fail / info。ol：改错服务状态（App.ol_last）。按钮都是运行 setup.ps1。"""
    rows = [{'ok': ('ok', 'Whisper 已载入。', None),
             'loading': ('info', '载入中…', None),
             'nogpu': ('fail', '没有 NVIDIA 显卡：Whisper 识别要用显卡，现在说不了话。', None),
             'nolib': ('fail', '缺显卡运行库（cuBLAS，约 0.5GB）。', '修复'),
             'nomodel': ('fail', 'Whisper 模型（约 1.6GB）还没下载。', '下载')}.get(eng.wh_state, ('fail', '没载入成功，详情见 run.log。', '修复'))]

    if not shutil.which('ollama') and not os.path.exists(os.path.expandvars(r'%LOCALAPPDATA%\Programs\Ollama\ollama.exe')):
        rows.append(('fail', '没装 Ollama：它负责改语病和翻译，没有它只能原样输出。', '安装'))
    elif ol == 'no_model': rows.append(('fail', f'还没下载改错模型 {LLM_MODEL}（约 2.6GB）。', '下载'))
    elif ol == 'down': rows.append(('warn', '改错服务没在运行，Quill 会自动启动它。', None))
    elif ol is None: rows.append(('info', '检查中…', None))
    else: rows.append(('ok', f'Ollama 正常，改错模型 {LLM_MODEL} 在。' + ('' if nvidia_gpu() and LLM_GPU_LAYERS else '现在用 CPU 改错，每句约 5~8 秒。'), None))
    return rows


class Ready:
    """准备情况窗口：缺 Ollama / 模型 / 显卡库时说清楚缺什么，按钮在黑框 PowerShell 里运行 setup.ps1（装完自动重新检查）。"""
    ICON = {**Checks.ICON, 'info': ('–', SUB)}
    NAMES = ['识别模型（Whisper）', '改错服务']
    W, ROW = 400, 88

    def __init__(self, app):
        self.app, self.proc, self.sig, self.btns = app, None, None, []
        self.w = sub_window(app, '准备情况', self.W)
        top = 92; by = top + len(self.NAMES) * self.ROW + 16 + 22
        n = self.n = Neu(self.w, self.W, by + 52 + 20); n.c.pack()
        close_btn(n, self.W, self.w.withdraw)
        n.text(33, 26, 'SETUP', 18, INK, family=DISPLAY)
        n.raised(20, top, 360, len(self.NAMES) * self.ROW + 16, 28, d=ELEV)
        self.rows = []
        for i, name in enumerate(self.NAMES):
            y = top + 8 + i * self.ROW
            n.inset(36, y + 10, 32, 32, 16, d=ELEV * INSET_K)
            self.rows.append((n.text(52, y + 26, '', 13, SUB, anchor='center'), n.text(84, y + 10, name, 12, TEXT), n.text(84, y + 34, '', 11, SUB), y))
        n.button(20, by, 360, 52, r=26, text='重新检查', size=12, cmd=lambda: self.refresh(True))
        n.commit(); self.refresh(); self.w.after(1500, self.tick)

    def build(self): self.refresh(True)

    def tick(self):
        if not self.w.winfo_exists(): return
        if self.w.winfo_viewable(): self.refresh()
        if self.proc and self.proc.poll() is not None: self.proc = None; self.after_setup(); self.refresh(True)
        self.w.after(1500, self.tick)

    def refresh(self, force=False):
        rows = readiness(self.app.ol_last); sig = (tuple(rows), bool(self.proc))
        if sig == self.sig and not force: return
        self.sig = sig
        for b in self.btns: self.n.remove(b)
        self.btns = []
        for (lv, text, act), (ic, _, d, y) in zip(rows, self.rows):
            sym, color = self.ICON[lv]
            self.n.c.itemconfigure(ic, text=sym, fill=color)
            self.n.c.itemconfigure(d, text=fit(text, fnt(11), px(196 if act else 276), 3), fill=RED if lv == 'fail' else SUB)
            if act: self.btns.append(self.n.button(300, y + 20, 64, 30, r=15, text='进行中' if self.proc else act, size=12, color=SUB if self.proc else INK, cmd=self.run_setup))

    def run_setup(self):
        if self.proc or not SETUP_PS1.exists(): return notify('找不到 setup.ps1，请重新下载 Quill。', 'err')
        cmd = f"& '{SETUP_PS1}'; Read-Host 'Finished. Press Enter to close this window'"
        self.proc = subprocess.Popen(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', cmd], cwd=HERE, creationflags=0x10)  # 新黑框
        log('运行 setup.ps1'); self.refresh(True)

    def after_setup(self):
        """setup.ps1 跑完：能补救的当场补上（Whisper 重新载入，Ollama 重新试），不用重启 Quill。"""
        log('setup.ps1 结束，重新检查'); ollama_retry[0] = 0.0
        if eng.wh_state not in ('ok', 'loading', 'nogpu'):
            eng.wh_state, eng.load_error = 'loading', ''; eng.loaded.clear(); threading.Thread(target=eng.load, daemon=True).start()
        post('mode')


def mix(a, b, t):
    ca, cb = (int(a[i:i + 2], 16) for i in (1, 3, 5)), (int(b[i:i + 2], 16) for i in (1, 3, 5))
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(ca, cb))

def hist_blocks():
    try: return [b for b in HIST.read_text(encoding='utf-8').strip().split('\n\n') if b.strip()]
    except Exception: return []

def trim_history():
    """只留最近 hist_days 天的记录（0 = 永久不删）。启动时和改保留期限时跑一次；一年也就几 MB，整个重写不费事。"""
    days = cfg.get('hist_days', 0)
    if not days or not HIST.exists(): return
    cut = f'{datetime.now() - timedelta(days=days):%Y-%m-%d %H:%M}'
    blocks = hist_blocks(); keep = [b for b in blocks if b[:16] >= cut]
    if len(keep) == len(blocks): return
    try: HIST.write_text(''.join(b + '\n\n' for b in keep), encoding='utf-8'); log(f'记录：删掉 {days} 天前的 {len(blocks) - len(keep)} 条')
    except Exception: log('清理旧记录失败', traceback.format_exc())

def load_history(n):
    blocks = hist_blocks()
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
    stop_ollama()  # 自己拉起的改错服务跟着关，别一直占着显存
    os._exit(0)


if __name__ == '__main__':
    if '--asr-worker' in sys.argv: asr_worker(); sys.exit(0)
    kernel32.CreateMutexW(None, False, 'QuillVoiceInput')
    if kernel32.GetLastError() == 183:  # 已经开着：把原来的窗口叫到前面
        h = user32.FindWindowW(None, TITLE)
        if h: user32.ShowWindow(h, 9); user32.SetForegroundWindow(h)
        sys.exit(0)
    (HERE / 'run.log').write_text('', encoding='utf-8')
    log('启动' + ('（自检模式）' if SELFTEST else ''))
    trim_history()
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
    root.geometry(f'+{wa.right - px(App.W) - px(20)}+{wa.top + px(12)}')  # 离右边 20：四个窗口全开时排得下，不用挪
    root.deiconify(); root.update()  # 先把窗口画出来：冷启动时开麦克风要等声卡驱动一两秒，放在前面窗口会白一阵
    threading.Thread(target=lambda: (eng.open(cfg['device']), eng.load()), daemon=True).start()  # 开麦克风、载识别模型都在后台
    threading.Thread(target=fix_worker, daemon=True).start()
    threading.Thread(target=eng.hotkey_loop, daemon=True).start()
    if SELFTEST: root.after(500, app.open_checks)
    for a in sys.argv:  # 截图测试用：--show=settings,history,checks 启动时直接打开这些子窗口
        if a.startswith('--show='):
            for w in a[7:].split(','):  # pop:mic / pop:ai 打开小弹窗
                name, _, arg = w.partition(':'); root.after(800, lambda f=getattr(app, 'open_' + name), arg=arg: f(arg) if arg else f())
        if a == '--scroll-end':  # 设置窗口滚到最底下（看「改错服务」卡片；模拟滚轮 Tk 只认一部分）
            root.after(1500, lambda: (app.settings.n.c.yview_moveto(1), app.settings.sbar.update()))
    root.mainloop()
