$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.WindowState = 'Minimized'
$owner.Opacity = 0
$owner.Show()
try {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = 'Select a folder'
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog($owner) -eq 'OK') {
        [Console]::Write($dialog.SelectedPath)
    }
} finally {
    if ($null -ne $dialog) { $dialog.Dispose() }
    $owner.Dispose()
}
