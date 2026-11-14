# CutSkills · Windows 一键体检与安装（PowerShell 5.1+ / PowerShell 7+）
#
# 与 scripts/runtime.sh 的分工：
#   · scripts/runtime.sh  —— POSIX（macOS / Linux / WSL 里的 bash），install / adopt 只跑那边
#   · install.ps1（本文件）—— Windows 原生：-CheckOnly 只体检不装，-Install 装缺的
#   · 两边共用一份依赖清单 deps.json（npm / pip 版本、platforms 分叉、fonts 目录）
#
# 安装目标（与 bash 版同一套目录约定，只有根不同）：
#   %CUTSKILLS_HOME%\<默认 %USERPROFILE%\.cutskills>
#   ├── npm\            node 依赖（hyperframes）与 npm 缓存前缀
#   ├── npm-cache\      npm 下载缓存
#   ├── py\             python 依赖（pip --target）
#   ├── cache\ models\  chrome / whisper 权重（HYPERFRAMES / HF_HOME）
#   ├── bin\ opt\bin\   自建命令
#   └── windows-selfcheck.md   首次安装的自检清单（-CheckOnly / -Install 都会刷新）
#
# 什么装在哪（与 bash 版的差别要说清）：
#   · 下载成包的东西（npm / pip / 字体）全部落 %CUTSKILLS_HOME%，不散到任务目录；
#   · 系统级工具（ffmpeg / Node / Python）走 winget，装在它们自己的默认位置并进系统 PATH——
#     Windows 没有 brew，硬把 ffmpeg.exe 塞进 %CUTSKILLS_HOME% 反而会和系统 PATH 打架。
#     所以这两类在体检里是分开报的（一个是「包在不在家目录」，一个是「命令在不在 PATH」）。
#
# **重要：本脚本未在 Windows 真机验证。** 目标不是宣称「支持 Windows」，而是让第一个
# Windows 用户三分钟能定位卡在哪一步：每一步失败都打印「下一步该做什么」，并且
# 不和稀泥（不用 -ErrorAction SilentlyContinue 掩盖安装失败）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File install.ps1               # 体检（默认，不改任何东西）
#   powershell -ExecutionPolicy Bypass -File install.ps1 -CheckOnly    # 同上，写明确
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Install      # 装缺的
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Install -FontSource D:\fonts
#
# 退出码：0 全绿（⚠️ / ➖ 不算红）；1 有 ❌；2 用法错。

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$Install,
    [string]$FontSource = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($CheckOnly -and $Install) {
    Write-Host '✗ -CheckOnly 与 -Install 只能二选一（体检不改任何东西，安装会写 %CUTSKILLS_HOME%）' -ForegroundColor Red
    exit 2
}

$script:DoInstall = $Install.IsPresent          # 不带参数时默认只体检：新用户第一跑不该动他的机器
$script:RepoRoot = $PSScriptRoot
$script:Home = if ($env:CUTSKILLS_HOME) { $env:CUTSKILLS_HOME } else { Join-Path $env:USERPROFILE '.cutskills' }
$script:DepsFile = Join-Path $script:RepoRoot 'deps.json'
$script:Results = New-Object System.Collections.ArrayList
$script:SelfCheckText = ''            # 自检清单正文（Write-SelfCheckDoc 填；-CheckOnly 会打印）
$script:Python = $null
$script:Node = $null
$script:Npm = $null
$script:Ffmpeg = $null

# ----------------------------------------------------------------- 结果登记
# 四种状态与 scripts\doctor.py 同一口径：
#   ok   ✅ 在位 / warn ⚠️ 可选缺失 / bad ❌ 缺失 / na ➖ 平台不支持
# ➖ 是「这个平台拿不到」，不是坏了——BRIEF 明令不许把平台不支持报成红。
function Add-Result {
    param([string]$Level, [string]$Text)
    [void]$script:Results.Add([pscustomobject]@{ Level = $Level; Text = $Text })
    $mark = switch ($Level) {
        'ok' { '✅' }
        'warn' { '⚠️ ' }
        'bad' { '❌' }
        default { '➖' }
    }
    $color = switch ($Level) {
        'ok' { 'Green' }
        'warn' { 'Yellow' }
        'bad' { 'Red' }
        default { 'DarkGray' }
    }
    Write-Host ("  {0} {1}" -f $mark, $Text) -ForegroundColor $color
}

function Write-Step {
    param([string]$Title)
    Write-Host ''
    Write-Host "== $Title" -ForegroundColor Cyan
}

function Write-Next {
    param([string]$Hint)
    Write-Host "  下一步：$Hint" -ForegroundColor Yellow
}

# 探测命令在不在。这里用 -ErrorAction Ignore 是「探测」，不是掩盖安装失败：
# 真正的安装命令一律不吞错误，失败会把原异常打出来并把该项记成 ❌。
function Find-CommandPath {
    param([string]$Name)
    $cmd = Get-Command -Name $Name -CommandType Application -ErrorAction Ignore
    if ($cmd) { return $cmd.Source }
    return $null
}

