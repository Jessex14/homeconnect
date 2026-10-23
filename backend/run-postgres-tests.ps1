$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = 'HomeConnect PostgreSQL test login'
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ClientSize = New-Object System.Drawing.Size(390, 132)
$label = New-Object System.Windows.Forms.Label
$label.Text = 'Enter the PostgreSQL password for user postgres:'
$label.AutoSize = $true
$label.Location = New-Object System.Drawing.Point(14, 16)
$form.Controls.Add($label)
$passwordBox = New-Object System.Windows.Forms.TextBox
$passwordBox.Location = New-Object System.Drawing.Point(16, 45)
$passwordBox.Size = New-Object System.Drawing.Size(356, 24)
$passwordBox.UseSystemPasswordChar = $true
$form.Controls.Add($passwordBox)
$okButton = New-Object System.Windows.Forms.Button
$okButton.Text = 'Continue'
$okButton.Location = New-Object System.Drawing.Point(196, 88)
$okButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($okButton)
$cancelButton = New-Object System.Windows.Forms.Button
$cancelButton.Text = 'Cancel'
$cancelButton.Location = New-Object System.Drawing.Point(286, 88)
$cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancelButton)
$form.AcceptButton = $okButton
$form.CancelButton = $cancelButton
$form.Add_Shown({ $passwordBox.Focus() })
$dialogResult = $form.ShowDialog()
if ($dialogResult -ne [System.Windows.Forms.DialogResult]::OK) { throw 'Database login was cancelled.' }
$plainPassword = $passwordBox.Text
$passwordBox.Clear()
$form.Dispose()
if ([string]::IsNullOrEmpty($plainPassword)) { throw 'Database password was empty.' }
$encodedPassword = [System.Uri]::EscapeDataString($plainPassword)
$plainPassword = $null

$env:DATABASE_URL = "postgresql://postgres:$encodedPassword@127.0.0.1:5432/postgres"
$env:DJANGO_DEBUG = 'true'
$env:DJANGO_SECRET_KEY = 'test-key-only'
$env:PYTHONPATH = Join-Path $PSScriptRoot '.runtime-packages'
Remove-Item Env:DJANGO_TEST_SQLITE -ErrorAction SilentlyContinue

$bundledPython = 'C:\Users\HP TOUCH SMART\.cache\codex-runtimes\codex-primary-runtime\dependencies\python'
if (Test-Path (Join-Path $bundledPython 'python.exe')) {
    $env:PATH = "$bundledPython;$env:PATH"
}

$previousDirectory = Get-Location
$exitCode = 1
try {
    Set-Location $PSScriptRoot
    # A previous failed run can leave the temporary database open briefly.
    # Reuse the test database so reruns don't need to drop an in-use database.
    & python manage.py test --verbosity 2 --keepdb
    $exitCode = $LASTEXITCODE
}
finally {
    Set-Location $previousDirectory
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
    Remove-Item Env:DJANGO_SECRET_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:DJANGO_DEBUG -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    $encodedPassword = $null
}

exit $exitCode
