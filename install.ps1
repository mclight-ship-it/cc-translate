<#
    CC Translate — one-line installer (Windows).

    Quick start (run in PowerShell):

        irm https://raw.githubusercontent.com/mclight-ship-it/cc-translate/master/install.ps1 | iex

    What it does, in order:
      1. Ensures git, Python 3.12 and Node.js LTS are installed (via winget).
      2. Clones (or updates) the repo into %USERPROFILE%\cc-translate.
      3. Installs / upgrades the Claude Code CLI.
      4. Installs the Python dependencies.
      5. Reminds you to log in to Claude (a one-time browser OAuth that no
         script can do for you).
      6. Launches CC Translate.

    Optional environment overrides (set before running):
      $env:CC_TRANSLATE_DIR    = "D:\apps\cc-translate"   # install location
      $env:CC_TRANSLATE_DRYRUN = "1"                        # print steps, change nothing
#>

$ErrorActionPreference = 'Stop'

$Repo       = 'https://github.com/mclight-ship-it/cc-translate.git'
$InstallDir = if ($env:CC_TRANSLATE_DIR) { $env:CC_TRANSLATE_DIR } else { Join-Path $HOME 'cc-translate' }
$DryRun     = [bool]$env:CC_TRANSLATE_DRYRUN

# ---------- small helpers ----------
function Info($m) { Write-Host $m -ForegroundColor Gray }
function Good($m) { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Step($n, $m) { Write-Host "`n[$n/6] $m" -ForegroundColor Cyan }

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Update-SessionPath {
    # winget-installed tools land in PATH via the registry, but the current
    # process doesn't see them until we re-read it. Also fold in npm's global
    # bin (where the Claude CLI installs its shim).
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts   = @($machine, $user) | Where-Object { $_ }
    $npmBin  = Join-Path $env:APPDATA 'npm'
    if (Test-Path $npmBin) { $parts += $npmBin }
    $env:Path = ($parts -join ';')
}

function Get-NpmCmd {
    # Invoke npm via its .cmd shim, never the bare `npm` name: PowerShell would
    # resolve that to npm.ps1, which a default execution policy blocks with
    # "running scripts is disabled on this system". A .cmd batch file is run by
    # cmd.exe and isn't subject to the PowerShell execution policy.
    $c = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $guess = Join-Path $env:ProgramFiles 'nodejs\npm.cmd'
    if (Test-Path $guess) { return $guess }
    return 'npm.cmd'
}

function Ensure-Winget {
    if (-not (Have winget)) {
        throw "找不到 winget（「应用安装程序」）。请从 Microsoft Store 更新「应用安装程序」后重试，或手动安装 Git / Python 3.12 / Node.js LTS 再重跑本脚本。"
    }
}

function Ensure-Tool($cmd, $wingetId, $name) {
    if (Have $cmd) { Good "  ✓ $name 已安装"; return }
    if ($DryRun) { Info "  [dry-run] 将用 winget 安装 $name ($wingetId)"; return }
    Ensure-Winget
    Info "  安装 $name（这可能要一两分钟）…"
    winget install --id $wingetId -e --accept-source-agreements --accept-package-agreements --silent | Out-Null
    Update-SessionPath
    if (Have $cmd) { Good "  ✓ $name 安装完成" }
    else { Warn "  ⚠ $name 已安装，但当前终端还找不到它——通常重开一个 PowerShell 即可。脚本会继续尝试。" }
}

function Invoke-Or-DryRun($desc, [scriptblock]$action) {
    if ($DryRun) { Info "  [dry-run] $desc"; return }
    & $action
}

# ---------- banner ----------
Write-Host ""
Write-Host "  CC Translate 安装程序" -ForegroundColor White
Write-Host "  安装目录: $InstallDir" -ForegroundColor DarkGray
if ($DryRun) { Warn "  （dry-run 模式：只显示步骤，不做任何改动）" }

# ---------- 1. base tooling ----------
Step 1 "检查基础环境（git / Python / Node.js）"
Ensure-Tool git    'Git.Git'            'Git'
Ensure-Tool python 'Python.Python.3.12' 'Python 3.12'
Ensure-Tool node   'OpenJS.NodeJS.LTS'  'Node.js LTS'

# ---------- 2. clone / update ----------
Step 2 "获取项目代码"
if (Test-Path (Join-Path $InstallDir '.git')) {
    Info "  已存在，拉取最新代码…"
    Invoke-Or-DryRun "git -C $InstallDir pull --ff-only" { git -C $InstallDir pull --ff-only }
} else {
    Invoke-Or-DryRun "git clone $Repo $InstallDir" { git clone $Repo $InstallDir }
}
if (-not $DryRun) { Set-Location $InstallDir }
Good "  ✓ 代码就绪"

# ---------- 3. Claude Code CLI ----------
Step 3 "安装 / 升级 Claude Code CLI"
Warn "  （必须升到最新版：旧版 CLI 的参数不兼容，会导致翻译报错）"
$npm = Get-NpmCmd
Invoke-Or-DryRun "$npm install -g @anthropic-ai/claude-code@latest" {
    & $npm install -g '@anthropic-ai/claude-code@latest'
}
Update-SessionPath
if ((Have claude) -or $DryRun) { Good "  ✓ Claude CLI 就绪" }
else { Warn "  ⚠ 装完仍找不到 claude——请确认 npm 全局目录（%APPDATA%\npm）在 PATH 中。" }

# ---------- 4. python deps ----------
Step 4 "安装 Python 依赖"
Invoke-Or-DryRun "python -m pip install --upgrade pip pynput pyperclip pystray Pillow Pygments" {
    python -m pip install --upgrade pip pynput pyperclip pystray Pillow Pygments
}
Good "  ✓ 依赖就绪"

# ---------- 5. login reminder ----------
function Test-ClaudeReady {
    # macOS/Linux keep an oauth token file; on Windows the token lives in
    # Credential Manager and ~/.claude.json records the signed-in account.
    if (Test-Path (Join-Path $HOME '.claude\.credentials.json')) { return $true }
    $j = Join-Path $HOME '.claude.json'
    if (Test-Path $j) {
        try {
            $c = Get-Content $j -Raw | ConvertFrom-Json
            if ($c.oauthAccount -or $c.hasCompletedOnboarding) { return $true }
        } catch {}
    }
    return $false
}

Step 5 "登录 Claude（唯一需要你手动完成的一步）"
if (Test-ClaudeReady) {
    Good "  ✓ 检测到已登录的 Claude 账号，跳过。"
} else {
    Warn "  还需登录一次（用你现有的 Claude 订阅，走浏览器授权，不额外收费）："
    Info  "      1) 打开一个新的终端窗口"
    Info  "      2) 运行:  claude"
    Info  "      3) 按提示在浏览器完成登录，成功后按 Ctrl+C 退出交互模式"
    Info  "  未登录时 CC Translate 会弹出「未登录」提示——登录后即可正常翻译。"
}

# ---------- 6. launch ----------
Step 6 "启动 CC Translate"
Invoke-Or-DryRun "Start-Process pythonw translator.pyw（工作目录 $InstallDir）" {
    $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = 'pythonw' }
    Start-Process $pyw -ArgumentList 'translator.pyw' -WorkingDirectory $InstallDir
}

Write-Host ""
Good  "完成！托盘里会出现 「CC」 图标。"
Info  "用法：选中任意文字，快速双击 Ctrl+C，鼠标旁即弹出译文。"
Info  "开机自启：右键托盘图标 → 设置 → 勾选「开机自动启动」。"
Write-Host ""
