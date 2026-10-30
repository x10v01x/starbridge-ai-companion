#requires -Version 5.1
param([switch]$CoreOnly)
$ErrorActionPreference = 'Stop'

function Get-TargetModels {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ Root = $null; IsArray = $true; Models = @() }
    }
    $raw = [IO.File]::ReadAllText($Path)
    if ([string]::IsNullOrWhiteSpace($raw)) { throw '已有配置为空。请先确认文件内容，助手不会覆盖未知格式。' }
    $root = ConvertFrom-Json -InputObject $raw
    if ($raw.TrimStart().StartsWith('[')) {
        return [pscustomobject]@{ Root = $root; IsArray = $true; Models = @($root) }
    }
    if ($null -ne $root -and $null -ne $root.PSObject.Properties['models'] -and $root.models -is [array]) {
        return [pscustomobject]@{ Root = $root; IsArray = $false; Models = @($root.models) }
    }
    throw '无法识别现有模型文件，未修改任何配置。请联系服务方处理。'
}

function Write-UserPrivateFile {
    param([string]$Path, [string]$Text)
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) { [IO.Directory]::CreateDirectory($parent) | Out-Null }
    # Create with restrictive ACL before writing any credential bytes.
    if (-not (Test-Path -LiteralPath $Path)) { [IO.File]::WriteAllText($Path, '') }
    $acl = New-Object Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)
    $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
}

