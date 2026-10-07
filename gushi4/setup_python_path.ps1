#Requires -RunAsAdministrator
<#
  把 Python 加进【系统】环境变量。

  做两件事：
    1. 新建系统变量 PYTHON_PATH = <Python 安装目录>
    2. 往系统 Path 追加两条引用：%PYTHON_PATH% 和 %PYTHON_PATH%\Scripts

  为什么不是直接写死绝对路径：留一层变量，将来 Python 升级到大版本，
  改 PYTHON_PATH 一处就行，不用去 PATH 里翻。

  ⚠️ 注意变量名是 PYTHON_PATH（**带下划线**）。
     别改成 PYTHONPATH —— 那是 Python 自己认的变量，改了会影响模块导入行为。

  ⚠️ 两个必须做对的地方：
     · 系统 Path 是 REG_EXPAND_SZ 类型，里面含 %JAVA_HOME%\bin 这类引用。
       写回时必须保持 ExpandString 类型，否则那些引用会退化成字面字符串，
       系统就找不到 java 之类的东西了。（setx 会截断到 1024 字符，
       [Environment]::SetEnvironmentVariable 会写成 REG_SZ —— 两个都不能用。）
     · 追加而不是覆盖，并且先备份。

  撤销：脚本会在同目录留一份 env-backup-<时间戳>.txt。
        里面有原始的 Path 值和类型，按那个还原即可。
#>

$ErrorActionPreference = 'Stop'

$PyDir = 'C:\Users\13261\AppData\Local\Programs\Python\Python312'
$SubKey = 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'

function Say($msg, $color = 'Gray') { Write-Host $msg -ForegroundColor $color }

Say ''
Say '  gushi4 - 把 Python 加进系统环境变量' 'White'
Say ('  ' + ('=' * 58)) 'DarkGray'
Say ''

# ---------- 0. 先确认 Python 真在那儿 ----------
if (-not (Test-Path (Join-Path $PyDir 'python.exe'))) {
    Say "  [x] 找不到 $PyDir\python.exe" 'Red'
    Say '      脚本里的 $PyDir 写死了。改一下再跑。' 'Red'
    exit 1
}
Say "  Python 目录：$PyDir" 'Gray'
Say ''

# ---------- 1. 读原始值（不展开 %VAR%）----------
$rk = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($SubKey, $true)
if (-not $rk) { Say '  [x] 打不开注册表项' 'Red'; exit 1 }

$oldPath = $rk.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$pathKind = $rk.GetValueKind('Path')
$oldPy = $rk.GetValue('PYTHON_PATH', $null)

Say "  系统 Path 类型：$pathKind" 'DarkGray'
if ($oldPy) { Say "  已有 PYTHON_PATH：$oldPy" 'DarkGray' }
Say ''

# ---------- 2. 算出新值（幂等）----------
$newPath = $oldPath.TrimEnd(';')
$added = @()
foreach ($entry in @('%PYTHON_PATH%', '%PYTHON_PATH%\Scripts')) {
    $hit = ($newPath -split ';') | Where-Object { $_.Trim().ToLower() -eq $entry.ToLower() }
    if (-not $hit) { $newPath += ";$entry"; $added += $entry }
}

if ($added.Count -eq 0 -and $oldPy -eq $PyDir) {
    Say '  已经配好了，什么都没改。' 'Green'
    $rk.Close()
    exit 0
}

# ---------- 3. 给用户看清楚要改什么 ----------
Say '  将要写入：' 'White'
Say ''
Say "    PYTHON_PATH (系统变量, REG_SZ) = $PyDir" 'Cyan'
Say "    系统 Path  追加 $($added -join '  ' )" 'Cyan'
Say ''
Say '  改动后的完整系统 Path：' 'DarkGray'
foreach ($seg in ($newPath -split ';')) {
    if ($seg.Trim()) {
        $mark = if ($added -contains $seg) { ' + ' } else { '   ' }
        Say "  $mark$seg" $(if ($added -contains $seg) { 'Green' } else { 'DarkGray' })
    }
}
Say ''
$ans = Read-Host '  确认写入？(y/N)'
if ($ans -notmatch '^[yY]') { Say '  已取消。'; $rk.Close(); exit 0 }

