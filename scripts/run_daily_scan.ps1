param(
    [string]$ConfigPath = "config.toml",
    [string]$DataSource = "auto",
    [switch]$SendEmail = $true
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PreferredPython = "C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe"
$PythonExe = if (Test-Path $PreferredPython) { $PreferredPython } else { (Get-Command python -ErrorAction Stop).Source }

Push-Location $ProjectRoot
try {
    $arguments = @("run_scheduled.py", "--config", $ConfigPath, "--data-source", $DataSource)
    if ($SendEmail) {
        $arguments += "--send-email"
    }
    & $PythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
