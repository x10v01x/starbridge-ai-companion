param([string]$OutputName = "Starbridge-Assistant.exe")
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$framework = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319'
$output = Join-Path $root ('dist/'+$OutputName)
New-Item -ItemType Directory -Force (Split-Path -Parent $output) | Out-Null
$refs = @('System.dll','System.Core.dll','System.Net.Http.dll','System.Security.dll','System.Web.Extensions.dll','System.Xaml.dll','WPF/WindowsBase.dll','WPF/PresentationCore.dll','WPF/PresentationFramework.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$icons = @('dashboard','wallet','help','connect','history','star') | ForEach-Object { '/resource:' + (Join-Path $root ('static/icons/' + $_ + '.png')) + ',icons.' + $_ + '.png' }
& (Join-Path $framework 'csc.exe') /nologo /target:winexe /platform:x64 /optimize+ ('/out:'+$output) ('/win32icon:'+(Join-Path $root 'static/icons/starbridge-app.ico')) ('/resource:'+(Join-Path $root 'static/icons/starbridge-app.png')+',icons.app.png') ('/win32manifest:'+(Join-Path $PSScriptRoot 'app.manifest')) ('/resource:'+(Join-Path $PSScriptRoot 'MainWindow.xaml')+',MainWindow.xaml') @refs @icons (Join-Path $PSScriptRoot 'AssemblyInfo.cs') (Join-Path $PSScriptRoot 'Starbridge.cs')
if ($LASTEXITCODE -ne 0) { throw 'EXE compilation failed' }
Get-FileHash $output -Algorithm SHA256
