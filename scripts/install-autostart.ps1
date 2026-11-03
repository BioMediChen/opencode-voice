# 在“启动”文件夹创建语音守护进程的快捷方式（登录后自动运行，无窗口）。
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "start-voice-silent.cmd"
$startup = [Environment]::GetFolderPath("Startup")
$link = Join-Path $startup "OpenCode Voice.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = "OpenCode offline voice control"
$shortcut.Save()

Write-Host "created: $link"