# winget 装完之后，本会话的 PATH 还是旧的：从机器级 + 用户级 PATH 重新拼一次
function Update-SessionPath {
    $parts = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User')
    ) | Where-Object { $_ }
    if ($parts.Count -gt 0) { $env:Path = ($parts -join ';') }
}

# ----------------------------------------------------------------- deps.json
if (-not (Test-Path -LiteralPath $script:DepsFile)) {
    Write-Host "✗ 找不到依赖清单：$script:DepsFile（本脚本必须在仓库根目录，和 deps.json 同级）" -ForegroundColor Red
    exit 2
}
try {
    $script:Deps = Get-Content -LiteralPath $script:DepsFile -Raw -Encoding UTF8 | ConvertFrom-Json
} catch {
    Write-Host "✗ deps.json 解析失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Next 'deps.json 是 JSON，别手工加注释；改坏了用 git checkout deps.json 找回'
    exit 2
}

function Get-DepNames {
    param([string]$Section)
    $node = $script:Deps.PSObject.Properties[$Section]
    if (-not $node) { return @() }
    return @($node.Value.PSObject.Properties | ForEach-Object { $_.Name })
}

function Get-DepSpec {
    param([string]$Section, [string]$Name)
    $node = $script:Deps.PSObject.Properties[$Section]
    if (-not $node) { return '' }
    $item = $node.Value.PSObject.Properties[$Name]
    if (-not $item) { return '' }
    return [string]$item.Value
}

function Get-PlatformFork {
    param([string]$Section, [string]$Item, [string]$Platform = 'win-x64')
    $sect = $script:Deps.PSObject.Properties[$Section]
    if (-not $sect) { return $null }
    $node = $sect.Value.PSObject.Properties[$Item]
    if (-not $node) { return $null }
    $plats = $node.Value.PSObject.Properties['platforms']
    if (-not $plats) { return $null }
    $fork = $plats.Value.PSObject.Properties[$Platform]
    if (-not $fork) { return $null }
    return $fork.Value
}

# ----------------------------------------------------------------- 各项检查
function Test-Python {
    # cap: check:python
    Write-Step 'Python（产线本体）'
    $py = $null
    foreach ($name in @('python3', 'python', 'py')) {
        $py = Find-CommandPath $name
        if ($py) { break }
    }
    if (-not $py) {
        if ($script:DoInstall) {
            try {
                & winget install --id Python.Python.3.12 --source winget --accept-source-agreements --accept-package-agreements
                if ($LASTEXITCODE -ne 0) { throw "winget 退出码 $LASTEXITCODE" }
                Update-SessionPath
                $py = Find-CommandPath 'python'
            } catch {
                Add-Result 'bad' "Python 安装失败：$($_.Exception.Message)"
                Write-Next '手动装 python.org 的 3.12（安装时勾 Add python.exe to PATH），重开终端再跑 -CheckOnly'
                return
            }
        } else {
            Add-Result 'bad' 'Python 缺失（产线本体）'
            Write-Next 'powershell -ExecutionPolicy Bypass -File install.ps1 -Install，或手动装 python.org 的 3.12 并勾 Add python.exe to PATH'
            return
        }
    }
    $ver = (& $py -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>&1) -join ' '
    $major = 0
    if ($ver -match '^(\d+)') { $major = [int]$Matches[1] }
    if ($major -ge 3) {
        Add-Result 'ok' "python → $py（$ver）"
    } else {
        Add-Result 'bad' "python 版本不对：$ver（要 >= 3.9）"
        Write-Next 'winget install Python.Python.3.12'
    }
    $script:Python = $py
}

function Test-Node {
    # cap: check:node
    Write-Step 'Node.js（hyperframes 渲染层，要 >= 22）'
    $node = Find-CommandPath 'node'
    if (-not $node) {
        if ($script:DoInstall) {
            try {
                & winget install --id OpenJS.NodeJS.LTS --source winget --accept-source-agreements --accept-package-agreements
                if ($LASTEXITCODE -ne 0) { throw "winget 退出码 $LASTEXITCODE" }
                Update-SessionPath
                $node = Find-CommandPath 'node'
            } catch {
                Add-Result 'bad' "Node 安装失败：$($_.Exception.Message)"
                Write-Next '手动装 nodejs.org 的 LTS（>= 22），重开终端再跑 -CheckOnly'
                return
            }
        } else {
            Add-Result 'bad' 'node 缺失（hyperframes 要 >= 22）'
            Write-Next 'install.ps1 -Install（winget install OpenJS.NodeJS.LTS）'
            return
        }
    }
    $v = (& $node --version 2>&1) -join ' '
    $major = 0
    if ($v -match 'v(\d+)') { $major = [int]$Matches[1] }
    if ($major -ge 22) {
        Add-Result 'ok' "node → $node（$v）"
    } elseif ($major -gt 0) {
        Add-Result 'warn' "node 版本 $v < 22——hyperframes 要 >= 22"
        Write-Next 'winget upgrade OpenJS.NodeJS.LTS（或卸了重装 LTS）'
    } else {
        Add-Result 'warn' "node 版本读不出来（$v）"
    }
    $script:Node = $node
}

