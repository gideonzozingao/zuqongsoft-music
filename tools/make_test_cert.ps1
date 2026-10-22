<#
.SYNOPSIS
    Creates a self-signed code-signing certificate for TESTING the signing pipeline.

.DESCRIPTION
    Puts the certificate in your Windows certificate store (CurrentUser\My) and writes its
    thumbprint to signing.env, so build.bat and build_installer.bat start signing.

    Only YOUR PC can be made to trust it (with -Trust). Everyone else still sees "Unknown
    publisher" and SmartScreen warnings. Replace it with a real certificate before publishing:
    put the real certificate's thumbprint in signing.env instead.

    Usage:  powershell -ExecutionPolicy Bypass -File tools\make_test_cert.ps1 [-Trust]
#>
param([switch]$Trust)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=Zuqongtech (TEST CERTIFICATE)" `
    -CertStoreLocation Cert:\CurrentUser\My -KeyAlgorithm RSA -KeyLength 3072 -HashAlgorithm SHA256 `
    -KeyExportPolicy NonExportable -NotAfter (Get-Date).AddYears(2)
Write-Host "Created test certificate $($cert.Thumbprint)"

$envFile = Join-Path $root "signing.env"
$lines = @()
if (Test-Path -LiteralPath $envFile) {
    $lines = Get-Content -LiteralPath $envFile | Where-Object { $_ -notmatch '^\s*SIGN_CERT_SHA1\s*=' }
}
$lines += "# Test certificate made by tools\make_test_cert.ps1 - replace with your real certificate's thumbprint"
$lines += "SIGN_CERT_SHA1=$($cert.Thumbprint)"
Set-Content -LiteralPath $envFile -Value $lines -Encoding ASCII
Write-Host "Wrote SIGN_CERT_SHA1 to $envFile"

if ($Trust) {
    # Trust it on THIS PC only, so signed builds show as Valid here. Windows asks to confirm.
    $cer = Join-Path $env:TEMP "zuqongsoft-test-signing.cer"
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
    Import-Certificate -FilePath $cer -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    Import-Certificate -FilePath $cer -CertStoreLocation Cert:\CurrentUser\TrustedPublisher | Out-Null
    Remove-Item -LiteralPath $cer
    Write-Host "This PC now trusts the test certificate."
}
Write-Host "Done. Run build_installer.bat to build signed files."
