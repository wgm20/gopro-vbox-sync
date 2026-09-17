$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$projectDir = Split-Path $PSScriptRoot -Parent
$toolsDir = Join-Path $projectDir '.build-tools'
$compilerDir = Join-Path $toolsDir 'InnoSetup'
if (Test-Path (Join-Path $compilerDir 'ISCC.exe')) { return }
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
$installerPath = Join-Path $toolsDir 'innosetup-6.7.3.exe'
Invoke-WebRequest 'https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe' -OutFile $installerPath
if ((Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash -ne '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732') { throw 'Inno Setup hash mismatch' }
$arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /NOICONS /DIR="' + $compilerDir + '"'
$process = Start-Process -FilePath $installerPath -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Inno Setup installation failed: $($process.ExitCode)" }