# ---------- 4. 备份 ----------
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $PSScriptRoot "env-backup-$stamp.txt"
@(
    "备份时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    "注册表项: HKLM\$SubKey"
    "Path 类型: $pathKind"
    "Path 原值:"
    $oldPath
    ""
    "PYTHON_PATH 原值: $oldPy"
) | Set-Content -Path $backup -Encoding UTF8
Say "  已备份 -> $backup" 'DarkGray'

# ---------- 5. 写入 ----------
# Path 必须维持 ExpandString：里面那些 %JAVA_HOME% 全靠它
$rk.SetValue('Path', $newPath, [Microsoft.Win32.RegistryValueKind]::ExpandString)
if ($oldPy -ne $PyDir) {
    $rk.SetValue('PYTHON_PATH', $PyDir, [Microsoft.Win32.RegistryValueKind]::String)
}
$rk.Close()

# ---------- 6. 广播环境变更，新开的进程立刻生效 ----------
Add-Type -Namespace Win32 -Name Native -MemberDefinition @'
[DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam,
    string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@ -ErrorAction SilentlyContinue
$res = [UIntPtr]::Zero
[Win32.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero,
    'Environment', 2, 5000, [ref]$res) | Out-Null

# ---------- 7. 回读验证 ----------
$rk = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey($SubKey, $false)
$verifyPath = $rk.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
$verifyKind = $rk.GetValueKind('Path')
$verifyPy = $rk.GetValue('PYTHON_PATH', $null)
$rk.Close()

Say ''
Say '  验证：' 'White'
Say "    PYTHON_PATH = $verifyPy"
Say "    Path 类型   = $verifyKind  $(if ($verifyKind -eq 'ExpandString') { '(正确)' } else { '(!! 不对，应该还原备份)' })"
Say "    Path 含 %PYTHON_PATH%        : $(($verifyPath -split ';') -contains '%PYTHON_PATH%')"
Say "    Path 含 %PYTHON_PATH%\Scripts: $(($verifyPath -split ';') -contains '%PYTHON_PATH%\Scripts')"
Say "    Path 仍含 %JAVA_HOME%\bin    : $(($verifyPath -split ';') -contains '%JAVA_HOME%\bin')  $(if (($verifyPath -split ';') -contains '%JAVA_HOME%\bin') { '(没被破坏)' } else { '(!! 被破坏了)' })"
# ---------- 8. API key ----------
# Python 能用之后，双击 start.bat 还会卡在下一关：start.py 会检查 API key。
# 那个 key 通常只存在于你当时那个终端会话里，没有持久化——新开的窗口看不到它。
Say ''
Say '  ── API key ──' 'White'
Say ''

$keyName = 'DEEPSEEK_API_KEY'
$existing = [Environment]::GetEnvironmentVariable($keyName, 'Machine')
if (-not $existing) { $existing = [Environment]::GetEnvironmentVariable($keyName, 'User') }

if ($existing) {
    $mask = $existing.Substring(0, [Math]::Min(6, $existing.Length))
    Say "  已经有 $keyName = $mask...  跳过" 'Green'
} else {
    Say '  没有持久化的 DEEPSEEK_API_KEY。' 'Yellow'
    Say '  不设的话，双击 start.bat 会在"检查 API key"那一步退出。' 'Yellow'
    Say ''
    Say '  提示：在你现在能跑通的那个终端里执行  echo $env:DEEPSEEK_API_KEY' 'DarkGray'
    Say '        （bash 用 echo $DEEPSEEK_API_KEY）就能拿到它。' 'DarkGray'
    Say ''
    $k = Read-Host '  粘贴 key（直接回车跳过）'
    if ($k -and $k.Trim()) {
        [Environment]::SetEnvironmentVariable($keyName, $k.Trim(), 'Machine')
        Say "  已写入系统变量 $keyName" 'Green'
        $res2 = [UIntPtr]::Zero
        [Win32.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero,
            'Environment', 2, 5000, [ref]$res2) | Out-Null
    } else {
        Say '  跳过了。之后可以手动设：' 'DarkGray'
        Say "      setx /M $keyName `"sk-xxx`"" 'DarkGray'
    }
}

Say ''
Say '  完成。**新开的**终端窗口就能直接用 python 了。' 'Green'
Say '  （已经开着的窗口读的是老环境，得重开。）' 'DarkGray'
Say ''
