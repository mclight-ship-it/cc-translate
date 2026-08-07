<#
    CC Translate — one-line installer (Windows).

    Quick start (run in PowerShell):

        irm https://raw.githubusercontent.com/mclight-ship-it/cc-translate/master/install.ps1 | iex

    What it does, in order:
      1. Ensures git, Python 3.12 and Node.js LTS are installed (via winget).
      2. Clones (or updates) the repo into %USERPROFILE%\cc-translate.
      3. Installs / upgrades the Claude Code and compatible Codex CLIs.
      4. Installs the Python dependencies.
      5. Reminds you to log in to Claude and Codex (one-time browser OAuth
         flows that no script can do for you).
      6. Launches CC Translate.

    Optional environment overrides (set before running):
      $env:CC_TRANSLATE_DIR    = "D:\apps\cc-translate"   # install location
      $env:CC_TRANSLATE_DRYRUN = "1"                        # print steps, change nothing
#>

$ErrorActionPreference = 'Stop'

$Repo       = 'https://github.com/mclight-ship-it/cc-translate.git'
$InstallDir = if ($env:CC_TRANSLATE_DIR) { $env:CC_TRANSLATE_DIR } else { Join-Path $HOME 'cc-translate' }
$DryRun     = [bool]$env:CC_TRANSLATE_DRYRUN
$CodexPackage = '@openai/codex@0.146.0'

# ---------- small helpers ----------
function Info($m) { Write-Host $m -ForegroundColor Gray }
function Good($m) { Write-Host $m -ForegroundColor Green }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Step($n, $m) { Write-Host "`n[$n/6] $m" -ForegroundColor Cyan }

