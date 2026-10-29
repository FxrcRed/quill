<div align="center">

<img src="icon_dial.png" width="88" alt="Quill icon">

# Quill

**Offline voice typing for Windows.**
Hold a key, speak messy English *or Chinese*, let go — clean, natural English is typed at your cursor.
Nothing leaves your computer.

[中文说明](README.zh.md) · MIT · Windows 10/11

<img src="docs/demo.gif" width="760" alt="Quill demo: a stuttered English sentence and a Chinese sentence become clean English">

</div>

## Why

I'm not a native English speaker. When I talk, I hesitate, repeat myself and correct mid-sentence —
and I often know exactly what I mean in Chinese but not in English. Dictation tools either type
all of that verbatim, or send my voice to the cloud. Quill does the cleanup locally:

| You say | Quill types |
|---|---|
| *"I, I want to, um, I want to change the the color, no, the size of the button."* | I want to change the size of the button. |
| *"Yesterday I go to the store and buy some apple."* | Yesterday I went to the store and bought some apples. |
| *"嗯，我想我想把这个按钮的颜色不对，是大小改一下。"* (Chinese) | I want to change the size of this button. |
| *"帮我把这个PR merge一下，然后呃，通知一下team。"* (mixed) | Please merge this PR and notify the team. |

## Features

- **Push-to-talk anywhere** — hold Right Alt (configurable) in any app; the text is pasted at the cursor and your clipboard is restored.
- **Cleans up speech, not just grammar** — removes fillers, stutters, false starts and self-corrections ("no, I mean…"); fixes tense, articles and plurals.
- **Speak Chinese, get English** — tap **EN / 中** on the home screen; mixed Chinese-English works too. Or get clean Chinese instead (Settings → *When speaking Chinese*).
- **Fast** — English appears about 1 s after you let go, clean Chinese in about half a second.
- **See what changed** — an overlay at the bottom of the screen shows the live text, the words that were fixed and a Chinese translation.
- **Learns your words** — names, projects, tools. Write `Claude: cloud` and Quill decides from context whether "cloud" meant Claude. Once a day it suggests words it probably misheard.

<p align="center">
<img src="docs/main.png" height="300" alt="Quill main window">
&nbsp;
<img src="docs/words.png" height="300" alt="Word list: cloud is checked in context and typed as Claude">
</p>

## How it works

```
hold key → microphone (16 kHz)
         → recognition   Whisper large-v3-turbo on the GPU, primed with your word list:
                         about once a second for the live overlay, then the whole recording on release
         → word list     Chinese words heard as same-sounding characters are swapped back by pinyin
         → cleanup       Qwen3 4B via Ollama (8B in Settings; falls back to CPU if the GPU is busy)
         → paste at cursor
```

Reading the whole recording at once is far more accurate than recognizing short pieces between pauses.
Recognition runs in a separate process so the UI stays responsive.

## Install

**Requirements:** Windows 10/11 only (no macOS / Linux) · 16 GB RAM · ~8 GB disk. An NVIDIA GPU with 6 GB+ VRAM is required (Whisper runs on it).

```powershell
git clone https://github.com/FxrcRed/quill.git
cd quill
powershell -ExecutionPolicy Bypass -File setup.ps1          # add -Mirror in mainland China
```

`setup.ps1` installs [uv](https://github.com/astral-sh/uv) and Python 3.12 (no system Python needed), the packages,
the Whisper model (~1.6 GB), [Ollama](https://ollama.com) with `qwen3:4b` (~2.6 GB), and a desktop shortcut.
It is safe to re-run. If something goes missing later, **Settings → Setup** in Quill says what and runs it for you.
For better cleanup, `ollama pull qwen3:8b` (~5 GB) and pick it in Settings.

Then start **Quill** from the desktop, wait for **READY**, hold **Right Alt** and speak.

## Usage tips

- **EN / 中** (top right of the status card) chooses the language you speak. Output is English, unless you set *When speaking Chinese* → 出中文 in Settings.
- Below the status card are **HISTORY** and **WORDS** buttons and a settings gear. Settings: microphone, hotkey (click *Record* and press any key), output mode; the three round icons at the top right are Setup, Self-test (checks every part) and Close.
- The shrink button (two arrows, top right) turns Quill to a floating ball at the edge of the screen; click it to come back.
- **HISTORY** keeps the last 20 sentences (the icon in its title row opens the file): the original speech, the English, and the Chinese meaning. You can turn saving off, keep only the last 7 / 30 days, or clear it.
- **WORDS**: add, edit or delete entries; the icon in its title row opens `words.txt` in a text editor. One entry per line: `Correct spelling: misheard, another misheard` (the part after the colon is optional).
- Ollama is started with Quill and stopped when Quill quits; the model leaves GPU memory after it sits idle (2 / 10 / 30 minutes, or never — in Settings).

## Development

- All of the app is one file: [`app.pyw`](app.pyw) (tkinter + Pillow for the UI).
- `e2e_test.py [zh]` — end-to-end test without a microphone (feeds a wav at real-time speed).
- `fix_test.py [zh | zhzh]` — compare correction output on real-world phrases after changing prompts.
- `term_test.py` — checks how well the model tells a misheard name (cloud → Claude) from the ordinary word.
- `eval_run.py [en | zh | zhzh | all]` — runs the correction test set with a fixed seed and diffs it against the last run.
- `cer_eval.py [auto]` — Chinese character error rate on your own recordings in `eval_zh\` (not in git).
- `harvest.py` — collects test samples by pairing each dictation with the edited message you sent in Claude Code (needs `"save_audio": true` in `settings.json`).
- `make_words_demo.py` — regenerates the word-list image.
- `make_demo.py` — regenerates the GIF and screenshot above.
- The project was built together with Claude Code.

## Credits

- [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) by OpenAI (MIT), run with [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [Qwen3](https://github.com/QwenLM/Qwen3) (Apache-2.0) served by [Ollama](https://ollama.com)
- [pypinyin](https://github.com/mozillazg/python-pinyin) (MIT); matching words by sound is an idea from [CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)
- Fonts: [Noto Sans SC](https://github.com/notofonts/noto-cjk) and [Michroma](https://github.com/googlefonts/Michroma-font) (SIL OFL 1.1)

Models are downloaded at install time and keep their own licenses. Quill itself is [MIT](LICENSE).
