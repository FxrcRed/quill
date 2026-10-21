# 测：易混词（cloud 是不是 Claude）让模型看上下文判断，准不准、多慢。不用关 Quill。
# 用法：.venv\Scripts\python.exe term_test.py
# 2026-10-21：直接问「是不是 Claude」全答否（9/18）；二选一 14/18；挖空填词 15/18；挖空 + 「拿不准选 Claude」17/18、新句子 10/10（现在这版）
import json, re, time, urllib.request

URL, MODEL = 'http://127.0.0.1:11434/api/chat', 'qwen3:8b'
TERM, HEARD = 'Claude', 'cloud'

# (句子, 应该是 Claude 吗)
CASES = [
    ('I have round out my cloud token. Next time I can use is two hours later.', True),
    ('thanks to the cloud and the cursor, these kinds of tools enhanced my ability', True),
    ('我有个疑问，就是现在我做的项目，你可以帮我推送到cloud里面的archK吗？', True),
    ('你可以直接登到我的本地的cloud吗？', True),
    ('I asked cloud to fix the bug and it rewrote the whole file', True),
    ('让cloud帮我写一个脚本', True),
    ('cloud code is really good at refactoring', True),
    ('我今天用cloud改了一下界面，它改得挺好的', True),
    ('can you tell cloud to stop asking me so many questions', True),
    ('I upload all my photos to the cloud so I do not lose them', False),
    ('there is a big dark cloud over the mountain', False),
    ('our company is moving the database to the cloud next month', False),
    ('这个文件存在cloud上面，换电脑也能打开', False),
    ('cloud storage is getting cheaper every year', False),
    ('我想把服务器部署到cloud上，用阿里云还是腾讯云', False),
    ('那如果我就是想说cloud怎么办？', False),
    ('the sky is full of cloud today', False),
    ('iCloud 的空间满了，要不要买 cloud 存储', False),
    # 下面 10 句是调好提示词后才加的，没对着它们调过
    ('cloud said my code has a memory leak', True),
    ('我把这个问题发给cloud了，等它回复', True),
    ('is cloud better than ChatGPT at writing', True),
    ('cloud的额度又用完了', True),
    ('let me check what cloud wrote in the readme', True),
    ('we pay a lot for cloud servers every month', False),
    ('我的照片都同步到cloud了', False),
    ('it looks like rain, the cloud is getting darker', False),
    ('AWS is the biggest cloud provider', False),
    ('把备份放到cloud上比较安全', False),
]

# 把听错的词挖掉让模型填空：直接问「这个 cloud 是不是 Claude」它会认死字面全答否。和 app.pyw 的 TERM_PROMPT 保持一样
SYS = (f'One word in this sentence was replaced by ___. It is either "{TERM}" (a name: a tool, app, product or person the speaker uses, talks to or works with every day) or the ordinary word "{HEARD}". '
       f'The speaker talks about {TERM} far more often than about {HEARD}. '
       f'Choose "{HEARD}" only when the sentence clearly uses its ordinary meaning; if unsure, choose "{TERM}". '
       f'Answer only JSON {{"word": "{TERM}"}} or {{"word": "{HEARD}"}}.')

def ask(s):
    body = {'model': MODEL, 'stream': False, 'think': False, 'format': 'json', 'keep_alive': '2m',
            'options': {'temperature': 0, 'num_ctx': 1024},
            'messages': [{'role': 'system', 'content': SYS}, {'role': 'user', 'content': re.sub(r'(?<![A-Za-z])%s(?![A-Za-z])' % HEARD, '___', s, flags=re.I)}]}
    r = urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=60)
    return json.loads(json.loads(r.read())['message']['content']).get('word', '').lower() == TERM.lower()

ask('hello cloud')  # 先载入模型，不计时
ok, ts = 0, []
for s, want in CASES:
    t = time.time(); got = ask(s); ts.append(time.time() - t)
    ok += got == want
    print(('对 ' if got == want else '错 ') + f'{ts[-1]:.2f}s  想要 {"Claude" if want else "cloud "}  {s[:50]}')
print(f'\n{ok}/{len(CASES)} 对，平均 {sum(ts)/len(ts):.2f} 秒')
