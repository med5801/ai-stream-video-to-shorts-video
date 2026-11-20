# One-time setup for Windows. Run setup.bat (double-click) instead of this file.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # much faster downloads on Windows PowerShell 5
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root  = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tools = Join-Path $Root "tools"
$TD    = Join-Path $Tools "TwitchDownloader"
$Venv  = Join-Path $Root ".venv"

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }

Step 1 "Looking for Python 3.10 - 3.13"
$py = $null
foreach ($c in @("py -3.13", "py -3.12", "py -3.11", "py -3.10", "python")) {
    $v = cmd /c "$c --version 2>nul"
    if ($LASTEXITCODE -eq 0 -and "$v" -match "Python 3\.(10|11|12|13)\.") { $py = $c; break }
}
if (-not $py) {
    Write-Host "Python 3.10-3.13 not found. Install it with:  winget install Python.Python.3.13" -ForegroundColor Red
    exit 1
}
Write-Host "  using: $py ($v)"

Step 2 "Creating the virtual environment (.venv)"
if (-not (Test-Path "$Venv\Scripts\python.exe")) { cmd /c "$py -m venv `"$Venv`"" }
$VPy = "$Venv\Scripts\python.exe"
& $VPy -m pip install --upgrade pip --quiet
& $VPy -m pip install -r (Join-Path $Root "requirements.txt")

Step 3 "GPU check (NVIDIA)"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "  NVIDIA GPU found -> installing CUDA libraries for Whisper"
    & $VPy -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
} else {
    Write-Host "  No NVIDIA GPU -> Whisper will run on the CPU (slower, works fine)"
}

Step 4 "TwitchDownloader CLI"
New-Item -ItemType Directory -Force $TD | Out-Null
if (-not (Test-Path "$TD\TwitchDownloaderCLI.exe")) {
    $rel = Invoke-RestMethod "https://api.github.com/repos/lay295/TwitchDownloader/releases/latest" `
           -Headers @{ "User-Agent" = "twitch-shorts-setup" }
    $asset = $rel.assets | Where-Object { $_.name -like "TwitchDownloaderCLI-*-Windows-x64.zip" } | Select-Object -First 1
    $zip = Join-Path $env:TEMP $asset.name
    Write-Host "  downloading $($asset.name)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    Expand-Archive $zip -DestinationPath $TD -Force
} else { Write-Host "  already installed" }

Step 5 "FFmpeg (ffmpeg.exe + ffprobe.exe)"
if (-not ((Test-Path "$TD\ffmpeg.exe") -and (Test-Path "$TD\ffprobe.exe"))) {
    $zip = Join-Path $env:TEMP "ffmpeg-win64-gpl.zip"
    $tmp = Join-Path $env:TEMP "ffmpeg_extract"
    Write-Host "  downloading FFmpeg (~200 MB)"
    Invoke-WebRequest "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip" -OutFile $zip
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp -Force
    Get-ChildItem $tmp -Recurse -Include ffmpeg.exe, ffprobe.exe | Copy-Item -Destination $TD -Force
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
} else { Write-Host "  already installed" }

Step 6 "Folders"
foreach ($d in "vods", "shorts", "assets", "sfx") { New-Item -ItemType Directory -Force (Join-Path $Root $d) | Out-Null }

Step 7 "Brains (AI command-line tools)"
$any = $false
foreach ($b in "agy", "claude", "gemini") {
    if (Get-Command $b -ErrorAction SilentlyContinue) { Write-Host "  [ok] $b"; $any = $true }
    else { Write-Host "  [--] $b not found" -ForegroundColor Yellow }
}
if (-not $any) {
    Write-Host "  Install at least one brain (see docs/USER_GUIDE.md, section 'Brains'):" -ForegroundColor Yellow
    Write-Host "    Gemini (default): install the Antigravity CLI so 'agy' works, then run: agy"
    Write-Host "    Claude Code: irm https://claude.ai/install.ps1 | iex     then run: claude"
    Write-Host "    Gemini CLI : npm install -g @google/gemini-cli            then run: gemini"
}

Write-Host "`nSetup finished. Start with run.bat" -ForegroundColor Green