function Test-Npm {
    # cap: check:npm
    Write-Step 'npm（装 hyperframes 用）'
    $npm = Find-CommandPath 'npm'
    if ($npm) {
        $v = (& $npm --version 2>&1) -join ' '
        Add-Result 'ok' "npm → $npm（$v）"
        $script:Npm = $npm
    } else {
        Add-Result 'bad' 'npm 缺失——它随 Node 一起装，node 装对了这里不会缺'
        Write-Next '重开终端让 PATH 生效；还不行就重装 nodejs.org 的 LTS'
    }
}

function Test-Ffmpeg {
    # cap: check:ffmpeg
    Write-Step 'ffmpeg（抽帧/调色/混音）'
    $ff = Find-CommandPath 'ffmpeg'
    if (-not $ff) {
        if ($script:DoInstall) {
            try {
                & winget install --id Gyan.FFmpeg --source winget --accept-source-agreements --accept-package-agreements
                if ($LASTEXITCODE -ne 0) { throw "winget 退出码 $LASTEXITCODE" }
                Update-SessionPath
                $ff = Find-CommandPath 'ffmpeg'
            } catch {
                Add-Result 'bad' "ffmpeg 安装失败：$($_.Exception.Message)"
                Write-Next '手动装 gyan.dev 的 full build，把 bin 目录加进 PATH，重开终端再跑 -CheckOnly'
                return
            }
        } else {
            Add-Result 'bad' 'ffmpeg 缺失（抽帧/调色/混音）'
            Write-Next 'install.ps1 -Install（winget install Gyan.FFmpeg）'
            return
        }
    }
    $v = (& $ff -version 2>&1 | Select-Object -First 1) -join ' '
    Add-Result 'ok' "ffmpeg → $ff（$v）"
    $script:Ffmpeg = $ff
}

function Test-Ffprobe {
    # cap: check:ffprobe
    Write-Step 'ffprobe（规格探测）'
    $fp = Find-CommandPath 'ffprobe'
    if ($fp) {
        Add-Result 'ok' "ffprobe → $fp"
    } else {
        Add-Result 'bad' 'ffprobe 缺失（规格探测）——它随 ffmpeg 一起装'
        Write-Next 'winget install Gyan.FFmpeg（装完重开终端让 PATH 生效）'
    }
}

function Test-MacOnlyTools {
    # cap: check:sips
    Write-Step 'macOS 专属工具'
    Add-Result 'na' 'sips 不适用 Windows（macOS 的 ImageIO CLI；本仓只在 macOS 分支调用）'
    # cap: check:ffmpeg-zimg
    Add-Result 'na' '自编 ffmpeg-zimg（HDR→SDR）不适用 Windows：用 Gyan.FFmpeg 自带 libzimg 的构建；HDR 转换这条链未在 Windows 上验证'
}

function Test-HyperframesPackage {
    # cap: install:npm-hyperframes
    Write-Step 'HyperFrames（node 包）'
    $npmDir = Join-Path $script:Home 'npm'
    $pkgDir = Join-Path $npmDir 'node_modules\hyperframes'
    if (Test-Path -LiteralPath $pkgDir) {
        $ver = '?'
        $pj = Join-Path $pkgDir 'package.json'
        if (Test-Path -LiteralPath $pj) {
            $ver = (Get-Content -LiteralPath $pj -Raw -Encoding UTF8 | ConvertFrom-Json).version
        }
        Add-Result 'ok' "hyperframes 已装在 $pkgDir（$ver）"
        return
    }
    if (-not $script:DoInstall) {
        Add-Result 'bad' 'hyperframes 未装（$CUTSKILLS_HOME\npm 下没有）'
        Write-Next 'install.ps1 -Install（依赖全落 %CUTSKILLS_HOME%，不散装）'
        return
    }
    if (-not $script:Npm) {
        Add-Result 'bad' 'hyperframes 装不了：npm 不在 PATH'
        Write-Next '先解决上面的 npm 缺失，再重跑 -Install'
        return
    }
    $spec = Get-DepSpec -Section 'npm' -Name 'hyperframes'
    if (-not $spec) {
        Add-Result 'bad' 'hyperframes 版本没在 deps.json 的 npm 段里'
        Write-Next 'deps.json 的 npm.hyperframes 必须是纯版本号（不能带备注）'
        return
    }
    try {
        New-Item -ItemType Directory -Force -Path $npmDir | Out-Null
        Write-Host "  → 安装 hyperframes@$spec 到 $npmDir"
        & $script:Npm install --prefix $npmDir --silent --no-fund --no-audit "hyperframes@$spec"
        if ($LASTEXITCODE -ne 0) { throw "npm 退出码 $LASTEXITCODE" }
        Add-Result 'ok' "hyperframes@$spec 已装到 $pkgDir"
    } catch {
        Add-Result 'bad' "hyperframes 安装失败：$($_.Exception.Message)"
        Write-Next '多半是网络/代理：npm config get proxy 看一眼，或设 npm_config_registry 后重跑 -Install'
    }
}

