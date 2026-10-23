$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

function Add-Label($form, $text, $x, $y) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $text
    $label.AutoSize = $true
    $label.Location = New-Object System.Drawing.Point($x, $y)
    $form.Controls.Add($label)
}

function Add-PasswordBox($form, $x, $y, $width) {
    $box = New-Object System.Windows.Forms.MaskedTextBox
    $box.Location = New-Object System.Drawing.Point($x, $y)
    $box.Width = $width
    $box.PasswordChar = '*'
    $form.Controls.Add($box)
    return $box
}

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Create your HomeConnect account'
$form.Width = 470
$form.Height = 285
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
Add-Label $form 'PostgreSQL password (user postgres):' 12 14
$dbPasswordBox = Add-PasswordBox $form 15 38 420
Add-Label $form 'New HomeConnect password (minimum 12 characters):' 12 78
$accountPasswordBox = Add-PasswordBox $form 15 102 420
Add-Label $form 'Confirm new HomeConnect password:' 12 142
$confirmPasswordBox = Add-PasswordBox $form 15 166 420
$okButton = New-Object System.Windows.Forms.Button
$okButton.Text = 'Create account'
$okButton.Location = New-Object System.Drawing.Point(300, 207)
$okButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($okButton)
$cancelButton = New-Object System.Windows.Forms.Button
$cancelButton.Text = 'Cancel'
$cancelButton.Location = New-Object System.Drawing.Point(385, 207)
$cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancelButton)
$form.AcceptButton = $okButton
$form.CancelButton = $cancelButton

if ($form.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
    $form.Dispose()
    throw 'Account setup cancelled.'
}
$dbPassword = $dbPasswordBox.Text
$accountPassword = $accountPasswordBox.Text
$confirmPassword = $confirmPasswordBox.Text
$dbPasswordBox.Clear()
$accountPasswordBox.Clear()
$confirmPasswordBox.Clear()
$form.Dispose()
if ([string]::IsNullOrWhiteSpace($dbPassword) -or [string]::IsNullOrEmpty($accountPassword)) {
    throw 'Both passwords are required.'
}
if ($accountPassword -cne $confirmPassword) {
    throw 'The HomeConnect passwords do not match.'
}
$confirmPassword = $null

$encodedDbPassword = [System.Uri]::EscapeDataString($dbPassword)
$dbPassword = $null
$env:DATABASE_URL = "postgresql://postgres:$encodedDbPassword@127.0.0.1:5432/postgres"
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_SECRET_KEY = 'development-key-only'
$env:PYTHONPATH = Join-Path $PSScriptRoot '.runtime-packages'
$env:TERM_PROGRAM = 'vscode'
$env:HC_OWNER_EMAIL = 'tarusjesse33@gmail.com'
$env:HC_OWNER_PASSWORD = $accountPassword
$accountPassword = $null

$bundledPython = 'C:\Users\HP TOUCH SMART\.cache\codex-runtimes\codex-primary-runtime\dependencies\python'
if (Test-Path (Join-Path $bundledPython 'python.exe')) {
    $env:PATH = "$bundledPython;$env:PATH"
}

Set-Location $PSScriptRoot
& python manage.py bootstrap_owner
$exitCode = $LASTEXITCODE
Remove-Item Env:DATABASE_URL,Env:DJANGO_SECRET_KEY,Env:DJANGO_DEBUG,Env:PYTHONPATH,Env:TERM_PROGRAM,Env:HC_OWNER_EMAIL,Env:HC_OWNER_PASSWORD -ErrorAction SilentlyContinue
if ($exitCode -ne 0) {
    throw "HomeConnect account setup failed (exit code $exitCode)."
}