function Install-WorkBuddyConfiguration {
    param([string]$Path, [object[]]$Models)
    if (-not $Models -or $Models.Count -eq 0) { throw '没有选择可用模型。' }
    $Path = [IO.Path]::GetFullPath($Path)
    if ([IO.Path]::GetFileName($Path) -ne 'models.json') { throw '目标文件名必须为 models.json。' }
    if ((Test-Path -LiteralPath $Path) -and ((Get-Item -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '目标是文件链接，请确认真实文件后手动配置。' }
    $parent = Split-Path -Parent $Path
    if ((Test-Path -LiteralPath $parent) -and ((Get-Item -LiteralPath $parent).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '配置目录是链接，请确认真实路径后手动配置。' }
    $existing = Get-TargetModels -Path $Path
    $ids = @($Models | ForEach-Object { [string]$_.id })
    $targetUrls = @($Models | ForEach-Object { ([string]$_.url).TrimEnd('/') } | Select-Object -Unique)
    $merged = New-Object 'System.Collections.Generic.List[object]'
    foreach ($item in $existing.Models) {
        if ($null -eq $item) { continue }
        $sameId = $ids -contains [string]$item.id
        $sameUrl = $targetUrls -contains ([string]$item.url).TrimEnd('/')
        if ($sameId -and -not $sameUrl) { throw ('已有其他供应商使用相同模型 ID：' + $item.id + '。为保护原配置，本次未写入，请联系服务方。') }
        if (-not ($sameId -and $sameUrl)) { $merged.Add($item) }
    }
    foreach ($model in $Models) { $merged.Add($model) }
    if ($existing.IsArray) { $newText = ConvertTo-Json -InputObject $merged.ToArray() -Depth 40 }
    else {
        $existing.Root.models = $merged.ToArray()
        if ($null -ne $existing.Root.PSObject.Properties['availableModels']) {
            $existing.Root.availableModels = @(@($existing.Root.availableModels) + $ids | Select-Object -Unique)
        }
        $newText = ConvertTo-Json -InputObject $existing.Root -Depth 40
    }
    # Validate before touching disk. Backups and atomic replacement protect the original.
    $null = ConvertFrom-Json -InputObject $newText
    $backup = $null
    if (Test-Path -LiteralPath $Path) {
        $backup = $Path + '.starbridge-backup.' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-ffff')
        Write-UserPrivateFile -Path $backup -Text ([IO.File]::ReadAllText($Path))
    }
    $staging = $Path + '.starbridge-staging.' + [Guid]::NewGuid().ToString('N')
    Write-UserPrivateFile -Path $staging -Text $newText
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($staging, $Path, [NullString]::Value) }
    else { [IO.File]::Move($staging, $Path) }
    return [pscustomobject]@{ Path = $Path; Backup = $backup; Count = $Models.Count }
}

function Restore-WorkBuddyConfiguration {
    param([string]$Path)
    $Path = [IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $Path
    $prefix = [IO.Path]::GetFileName($Path) + '.starbridge-backup.'
    $backup = Get-ChildItem -LiteralPath $parent -File | Where-Object { $_.Name.StartsWith($prefix) } | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $backup) { throw '未找到助手生成的历史备份。' }
    $null = Get-TargetModels -Path $backup.FullName
    return $backup
}

if ($CoreOnly) { return }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Windows.Forms.Application]::EnableVisualStyles()
$profilePath = Join-Path $PSScriptRoot 'portal-profile.json'
if (-not (Test-Path -LiteralPath $profilePath)) { throw '请将 ZIP 中所有文件完整解压后再运行。' }
$profile = ConvertFrom-Json ([IO.File]::ReadAllText($profilePath))
$base = ([string]$profile.portal_url).TrimEnd('/')
$uri = [Uri]$base
if (($uri.Scheme -ne 'https' -and -not ($uri.Scheme -eq 'http' -and $uri.IsLoopback)) -or $uri.UserInfo -or $uri.AbsolutePath -ne '/' -or $uri.Query -or $uri.Fragment) { throw '请输入 HTTPS 根地址；仅本机测试可使用 HTTP。' }
$script:webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$script:user = $null
$script:models = @()
$script:busy = $false

function Invoke-Portal {
    param([string]$Endpoint, [object]$Body = $null)
    $headers = @{ Accept = 'application/json' }
    $args = @{ Uri = $base + '/api/' + $Endpoint; WebSession = $script:webSession; Headers = $headers; TimeoutSec = 25; UseBasicParsing = $true }
    if ($null -ne $Body) {
        $args.Method = 'POST'; $headers.Origin = $base; $headers.'X-Portal-Request' = '1'
        $args.ContentType = 'application/json; charset=utf-8'
        $args.Body = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Body -Depth 40 -Compress))
    }
    try { return ConvertFrom-Json (Invoke-WebRequest @args).Content }
    catch {
        $message = '无法连接服务，请检查网络或重新登录。'
        if ($_.ErrorDetails.Message) {
            try { $d = ConvertFrom-Json $_.ErrorDetails.Message; if ($d.detail -is [string]) { $message = $d.detail } } catch { }
        }
        throw $message
    }
}

$form = New-Object Windows.Forms.Form
$form.Text = $profile.brand + ' · WorkBuddy 接入助手'
$form.Size = New-Object Drawing.Size(610, 770)
$form.MinimumSize = New-Object Drawing.Size(610, 770)
$form.StartPosition = 'CenterScreen'
$form.BackColor = [Drawing.ColorTranslator]::FromHtml('#f6f8f1')
$form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 9)

