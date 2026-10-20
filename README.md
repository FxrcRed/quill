<div align="center">

<img src="icon_dial.png" width="88" alt="Quill icon">

# Quill

**Offline voice typing for Windows.**
Hold a key, speak messy English *or Chinese*, let go — clean, natural English is typed at your cursor.
Stutters, "um"s, restarts and self-corrections are removed. Nothing leaves your computer.

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

- **Push-to-talk anywhere** — hold Right Alt (configurable) in any app; the result is pasted at the cursor and your clipboard is restored.
- **Cleans up speech, not just grammar** — removes fillers, stutters, false starts and self-corrections ("no, I mean…"), fixes tense, articles and plurals.
- **Speak Chinese, get English** — one tap on **EN / 中** on the home screen. Mixed Chinese-English speech works too.
- **Or speak Chinese, get clean Chinese** — Settings → *When speaking Chinese* → 出中文. Fillers (嗯, 呃), stutters and repeats are removed instantly by rules; only sentences with a self-correction (不对 / 我是说 …) go to the LLM, so most sentences appear in about half a second.
- **Fast** — while you talk, finished sentences are already being corrected; in English the text usually appears ~0.5 s after you let go (NVIDIA GPU).
- **Two output modes** — *Direct* (type it right away) or *Pick* (choose between a natural and a casual version).
- **See what changed** — a small overlay at the bottom of the screen shows live transcription, the words that were fixed, and a Chinese translation.
- **100% local** — speech recognition and the language model run on your machine. History is a plain text file that never leaves it.
- Soft-UI (neumorphism) interface, floating mini ball, history with one-click copy, built-in self-test.

<img src="docs/main.png" width="300" alt="Quill main window">

## How it works

```
hold key → microphone (16 kHz)
        → speech recognition    English: NVIDIA Parakeet TDT 0.6B v3 (ONNX, CPU)
                                Chinese: SenseVoice Small (sherpa-onnx, CPU)
        → cleanup / translation  Qwen3 8B via Ollama (GPU if available, falls back to CPU)
        → paste at cursor
```

Audio is split at pauses that end a sentence, so earlier sentences are corrected while you are still talking.
Recognition runs in a separate process so the UI stays responsive.

## Install

**Requirements:** Windows 10/11 · 16 GB RAM · ~8 GB disk. An NVIDIA GPU with 6 GB+ VRAM is recommended
(CPU-only works, but each sentence takes about 5–8 s to fix).

```powershell
git clone https://github.com/FxrcRed/quill.git
cd quill
powershell -ExecutionPolicy Bypass -File setup.ps1          # add -Mirror in mainland China
```

`setup.ps1` installs [uv](https://github.com/astral-sh/uv) and Python 3.12 (no system Python needed), the Python packages,
the two speech models (~0.9 GB), [Ollama](https://ollama.com) and `qwen3:8b` (~5 GB), and creates a desktop shortcut.
Re-running it is safe; it skips what you already have.

Then start **Quill** from the desktop, wait for **READY**, hold **Right Alt** and speak.

## Usage tips

- **EN / 中** (top right of the status card) chooses the language you speak. Output is always English.
- Settings: microphone, hotkey (click *Record* and press any key), output mode, and a self-test that checks every part.
- The shrink button (two arrows, top right) turns Quill to a floating ball at the edge of the screen; click it to come back.
- History keeps the last 20 sentences: the original speech, the English, and the Chinese meaning.

## Development

- All of the app is one file: [`app.pyw`](app.pyw) (tkinter + Pillow for the UI).
- `e2e_test.py [zh]` — end-to-end test without a microphone (feeds a wav at real-time speed).
- `fix_test.py [pick] [zh]` — compare correction output on real-world phrases after changing prompts.
- `make_demo.py` — regenerates the GIF and screenshot above.
- The project was built together with Claude Code.

## Credits

- [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) by NVIDIA (CC-BY-4.0), ONNX export by
  [istupakov](https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx), run with [onnx-asr](https://github.com/istupakov/onnx-asr)
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) by FunAudioLLM, run with [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)
- [Qwen3](https://github.com/QwenLM/Qwen3) (Apache-2.0) served by [Ollama](https://ollama.com)
- Fonts: [Noto Sans SC](https://github.com/notofonts/noto-cjk) and [Michroma](https://github.com/googlefonts/Michroma-font) (SIL OFL 1.1)

Models are downloaded at install time and keep their own licenses. Quill itself is [MIT](LICENSE).
