# Optional local speech recognizer. Run from any directory, with -Mirror if needed.
param([switch]$Mirror)
$ErrorActionPreference = 'Stop'
$dir = Join-Path (Split-Path $PSScriptRoot -Parent) 'models\r2t2'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$lock = [IO.File]::Open((Join-Path $dir '.install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
function Fetch($url, $name, $hash) {
    $file = Join-Path $dir $name
    if ((Test-Path -LiteralPath $file) -and (Get-FileHash -LiteralPath $file).Hash -eq $hash) { return $file }
    Write-Host "Downloading $name"
    & curl.exe -fL --retry 2 --output "$file.part" $url
    if ($LASTEXITCODE -ne 0 -or (Get-FileHash -LiteralPath "$file.part").Hash -ne $hash) {
        throw "Failed to download or verify $name. Run this installer again."
    }
    Move-Item -LiteralPath "$file.part" -Destination $file -Force
    return $file
}
try {
    $hf = if ($Mirror) { 'https://hf-mirror.com' } else { 'https://huggingface.co' }
    $weights = "$hf/netease-youdao/Confucius4-R2T2-GGUF/resolve/main"
    Fetch "$weights/Confucius4-R2T2-Q8_0.gguf" 'model.gguf' '151097e43957a19984ea7de66e8144ce69b95039eb31c93da4f58db367e455c3' | Out-Null
    Fetch "$weights/mmproj-Confucius4-R2T2-f16.gguf" 'mmproj.gguf' '0057b28b8814e431a28e3d1002343eef2d1676bbf8b0625351ae4550ac4e595d' | Out-Null
    Fetch "$hf/netease-youdao/Confucius4-R2T2/resolve/main/tokenizer.json" 'tokenizer.json' '0499602714160467f2d68b910651d6216020689f1e016be87a2d0019ee3baeab' | Out-Null
    Fetch 'https://raw.githubusercontent.com/netease-youdao/Confucius4-R2T2/master/MODEL_LICENSE_zh' 'MODEL_LICENSE_zh' '18b438311ebb842c15a7c91d9dbb59034efdaf6fff0d105031ef7baf5eeed275' | Out-Null
    $release = 'https://github.com/ggml-org/llama.cpp/releases/download/b10950'
    $runtime = Join-Path $dir 'llama'
    $required = @('llama-server.exe','llama-server-impl.dll','llama-common.dll','llama.dll','mtmd.dll',
                  'ggml.dll','ggml-base.dll','ggml-cpu-x64.dll','ggml-cuda.dll','libomp.dll',
                  'cudart64_12.dll','cublas64_12.dll','cublasLt64_12.dll')
    $marker = Join-Path $runtime '.b10950-ready'
    $missing = @($required | Where-Object { -not (Test-Path (Join-Path $runtime $_)) })
    if ($missing.Count -or -not (Test-Path $marker)) {
        if (Test-Path $marker) { Remove-Item -LiteralPath $marker }
        foreach ($asset in @(
            @('llama-b10950-bin-win-cuda-12.4-x64.zip', 'b184393e8dc54fdcca4f4de5059b02d143d2dc813e7cd5d900d1b494d127004c'),
            @('cudart-llama-bin-win-cuda-12.4-x64.zip', '8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6'))) {
            $archive = Fetch "$release/$($asset[0])" $asset[0] $asset[1]
            Expand-Archive -LiteralPath $archive -DestinationPath $runtime -Force
            Remove-Item -LiteralPath $archive
        }
        foreach ($name in $required) {
            if (-not (Test-Path (Join-Path $runtime $name))) { throw "Missing runtime file: $name" }
        }
        New-Item -ItemType File -Force (Join-Path $runtime '.b10950-ready') | Out-Null
    }
    Write-Host 'Youdao R2T2 is ready.'
} finally { $lock.Dispose() }