function Label-At([string]$Text, [int]$X, [int]$Y, [int]$W, [int]$H) {
    $l = New-Object Windows.Forms.Label; $l.Text = $Text; $l.Location = New-Object Drawing.Point($X,$Y); $l.Size = New-Object Drawing.Size($W,$H); $form.Controls.Add($l); return $l
}
function Button-At([string]$Text, [int]$X, [int]$Y, [int]$W) {
    $b = New-Object Windows.Forms.Button; $b.Text = $Text; $b.Location = New-Object Drawing.Point($X,$Y); $b.Size = New-Object Drawing.Size($W,36); $b.FlatStyle = 'Flat'; $form.Controls.Add($b); return $b
}
function Text-At([int]$X, [int]$Y, [int]$W) {
    $t = New-Object Windows.Forms.TextBox; $t.Location = New-Object Drawing.Point($X,$Y); $t.Size = New-Object Drawing.Size($W,28); $form.Controls.Add($t); return $t
}
$title = Label-At ($profile.brand + '，连接你的好想法。') 25 22 545 35
$title.Font = New-Object Drawing.Font('Microsoft YaHei UI', 17, [Drawing.FontStyle]::Bold)
$null = Label-At ('服务地址：' + $base) 27 63 545 23
$null = Label-At '账户' 27 100 70 22
$username = Text-At 100 97 165
$null = Label-At '密码' 282 100 55 22
$password = Text-At 340 97 225
$password.UseSystemPasswordChar = $true
$null = Label-At '二次验证码（未开启可留空）' 27 140 240 22
$code = Text-At 280 137 125
$loginButton = Button-At '登录账户' 420 133 145
$balance = Label-At '登录后显示本站积分余额' 27 187 525 42
$balance.Font = New-Object Drawing.Font('Microsoft YaHei UI', 19, [Drawing.FontStyle]::Bold)
$balance.ForeColor = [Drawing.ColorTranslator]::FromHtml('#173e36')
$updated = Label-At '余额来自本站钱包，与 WorkBuddy 官方积分分开记账。' 28 233 530 25
$refresh = Button-At '刷新余额' 27 269 165
$recharge = Button-At '充值 / 查看消费记录' 212 269 170
$logout = Button-At '退出登录' 400 269 165
$null = Label-At '选择要配置的模型' 27 326 530 25
$modelList = New-Object Windows.Forms.CheckedListBox
$modelList.Location = New-Object Drawing.Point(27,355); $modelList.Size = New-Object Drawing.Size(538,140); $modelList.CheckOnClick = $true
$form.Controls.Add($modelList)
$null = Label-At '配置文件（默认 WorkBuddy；安装后如路径不同可手动修改）' 27 505 540 23
$target = Text-At 27 533 538
$target.Text = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.workbuddy\models.json'
$configure = Button-At '一键配置 WorkBuddy' 27 575 255
$configure.BackColor = [Drawing.ColorTranslator]::FromHtml('#173e36'); $configure.ForeColor = [Drawing.Color]::White
$restore = Button-At '恢复最近备份' 305 575 260
$status = Label-At '请使用购买积分时的本站账户登录。配置不会结束正在运行的任务。' 27 629 538 60
$status.ForeColor = [Drawing.ColorTranslator]::FromHtml('#66725f')
$null = Label-At '配置后请保存任务并自行重启 WorkBuddy，在自定义模型中选择本站模型。' 27 690 540 35
$refresh.Enabled = $false; $configure.Enabled = $false; $logout.Enabled = $false

function Set-Status([string]$Message, [bool]$ErrorState = $false) {
    $status.Text = $Message
    $status.ForeColor = if ($ErrorState) { [Drawing.Color]::Firebrick } else { [Drawing.ColorTranslator]::FromHtml('#66725f') }
}
function Refresh-Wallet {
    $u = Invoke-Portal 'me'; $script:user = $u
    $balance.Text = ('{0:N0} 积分' -f [double]$u.balance)
    $updated.Text = '已同步 ' + [DateTime]::Now.ToString('HH:mm:ss') + ' · 本站钱包 · 累计使用 ' + $u.used
}
function Run-Action([scriptblock]$Action) {
    if ($script:busy) { return }
    $script:busy = $true; $form.UseWaitCursor = $true
    try { & $Action }
    catch { Set-Status ([string]$_.Exception.Message) $true }
    finally { $script:busy = $false; $form.UseWaitCursor = $false }
}

