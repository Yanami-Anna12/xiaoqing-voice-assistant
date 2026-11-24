# 下载 Vosk 中文小模型（约 42MB）到 models/
param(
    [string]$Model = "vosk-model-small-cn-0.22"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$modelsDir = Join-Path $root "models"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null

$target = Join-Path $modelsDir $Model
if (Test-Path $target) {
    Write-Host "模型已存在：$target"
    exit 0
}

$zip = Join-Path $env:TEMP "$Model.zip"
$url = "https://alphacephei.com/vosk/models/$Model.zip"
Write-Host "下载 $url ..."
curl.exe -L --fail --retry 3 -o $zip $url

Write-Host "解压到 $modelsDir ..."
Expand-Archive -Path $zip -DestinationPath $modelsDir -Force
Remove-Item $zip -Force

if (Test-Path $target) {
    Write-Host "完成：$target"
} else {
    Write-Warning "解压后没有找到 $target，请检查压缩包内容"
    exit 1
}