function Test-PipDeps {
    # cap: install:pip-deps
    Write-Step 'Python 依赖（pypinyin / pyyaml / pillow / numpy）'
    $pyDir = Join-Path $script:Home 'py'
    $names = Get-DepNames -Section 'pip'
    if ($names.Count -eq 0) {
        Add-Result 'na' 'deps.json 的 pip 段为空，没有要装的'
        return
    }
    if (-not $script:Python) {
        Add-Result 'bad' 'Python 依赖装不了：python 不在 PATH'
        Write-Next '先解决上面的 Python 缺失，再重跑 -Install'
        return
    }
    $missing = @()
    foreach ($n in $names) {
        $hit = Join-Path $pyDir $n
        $distInfo = Get-ChildItem -LiteralPath $pyDir -Filter "$n-*.dist-info" -ErrorAction Ignore
        if ((Test-Path -LiteralPath $hit) -or $distInfo) { continue }
        $missing += $n
    }
    if ($missing.Count -eq 0) {
        Add-Result 'ok' "python 依赖已齐（$($names -join ' / ')）"
        return
    }
    if (-not $script:DoInstall) {
        Add-Result 'bad' "python 依赖缺 $($missing.Count) 个：$($missing -join ' / ')"
        Write-Next 'install.ps1 -Install（pip install --target %CUTSKILLS_HOME%\py）'
        return
    }
    foreach ($n in $missing) {
        $spec = Get-DepSpec -Section 'pip' -Name $n
        try {
            New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
            Write-Host "  → pip install --target $pyDir $n$spec"
            & $script:Python -m pip install --quiet --target $pyDir "$n$spec"
            if ($LASTEXITCODE -ne 0) { throw "pip 退出码 $LASTEXITCODE" }
            Add-Result 'ok' "pip $n$spec 已装到 $pyDir"
        } catch {
            Add-Result 'bad' "pip $n 安装失败：$($_.Exception.Message)"
            Write-Next "手动：$($script:Python) -m pip install --target `"$pyDir`" $n$spec；pyyaml 有随包 vendor 版（scripts\vendor\yaml）可先顶着"
        }
    }
}

function Test-InstalledPackages {
    # cap: check:npm-packages
    Write-Step '已装的 node 包'
    $mods = Join-Path $script:Home 'npm\node_modules'
    if (Test-Path -LiteralPath $mods) {
        $list = Get-ChildItem -LiteralPath $mods -Directory -ErrorAction Ignore |
            Where-Object { $_.Name -notlike '.*' } |
            Select-Object -First 30
        if ($list) {
            foreach ($d in $list) { Write-Host ("    {0}" -f $d.Name) }
            Add-Result 'ok' "node 包 $($list.Count) 个（最多列 30）"
        } else {
            Add-Result 'na' "node 包目录是空的：$mods"
        }
    } else {
        Add-Result 'na' "还没有 node 包目录：$mods"
    }
    # cap: check:python-packages
    Write-Step '已装的 python 包'
    $pyDir = Join-Path $script:Home 'py'
    if (Test-Path -LiteralPath $pyDir) {
        $list = Get-ChildItem -LiteralPath $pyDir -Directory -ErrorAction Ignore |
            Where-Object { $_.Name -notlike '*.dist-info' } |
            Select-Object -First 30
        if ($list) {
            foreach ($d in $list) { Write-Host ("    {0}" -f $d.Name) }
            Add-Result 'ok' "python 包 $($list.Count) 个（最多列 30）"
        } else {
            Add-Result 'na' "python 包目录是空的：$pyDir"
        }
    } else {
        Add-Result 'na' "还没有 python 包目录：$pyDir"
    }
}

function Test-Transcription {
    # cap: check:coli
    Write-Step '语音转写（coli + sensevoice）'
    $fork = Get-PlatformFork -Section 'system' -Item 'coli'
    $why = 'Cola.app 没有 Windows 版，@marswave/coli 与 sherpa-onnx 闭包都不在 Windows 上分发'
    if ($fork -and $fork.PSObject.Properties['why']) { $why = [string]$fork.why }
    Add-Result 'na' "平台不支持：$why"
    # cap: check:asr-model
    Add-Result 'na' 'sensevoice 模型不适用本平台（没有 coli 可调用）；Windows 的转写走 hyperframes transcribe（whisper）'
    Write-Next '要用本地字级转写：迁到 macOS（python3 scripts/fetch_coli.py）；Windows 上先接受 whisper 降级'
}

function Test-BeatDetection {
    # cap-extra: check:beat
    Write-Step '节拍检测（beat_sync，纯 stdlib + ffmpeg）'
    if (-not $script:Python) {
        Add-Result 'bad' '节拍检测跑不了：python 不在 PATH'
        Write-Next '先解决 Python，再重跑 -CheckOnly'
        return
    }
    if (-not $script:Ffmpeg) {
        Add-Result 'bad' '节拍检测跑不了：ffmpeg 不在 PATH（解码靠它）'
        Write-Next 'install.ps1 -Install（winget install Gyan.FFmpeg）'
        return
    }
    Push-Location $script:RepoRoot
    $ok = $false
    $out = ''
    try {
        $out = (& $script:Python -c "import sys; sys.path.insert(0, 'scripts'); import beat_sync; print('beat_sync ok')" 2>&1) -join ' '
        $ok = ($LASTEXITCODE -eq 0)
    } catch {
        $out = $_.Exception.Message
    } finally {
        Pop-Location
    }
    if ($ok) {
        Add-Result 'ok' "beat_sync 可导入（$($out.Trim())）；真检测由 doctor.py 跑一次合成鼓点"
    } else {
        Add-Result 'bad' "beat_sync 导入失败：$($out.Trim())"
        Write-Next '确认在仓库根目录跑；scripts\beat_sync.py 只用 stdlib，失败通常是 python 装在了奇怪的路径'
    }
}

function Test-Gsap {
    # cap: check:gsap
    Write-Step '前端运行时 gsap'
    $cands = @(
        (Join-Path $script:RepoRoot 'assets\gsap.min.js'),
        (Join-Path $env:USERPROFILE '.cola\skills\srt-motion-cards\assets\gsap.min.js')
    )
    $hit = $null
    foreach ($c in $cands) {
        if ((Test-Path -LiteralPath $c) -and ((Get-Item -LiteralPath $c).Length -gt 4096)) { $hit = $c; break }
    }
    if ($hit) {
        Add-Result 'ok' "gsap → $hit"
    } else {
        Add-Result 'bad' 'gsap.min.js 找不到——新建项目渲染会在拷资产这一步退出'
        Write-Next "把任意一份 gsap.min.js 放到 $(Join-Path $script:RepoRoot 'assets\gsap.min.js')"
    }
}

function Test-Sandbox {
    # cap-extra: check:sandbox
    Write-Step 'AI 场景围栏'
    Add-Result 'na' 'Windows 不支持 AI 场景生成（没有 sandbox-exec）：scene_sandbox 会 exit 5 拒跑；用实拍素材或内置模板替代'
}

function Test-Fonts {
    # cap-extra: check:fonts
    Write-Step '字体（字幕/大字按宽度比反算字号，缺字体会在 @font-face 那步停）'
    $fork = Get-PlatformFork -Section 'fonts' -Item 'fonts'
    # 系统环境变量有可能缺失（极少见），缺了也不能让脚本崩：给一份默认值
    $windir = if ($env:WINDIR) { $env:WINDIR } else { 'C:\Windows' }
    $localapp = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
    $dirs = @(
        (Join-Path $windir 'Fonts'),
        (Join-Path $localapp 'Microsoft\Windows\Fonts')
    )
    if ($fork -and $fork.PSObject.Properties['dirs']) {
        $dirs = @($fork.dirs | ForEach-Object { [Environment]::ExpandEnvironmentVariables($_) })
    }
    $required = @()
    $fnode = $script:Deps.PSObject.Properties['fonts']
    if ($fnode -and $fnode.Value.PSObject.Properties['required']) { $required = @($fnode.Value.required) }
    if ($required.Count -eq 0) {
        Add-Result 'warn' '依赖清单里没登记字体（deps.json fonts.required）'
        return
    }
    $missing = @()
    foreach ($f in $required) {
        $stem = [System.IO.Path]::GetFileNameWithoutExtension($f)
        $found = $null
        foreach ($d in $dirs) {
            if (-not (Test-Path -LiteralPath $d)) { continue }
            $hit = Get-ChildItem -LiteralPath $d -Filter "$stem.*" -ErrorAction Ignore | Select-Object -First 1
            if ($hit) { $found = $hit.FullName; break }
        }
        if (-not $found) { $missing += $f }
    }
    if ($missing.Count -eq 0) {
        Add-Result 'ok' "字体 $($required.Count)/$($required.Count) 在位（$($required -join ' · ')）"
        return
    }
    if (-not $script:DoInstall) {
        Add-Result 'warn' "字体缺 $($missing.Count)/$($required.Count)：$($missing -join ' · ')"
        Write-Next "install.ps1 -Install -FontSource <放着这些 ttf 的目录>；或手动拷到 $($dirs[1]) 再右键「安装」"
        return
    }
    if (-not $FontSource) {
        Add-Result 'warn' "字体缺 $($missing.Count)/$($required.Count)：$($missing -join ' · ')"
        Write-Next "加 -FontSource <放着 ttf 的目录> 重跑 -Install（脚本不替你从网上抓字体：授权各自不同，得你自己确认来源）"
        return
    }
    if (-not (Test-Path -LiteralPath $FontSource)) {
        Add-Result 'bad' "-FontSource 目录不存在：$FontSource"
        Write-Next '给一个真的目录（里面有 SmileySans-Oblique.ttf / NotoSansSC-Black.ttf / NotoSerifSC-Black.ttf）'
        return
    }
    $userFontDir = Join-Path $localapp 'Microsoft\Windows\Fonts'
    # cap-extra: install:fonts
    try {
        New-Item -ItemType Directory -Force -Path $userFontDir | Out-Null
        $regPath = 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts'
        if (-not (Test-Path -LiteralPath $regPath)) { New-Item -Path $regPath -Force | Out-Null }
        foreach ($f in $missing) {
            $src = Join-Path $FontSource $f
            if (-not (Test-Path -LiteralPath $src)) {
                Add-Result 'bad' "字体源里没有 $f（找的是 $src）"
                Write-Next '把 deps.json fonts.required 里列的那几个 ttf 都放进 -FontSource 目录'
                continue
            }
            $dst = Join-Path $userFontDir $f
            Copy-Item -LiteralPath $src -Destination $dst -Force
            $regName = "$([System.IO.Path]::GetFileNameWithoutExtension($f)) (TrueType)"
            Set-ItemProperty -Path $regPath -Name $regName -Value $dst
            Add-Result 'ok' "字体 $f 已装到 $dst（注销/重登后对其它程序生效）"
        }
    } catch {
        Add-Result 'bad' "字体安装失败：$($_.Exception.Message)"
        Write-Next "手动：把 ttf 拷到 $userFontDir，再右键「安装」；管理员装了也没用的话用「为所有用户安装」"
    }
}

# ----------------------------------------------------------------- 自检清单
function Write-SelfCheckDoc {
    # cap-extra: doc:selfcheck
    $docPath = Join-Path $script:Home 'windows-selfcheck.md'
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    $script:SelfCheckText = ''
    # 单引号 here-string：文档里的 $env:XXX 是给用户照抄的字面量，不能被本脚本展开
    $template = @'
# CutSkills · Windows 首次安装自检清单

> 由 install.ps1 生成于 __STAMP__（依赖根目录 __HOME__）。
> **本仓没有 Windows 真机验证。** 这份清单的作用是：第一个 Windows 用户按顺序跑完，
> 就能把问题定位到具体哪一步，把输出贴回来即可复现。

## 0. 先决条件

- Windows 10 / 11，PowerShell 5.1 或 7（`$PSVersionTable.PSVersion` 能看到版本）
- 仓库放在本地盘，**不要放 OneDrive 之类的同步目录**（npm 装包会被同步锁住，报 EPERM）
- 一律用 `powershell -ExecutionPolicy Bypass -File install.ps1 ...` 调，不要双击

## 1. 逐条跑，对照期望输出

| # | 命令 | 期望输出 | 不对时看哪 |
|---|---|---|---|
| 1 | `powershell -ExecutionPolicy Bypass -File install.ps1 -CheckOnly` | 末尾一行 `结论：...`，❌ 0 个 | 每条 ❌ 下面都直接印了「下一步」 |
| 2 | `python --version` | `Python 3.12.x`（>= 3.9） | 缺：`winget install Python.Python.3.12`，装时勾 Add python.exe to PATH，重开终端 |
| 3 | `node --version` | `v22.x` 或更高 | 低于 22：`winget upgrade OpenJS.NodeJS.LTS` |
| 4 | `npm --version` | `10.x` / `11.x` | npm 缺 = Node 没装好，别单独装 npm |
| 5 | `ffmpeg -version` | 第一行含 `ffmpeg version` | 缺：`winget install Gyan.FFmpeg`，装完**重开终端**（PATH 才刷新） |
| 6 | `ffprobe -version` | 第一行含 `ffprobe version` | 它随 ffmpeg 一起装，缺就是 5 没成 |
| 7 | `powershell -ExecutionPolicy Bypass -File install.ps1 -Install` | 每一步 `✅`，末尾 `结论：Windows 侧检查项都过了（❌ 0 ...）` | 失败那一步会打原始报错 + 「下一步」，照做后重跑 |
| 8 | `install.ps1 -CheckOnly` 再跑一次 | 只剩 `➖`，没有 ❌ | 还有 ❌ 说明第 7 步没真装成 |
| 9 | `install.ps1 -Install -FontSource <放着 ttf 的目录>` | `字体 N/N 在位（得意黑 · 思源黑体 · …）` | 缺哪个会点名，按 `deps.json` 的 `fonts.required` 逐个对照 |
| 10 | `python scripts\doctor.py` | `结论：可以开工（❌ 0 ...）` + 一行「Windows 未真机验证」说明 | 用法与 macOS 完全相同 |

## 2. `➖` 是「平台不支持」，不是坏了

Windows 上这几项**注定**是 `➖`，不要想办法点亮：

- **语音转写（coli + sensevoice）**：Cola.app 没有 Windows 版，`@marswave/coli` 与
  `sherpa-onnx` 闭包不在 Windows 上分发。转写走 `hyperframes transcribe`（whisper）降级——
  能用，但没有字级真值（字幕对齐精度会降一档）。
- **AI 场景围栏（sandbox-exec）**：Windows 没有等价的系统围栏，`scripts\scene_sandbox.py`
  会 **exit 5 拒跑**（这是设计，不是 bug）。Windows 上请用实拍素材或内置场景模板。
- **sips / 自编 ffmpeg-zimg**：macOS 专属。Windows 用 Gyan.FFmpeg（自带 libzimg）的构建；
  HDR→SDR 转换这条链**没在 Windows 上验证过**，HDR 素材先当「未支持」。

## 3. 环境变量（每个新终端都要设一遍）

```powershell
$env:CUTSKILLS_HOME = "__HOME__"
$env:NODE_PATH      = "__HOME__\npm\node_modules"
$env:PYTHONPATH     = "__HOME__\py;__REPO__\scripts\vendor"
$env:PATH           = "__HOME__\npm\node_modules\.bin;__HOME__\bin;__HOME__\opt\bin;" + $env:PATH
$env:XDG_CACHE_HOME = "__HOME__\cache"
$env:HF_HOME        = "__HOME__\models\hf"
$env:PUPPETEER_CACHE_DIR = "__HOME__\cache\puppeteer"
```

Windows 的 `PATH` 分隔符是 `;`，bash 版（`scripts/runtime.sh env`）用的是 `:`——两边不能混抄。
`install.ps1 -CheckOnly` 末尾会把这段再打一遍，直接复制即可。

## 4. 常见报错怎么解

| 报错 | 原因 | 解法 |
|---|---|---|
| `... 因为在此系统上禁止运行脚本` | 执行策略 | 用 `powershell -ExecutionPolicy Bypass -File install.ps1`，别双击 |
| `winget 不是内部或外部命令` | 没装 App Installer | Microsoft Store 装「应用安装程序」；或手动装 Python / Node / ffmpeg 后重跑 -CheckOnly |
| `npm ERR! code EPERM` | 目录被占用 / 同步盘 | 仓库移出 OneDrive；关掉占着 `%CUTSKILLS_HOME%` 的终端再装 |
| `ModuleNotFoundError: No module named 'yaml'` | `PYTHONPATH` 没设 | 按第 3 节设好；`scripts\vendor\yaml` 是随包的，不需要联网 |
| `ModuleNotFoundError: No module named 'numpy'` | `$CUTSKILLS_HOME\py` 没设进 `PYTHONPATH` | 同上，或 `install.ps1 -Install` 装 pip 依赖 |
| `python : command not found` 但确实装了 | 安装时没勾 Add to PATH | 重装勾上，或用 `py -3` 代替 `python` |
| `hyperframes 安装失败：npm 退出码 1` | 代理 / registry | `npm config get proxy`；必要时 `npm config set registry https://registry.npmmirror.com` 后重跑 |
| 渲染时 `chrome-headless-shell` 下不下来 | 网络 | 设 `$env:HYPERFRAMES_BROWSER_PATH` 指向本机 `chrome.exe` |
| 字体装完 `python scripts\fonts.py --list` 还说缺 | 字体只对当前用户注册 | 注销重登（别只关终端）；或右键「为所有用户安装」 |
| `scene_sandbox` 报 exit 5 | Windows 无 sandbox-exec | 预期行为：改用实拍素材 / 内置模板，不要加 `--allow-unsandboxed` |

## 5. 卡住了怎么报

把这三样贴进 issue，基本能一次定位：

1. `powershell -ExecutionPolicy Bypass -File install.ps1 -CheckOnly` 的完整输出
2. `python scripts\doctor.py` 的完整输出（Windows 上会多打一行「未真机验证」说明）
3. `$PSVersionTable.PSVersion`，以及 `where.exe ffmpeg node python`

**不要**为了让检查变绿去改 `deps.json` 里的 `status`、删检查项、或加 `--allow-unsandboxed`——
那是把「不知道」写成「没问题」，比没有兼容声明更坑人。
'@
    $doc = $template.Replace('__STAMP__', $stamp).Replace('__HOME__', $script:Home).Replace('__REPO__', $script:RepoRoot)
    $script:SelfCheckText = $doc
    New-Item -ItemType Directory -Force -Path $script:Home | Out-Null
    try {
        Set-Content -LiteralPath $docPath -Value $doc -Encoding UTF8
        Add-Result 'ok' "自检清单已写到 $docPath"
    } catch {
        Add-Result 'warn' "自检清单写不进去（$docPath）：$($_.Exception.Message)"
        Write-Next '把 %CUTSKILLS_HOME% 指到可写目录，例如 $env:CUTSKILLS_HOME = "D:\cutskills"，然后重跑'
    }
}

function Show-Env {
    # cap-extra: env:export
    Write-Step '把环境导进当前终端（等价于 bash 版 runtime.sh env）'
    Write-Host "`$env:CUTSKILLS_HOME = `"$($script:Home)`""
    Write-Host "`$env:NODE_PATH = `"$($script:Home)\npm\node_modules`""
    Write-Host "`$env:PYTHONPATH = `"$($script:Home)\py;$($script:RepoRoot)\scripts\vendor`""
    Write-Host "`$env:PATH = `"$($script:Home)\npm\node_modules\.bin;$($script:Home)\bin;$($script:Home)\opt\bin;`" + `$env:PATH"
    Write-Host "`$env:XDG_CACHE_HOME = `"$($script:Home)\cache`""
    Write-Host "`$env:HF_HOME = `"$($script:Home)\models\hf`""
    Write-Host "`$env:PUPPETEER_CACHE_DIR = `"$($script:Home)\cache\puppeteer`""
    Write-Host '（Windows 的 PATH 分隔符是分号，不是冒号）' -ForegroundColor DarkGray
    Add-Result 'ok' '环境变量导出片段见上（每个新终端都要重复一次）'
}

# ----------------------------------------------------------------- 入口
Write-Host ''
Write-Host 'CutSkills Windows 体检' -ForegroundColor White
Write-Host "  依赖根目录：$($script:Home)"
Write-Host "  模式：$(if ($script:DoInstall) { '安装（-Install，会写 %CUTSKILLS_HOME%）' } else { '只体检（-CheckOnly，不动任何东西）' })"
Write-Host '  说明：Windows 路径**未在 Windows 真机验证**——本脚本给出的是可照着跑的计划，不是承诺' -ForegroundColor DarkGray

if (-not (Test-Path -LiteralPath $script:Home)) {
    if ($script:DoInstall) {
        try {
            New-Item -ItemType Directory -Force -Path $script:Home | Out-Null
        } catch {
            Write-Host "✗ 建不了依赖根目录 $($script:Home)：$($_.Exception.Message)" -ForegroundColor Red
            Write-Next '换个可写目录：$env:CUTSKILLS_HOME = "D:\cutskills" 后重跑'
            exit 1
        }
    }
}

Test-Python
Test-Node
Test-Npm
Test-Ffmpeg
Test-Ffprobe
Test-MacOnlyTools
Test-HyperframesPackage
Test-PipDeps
Test-InstalledPackages
Test-Transcription
Test-BeatDetection
Test-Gsap
Test-Sandbox
Test-Fonts
Show-Env
Write-SelfCheckDoc

# ----------------------------------------------------------------- 结论
$bad = @($script:Results | Where-Object { $_.Level -eq 'bad' })
$warn = @($script:Results | Where-Object { $_.Level -eq 'warn' })
$na = @($script:Results | Where-Object { $_.Level -eq 'na' })
$ok = @($script:Results | Where-Object { $_.Level -eq 'ok' })

Write-Host ''
if ($bad.Count -eq 0) {
    Write-Host ("结论：Windows 侧检查项都过了（❌ 0 · ⚠️ {0} · ➖ {1} · ✅ {2}）" -f $warn.Count, $na.Count, $ok.Count) -ForegroundColor Green
    Write-Host '  注意：这只是「这台机器的依赖齐了」，**不是**「整套产线在 Windows 上验证通过」——' -ForegroundColor Yellow
    Write-Host '  先按 <CUTSKILLS_HOME>\windows-selfcheck.md 跑一遍最小样例，再上真项目。' -ForegroundColor Yellow
} else {
    Write-Host ("结论：还有 $($bad.Count) 项 ❌，先按上面每条下面的「下一步」处理（❌ {0} · ⚠️ {1} · ➖ {2} · ✅ {3}）" -f $bad.Count, $warn.Count, $na.Count, $ok.Count) -ForegroundColor Red
}
if (-not $script:DoInstall -and $bad.Count -gt 0) {
    Write-Host ''
    Write-Host '   装缺的：powershell -ExecutionPolicy Bypass -File install.ps1 -Install' -ForegroundColor Yellow
}
Write-Host "   自检清单：$(Join-Path $script:Home 'windows-selfcheck.md')" -ForegroundColor DarkGray

# -CheckOnly 的职责就是「给我一份能照着跑的东西」：把清单正文一并打在屏幕上，
# 免得用户还得再开一个文件。安装模式不刷屏，只给路径。
if (-not $script:DoInstall -and $script:SelfCheckText) {
    Write-Host ''
    Write-Host '---------------- Windows 首次安装自检清单（同文件也落在上面那个路径）----------------' -ForegroundColor DarkGray
    Write-Host $script:SelfCheckText
}

if ($bad.Count -gt 0) { exit 1 }
exit 0