function Have($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

function Update-SessionPath {
    # winget-installed tools land in PATH via the registry, but the current
    # process doesn't see them until we re-read it. Also fold in npm's global
    # bin (where the model CLIs install their shims).
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

function Get-CodexCmd {
    foreach ($name in @('codex.exe', 'codex.cmd')) {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
    }
    $guess = Join-Path $env:APPDATA 'npm\codex.cmd'
    if (Test-Path $guess) { return $guess }
    return 'codex.cmd'
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

function Ensure-ExecutionPolicy {
    # Windows' default 'Restricted' policy blocks the PowerShell .ps1 shims npm
    # installs for `claude` (and `npm` itself), so the user can't even run
    # `claude` to log in — which leaves the app with no Claude session and makes
    # every translation fail. Raise the CurrentUser policy (no admin needed) to
    # RemoteSigned when the effective policy is more restrictive; RemoteSigned
    # runs local scripts and still requires a signature for downloaded ones.
    $eff = try { Get-ExecutionPolicy } catch { return }
    if ($eff -notin @('Restricted', 'AllSigned', 'Undefined')) {
        Good "  ✓ 执行策略正常（$eff），可运行本地脚本"
        return
    }
    if ($DryRun) {
        Info "  [dry-run] 将把当前用户执行策略设为 RemoteSigned（当前: $eff）"
        return
    }
    try {
        Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force
        $now = Get-ExecutionPolicy
        if ($now -in @('Restricted', 'AllSigned', 'Undefined')) {
            Warn "  ⚠ 执行策略仍为 $now（可能被组策略锁定）。登录时请改用命令： claude.cmd"
        } else {
            Good "  ✓ 已允许运行本地脚本（执行策略: $now）——这样才能运行 claude 登录"
        }
    } catch {
        Warn "  ⚠ 无法调整执行策略。登录 Claude 时请改用命令： claude.cmd"
    }
}

# ---------- banner ----------
Write-Host ""
Write-Host "  CC Translate 安装程序" -ForegroundColor White
Write-Host "  安装目录: $InstallDir" -ForegroundColor DarkGray
if ($DryRun) { Warn "  （dry-run 模式：只显示步骤，不做任何改动）" }

# ---------- prep: allow local .ps1 scripts so `claude` can be launched ----------
Write-Host "`n[准备] PowerShell 执行策略" -ForegroundColor Cyan
Ensure-ExecutionPolicy

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

# ---------- 3. model CLIs ----------
Step 3 "安装 / 升级模型 CLI（Claude Code + OpenAI Codex）"
Warn "  （Claude 升到最新版；Codex 安装当前已验证兼容长文流式 Beta 的版本）"
$npm = Get-NpmCmd
Invoke-Or-DryRun "$npm install -g @anthropic-ai/claude-code@latest" {
    & $npm install -g '@anthropic-ai/claude-code@latest'
}
Update-SessionPath
if ((Have claude) -or $DryRun) { Good "  ✓ Claude CLI 就绪" }
else { Warn "  ⚠ 装完仍找不到 claude——请确认 npm 全局目录（%APPDATA%\npm）在 PATH 中。" }
Info "  安装已验证可用于 Codex 长文流式 Beta 的版本…"
Invoke-Or-DryRun "$npm install -g $CodexPackage" {
    & $npm install -g $CodexPackage
}
Update-SessionPath
if ((Have codex) -or (Have codex.cmd) -or $DryRun) {
    Good "  ✓ Codex CLI 就绪"
} else {
    Warn "  ⚠ 装完仍找不到 codex——请确认 npm 全局目录（%APPDATA%\npm）在 PATH 中。"
}

# ---------- 4. python deps ----------
Step 4 "安装 Python 依赖"
# 依赖清单集中在 requirements.txt（单一事实来源）。winsdk（截图翻译的离线本地
# OCR）与 comtypes（智能选区识别）为可选增强，缺失时对应功能自动降级/关闭，不影响
# 核心翻译。
Invoke-Or-DryRun "python -m pip install --user --upgrade pip; python -m pip install --user -r requirements.txt" {
    # Try system-wide install first, fall back to --user if permission denied
    python -m pip install --upgrade pip
    python -m pip install --upgrade -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Warn "  ⚠ 系统级安装失败（权限不足），改用用户级安装…"
        python -m pip install --user --upgrade pip
        python -m pip install --user --upgrade -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Python 依赖安装失败。请尝试：`n  1) 以管理员身份重新运行本脚本`n  2) 手动运行: python -m pip install --user -r requirements.txt"
        }
    }
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

function Test-CodexReady {
    if ($DryRun) { return $false }
    try {
        $codex = Get-CodexCmd
        & $codex login status *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

Step 5 "登录模型 CLI（需要你手动完成浏览器授权）"
if (Test-ClaudeReady) {
    Good "  ✓ 检测到已登录的 Claude 账号，跳过。"
} else {
    Warn "  还需登录一次（用你现有的 Claude 订阅，走浏览器授权，不额外收费）："
    Info  "      1) 打开一个新的终端窗口"
    Info  "      2) 运行:  claude    （若提示脚本被禁用，改用:  claude.cmd）"
    Info  "      3) 按提示在浏览器完成登录，成功后按 Ctrl+C 退出交互模式"
    Info  "  未登录时 CC Translate 会弹出「未登录」提示——登录后即可正常翻译。"
}
if (Test-CodexReady) {
    Good "  ✓ 检测到已登录的 ChatGPT / Codex 账号，跳过。"
} else {
    Warn "  若要使用 OpenAI GPT，还需登录一次 Codex（Claude 默认路径不受影响）："
    Info  "      1) 打开一个新的终端窗口"
    Info  "      2) 运行:  codex login    （若提示脚本被禁用，改用:  codex.cmd login）"
    Info  "      3) 在浏览器使用 ChatGPT 账号完成登录"
    Info  "      4) 运行:  codex login status    确认登录成功"
    Info  "  CC Translate 只复用 Codex CLI 的登录状态，不读取或保存认证 token。"
}

# ---------- 6. launch ----------
Step 6 "启动 CC Translate"
Invoke-Or-DryRun "创建 CC Translate 启动器并启动（工作目录 $InstallDir）" {
    $pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    if (-not $pyw) { $pyw = 'pythonw' }
    $launcher = python -c "import cc_update; print(cc_update.ensure_branded_launcher() or '')"
    if ($LASTEXITCODE -ne 0 -or -not $launcher -or -not (Test-Path $launcher)) {
        $launcher = $pyw
    }
    Start-Process $launcher -ArgumentList 'translator.pyw' -WorkingDirectory $InstallDir
}

Write-Host ""
Good  "完成！托盘里会出现 「CC」 图标。"
Info  "用法：选中任意文字，快速双击 Ctrl+C，鼠标旁即弹出译文。"
Info  "开机自启：右键托盘图标 → 设置 → 勾选「开机自动启动」。"
Write-Host ""
