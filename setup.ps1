# Quill one-click setup (Windows 10/11).
# Right-click -> "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File setup.ps1
# Options:
#   -Mirror        download models from hf-mirror.com (faster in mainland China)
#   -NoOllama      skip installing Ollama / pulling qwen3:4b
#   -NoShortcut    do not create the desktop shortcut
param([switch]$Mirror, [switch]$NoOllama, [switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }

# 1. uv (installs Python 3.12 for us, no system Python needed)
Step 'Checking uv'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv (https://astral.sh/uv) ...'
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
uv --version

# 2. Python environment + packages
Step 'Creating .venv (Python 3.12) and installing packages'
if (-not (Test-Path '.venv\Scripts\python.exe')) { uv venv .venv --python 3.12 }
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Package install failed' }

# NVIDIA GPU? Then Whisper (final English text) runs on it and needs cuBLAS 12 (nvidia-cublas-cu12, ~0.5 GB download) in the venv.
# No NVIDIA GPU: skip both, Quill falls back to Parakeet only (see README).
$hasGpu = $false
try { $null = & nvidia-smi --query-gpu=name --format=csv,noheader 2>$null; $hasGpu = ($LASTEXITCODE -eq 0) } catch { }
if ($hasGpu) {
    Step 'NVIDIA GPU found: installing cuBLAS for Whisper'
    uv pip install --python .venv\Scripts\python.exe 'nvidia-cublas-cu12<13'
    if ($LASTEXITCODE -ne 0) { Write-Host 'cuBLAS install failed; Quill will use Parakeet only until you run this script again.' -ForegroundColor Yellow }
} else { Write-Host 'No NVIDIA GPU found: skipping Whisper (Quill will use Parakeet only).' -ForegroundColor Yellow }

# sherpa-onnx-core puts its DLLs in Scripts\; they must sit next to the .pyd, otherwise
# Windows loads its own old System32\onnxruntime.dll and the Chinese recognizer crashes.
$lib = Join-Path $here '.venv\Lib\site-packages\sherpa_onnx\lib'
Get-ChildItem -Path (Join-Path $here '.venv') -Recurse -Include 'onnxruntime.dll', 'sherpa-onnx-c*-api.dll' -ErrorAction SilentlyContinue |
    Where-Object { $_.DirectoryName -ne $lib } | ForEach-Object { Copy-Item $_.FullName $lib -Force -ErrorAction SilentlyContinue }

# 3. Speech models (Parakeet for live English text ~650 MB, Whisper large-v3-turbo for the final English text ~1.6 GB,
#    SenseVoice for Chinese ~240 MB). Whisper needs an NVIDIA GPU; without one it is skipped and Quill just uses Parakeet.
Step 'Downloading speech models (skips files you already have)'
$env:HF_HUB_DISABLE_XET = '1'
$env:QUILL_SKIP_WHISPER = $(if ($hasGpu) { '' } else { '1' })
if ($Mirror) { $env:HF_ENDPOINT = 'https://hf-mirror.com' }
$py = @'
import os
from pathlib import Path
from huggingface_hub import hf_hub_download
jobs = {
    'models/parakeet': ('istupakov/parakeet-tdt-0.6b-v3-onnx',
                        ['config.json', 'vocab.txt', 'encoder-model.int8.onnx', 'decoder_joint-model.int8.onnx']),
    'models/sensevoice': ('csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17', ['tokens.txt', 'model.int8.onnx']),
    'models/whisper': ('deepdml/faster-whisper-large-v3-turbo-ct2',
                       ['config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json', 'model.bin']),
}
if os.environ.get('QUILL_SKIP_WHISPER'): jobs.pop('models/whisper')
for d, (repo, files) in jobs.items():
    for f in files:
        if (Path(d) / f).exists(): print('  have', d + '/' + f); continue
        print('  get ', d + '/' + f, flush=True); hf_hub_download(repo, f, local_dir=d)
'@
$py | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { throw 'Model download failed (try: setup.ps1 -Mirror)' }

# 4. Ollama + qwen3:4b (grammar fixing / translation; qwen3:8b is optional, pick it in Settings)
if (-not $NoOllama) {
    Step 'Checking Ollama and qwen3:4b'
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
            $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
        } else { Write-Host 'Please install Ollama from https://ollama.com/download, then run this script again.' -ForegroundColor Yellow }
    }
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        # Already have qwen3:4b (e.g. built from a GGUF)? Keep it: pulling would re-download it.
        if ((ollama list | Out-String) -match '(?m)^qwen3:4b\s') { Write-Host '  have qwen3:4b' }
        else {
            Write-Host '  pulling qwen3:4b (~2.6 GB). For the higher-quality option run "ollama pull qwen3:8b" (~5 GB) and pick it in Settings.'
            ollama pull qwen3:4b
        }
    }
}

# 5. Test recording for the self-test (Settings -> Self-test)
if (-not (Test-Path 'test.wav')) {
    Step 'Making test.wav with Windows text-to-speech'
    Add-Type -AssemblyName System.Speech
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $v = $s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo } | Where-Object { $_.Culture.Name -like 'en-*' } | Select-Object -First 1
    if ($v) {
        $s.SelectVoice($v.Name)
        $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
        $s.SetOutputToWaveFile((Join-Path $here 'test.wav'), $fmt)
        $s.Speak("Yesterday I go to the store and buy some apple, but they doesn't have the one I want.")
    }
    $s.Dispose()
}

# 6. Desktop shortcut
if (-not $NoShortcut) {
    Step 'Creating desktop shortcut'
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Quill.lnk'))
    $sc.TargetPath = Join-Path $here '.venv\Scripts\pythonw.exe'
    $sc.Arguments = '"' + (Join-Path $here 'app.pyw') + '"'
    $sc.WorkingDirectory = $here
    $sc.IconLocation = Join-Path $here 'icon_dial.ico'
    $sc.Save()
}

Step 'Done. Start Quill from the desktop shortcut (or start.vbs). Hold Right Alt and speak (change the key in Settings).'