$loginButton.Add_Click({ Run-Action {
    Set-Status '正在登录，请稍候…'
    $script:webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $r = Invoke-Portal 'login' @{ username = $username.Text.Trim(); password = $password.Text; code = $code.Text.Trim() }
    if ($r.requires_2fa) { Set-Status '此账户需要二次验证码，填写后再次点击登录。'; return }
    $password.Clear(); $code.Clear()
    Refresh-Wallet
    $script:models = @((Invoke-Portal 'models').models)
    $modelList.Items.Clear()
    foreach ($m in $script:models) { $index = $modelList.Items.Add(($m.name + '  [' + $m.id + ']')); $modelList.SetItemChecked($index, [bool]$m.recommended) }
    $refresh.Enabled = $true; $configure.Enabled = $true; $logout.Enabled = $true
    Set-Status '登录成功。确认模型和配置路径后，点击“一键配置 WorkBuddy”。'
} })
$refresh.Add_Click({ Run-Action { Refresh-Wallet; Set-Status '余额已更新。' } })
$recharge.Add_Click({ Start-Process ($base + '/') })
$logout.Add_Click({ Run-Action {
    $null = Invoke-Portal 'logout' @{}
    $script:user = $null; $script:models = @(); $modelList.Items.Clear()
    $refresh.Enabled = $false; $configure.Enabled = $false; $logout.Enabled = $false
    $balance.Text = '已退出登录'; $updated.Text = '登录后重新读取本站钱包。'
    Set-Status '已退出。WorkBuddy 中的专属 Key 仍保留，需要停用时请联系服务方。'
} })
$configure.Add_Click({ Run-Action {
    $selected = @($modelList.CheckedIndices | ForEach-Object { $script:models[$_].id })
    if ($selected.Count -eq 0) { throw '请至少勾选一个模型。' }
    $path = [IO.Path]::GetFullPath($target.Text.Trim())
    $answer = [Windows.Forms.MessageBox]::Show(('将备份并合并配置到：' + [Environment]::NewLine + $path + [Environment]::NewLine + '配置包含你的专属密钥。是否继续？'), '确认配置', 'YesNo', 'Question')
    if ($answer -ne 'Yes') { return }
    Set-Status '正在获取你的专属配置…'
    $config = Invoke-Portal 'configuration' @{ models = $selected }
    $result = Install-WorkBuddyConfiguration -Path $path -Models @($config.models)
    Set-Status ('已配置 ' + $result.Count + ' 个模型。请保存任务并自行重启 WorkBuddy。' + $(if ($result.Backup) { '原配置已备份。' } else { '已创建新的模型文件。' }))
    [Windows.Forms.MessageBox]::Show('配置完成。请在保存任务后重启 WorkBuddy，并在自定义模型中选择带本站名称的模型。助手不会强制退出 WorkBuddy。', '配置成功', 'OK', 'Information') | Out-Null
} })
$restore.Add_Click({ Run-Action {
    $path = [IO.Path]::GetFullPath($target.Text.Trim())
    $b = Restore-WorkBuddyConfiguration -Path $path
    $answer = [Windows.Forms.MessageBox]::Show(('将使用此备份恢复：' + [Environment]::NewLine + $b.FullName + [Environment]::NewLine + '当前配置会先另存一份，是否继续？'), '确认恢复', 'YesNo', 'Warning')
    if ($answer -ne 'Yes') { return }
    if (Test-Path -LiteralPath $path) { Write-UserPrivateFile -Path ($path + '.before-restore.' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss-ffff')) -Text ([IO.File]::ReadAllText($path)) }
    $stage = $path + '.restore-staging.' + [Guid]::NewGuid().ToString('N')
    Write-UserPrivateFile -Path $stage -Text ([IO.File]::ReadAllText($b.FullName))
    if (Test-Path -LiteralPath $path) { [IO.File]::Replace($stage,$path,[NullString]::Value) } else { [IO.File]::Move($stage,$path) }
    Set-Status '恢复完成。请保存任务并自行重启 WorkBuddy。'
} })
$timer = New-Object Windows.Forms.Timer; $timer.Interval = 20000
$timer.Add_Tick({ if ($script:user -and -not $script:busy) { Run-Action { Refresh-Wallet } } })
$timer.Start()
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose(); $password.Clear() })
$null = $form.ShowDialog()
