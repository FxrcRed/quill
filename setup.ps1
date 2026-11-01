# Quill one-click setup (Windows 10/11, NVIDIA GPU required).
# Right-click -> "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File setup.ps1
# Options:
#   -Mirror        download models from hf-mirror.com (faster in mainland China)
#   -NoOllama      skip installing Ollama / pulling qwen3:4b
#   -NoShortcut    do not create the desktop shortcut
param([switch]$Mirror, [switch]$NoOllama, [switch]$NoShortcut)
# Native commands (uv, winget, ollama) write progress to stderr; 'Stop' would turn that into red PowerShell errors.
# Every native call is checked through $LASTEXITCODE / Get-Command and ends in Fail with a next step.
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
function Step($t) { Write-Host "`n==> $t" -ForegroundColor Cyan }
function Fail($what, $next) {
    Write-Host "`n!!! SETUP FAILED: $what" -ForegroundColor Red
    Write-Host "    Next: $next" -ForegroundColor Red
    exit 1
}

# 0. NVIDIA GPU + driver. Whisper runs on the GPU (there is no CPU mode), so check before downloading anything.
Step 'Checking the NVIDIA GPU'
$gpu = ''
try { $gpu = (& nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>$null | Select-Object -First 1); if ($LASTEXITCODE -ne 0) { $gpu = '' } } catch { $gpu = '' }
if (-not $gpu) {
    Fail 'no NVIDIA GPU / driver found (nvidia-smi did not run). Quill cannot recognize speech without one.' 'Install an NVIDIA driver (527.41 or newer) from https://www.nvidia.com/drivers, reboot, check that "nvidia-smi" works in PowerShell, then run this script again. Computers without an NVIDIA GPU are not supported.'
}
Write-Host "  found: $gpu"

# 1. uv (installs Python 3.12 for us, no system Python needed)
Step 'Checking uv'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv (https://astral.sh/uv) ...'
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail 'uv could not be installed (no internet, or the download was blocked).' 'Check your connection (or install uv by hand: https://docs.astral.sh/uv/getting-started/installation/), open a new PowerShell window and run this script again.'
}
uv --version

# 2. Python environment + packages
Step 'Creating .venv (Python 3.12) and installing packages'
if (-not (Test-Path '.venv\Scripts\python.exe')) {
    uv venv .venv --python 3.12
    if ($LASTEXITCODE -ne 0) { Fail 'could not create the Python 3.12 environment (.venv).' 'Check your internet connection, delete the .venv folder if it exists, then run this script again.' }
}
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'the Python packages did not install.' 'Check your internet connection and run this script again (it resumes where it stopped).' }

# Whisper needs cuBLAS 12 (nvidia-cublas-cu12, ~0.5 GB download) in the venv.
Step 'Installing cuBLAS for Whisper'
uv pip install --python .venv\Scripts\python.exe 'nvidia-cublas-cu12<13'
if ($LASTEXITCODE -ne 0) { Fail 'cuBLAS (nvidia-cublas-cu12) did not install.' 'Check your internet connection and run this script again.' }

# 3. Speech model: Whisper large-v3-turbo (~1.6 GB, English and Chinese).
Step 'Downloading the speech model (skips files you already have)'
$env:HF_HUB_DISABLE_XET = '1'
if ($Mirror) { $env:HF_ENDPOINT = 'https://hf-mirror.com' }
$py = @'
from pathlib import Path
from huggingface_hub import hf_hub_download
jobs = {
    'models/whisper': ('deepdml/faster-whisper-large-v3-turbo-ct2',
                       ['config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json', 'model.bin']),
}
for d, (repo, files) in jobs.items():
    for f in files:
        if (Path(d) / f).exists(): print('  have', d + '/' + f); continue
        print('  get ', d + '/' + f, flush=True); hf_hub_download(repo, f, local_dir=d)
'@
$py | .venv\Scripts\python.exe -
if ($LASTEXITCODE -ne 0) { Fail 'the Whisper model download failed.' 'Run this script again (finished files are kept). In mainland China add -Mirror: powershell -ExecutionPolicy Bypass -File setup.ps1 -Mirror' }

# 4. Ollama + qwen3:4b (grammar fixing / translation; qwen3:8b is optional, pick it in Settings)
if (-not $NoOllama) {
    Step 'Checking Ollama and qwen3:4b'
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fail 'Ollama is not installed and winget is missing.' 'Install Ollama from https://ollama.com/download (or get winget by installing "App Installer" from the Microsoft Store), open a new PowerShell window and run this script again.'
        }
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        $env:Path = "$env:LOCALAPPDATA\Programs\Ollama;$env:Path"
        if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
            Fail 'Ollama was installed but is not on PATH yet (or the winget install failed).' 'Close this window, open a new PowerShell window and run this script again. If it repeats, install Ollama from https://ollama.com/download first.'
        }
    }
    # Already have qwen3:4b (e.g. built from a GGUF)? Keep it: pulling would re-download it.
    if ((ollama list | Out-String) -match '(?m)^qwen3:4b\s') { Write-Host '  have qwen3:4b' }
    else {
        Write-Host '  pulling qwen3:4b (~2.6 GB). For the higher-quality option run "ollama pull qwen3:8b" (~5 GB) and pick it in Settings.'
        ollama pull qwen3:4b
        if ($LASTEXITCODE -ne 0) { Fail 'could not pull qwen3:4b from Ollama.' 'Check your internet connection. If Ollama was just installed, start it once (search "Ollama" in the Start menu), then run this script again.' }
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

Step 'Done. Start Quill from the desktop shortcut (or start.vbs), wait for READY (the first start loads the models), then hold Right Ctrl and speak (change the key in the settings).'
