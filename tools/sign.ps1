<#
.SYNOPSIS
    Authenticode-signs Zuqongsoft Music build outputs. Used by build.bat and build_installer.bat.

.DESCRIPTION
    Signing is OFF until it's configured. Configure it with environment variables, or put the
    same KEY=VALUE lines in "signing.env" in the project root (that file is git-ignored).
    Pick ONE identity:

      SIGN_CERT_SHA1=<thumbprint>   Certificate in the Windows certificate store. This covers USB
                                    hardware tokens, cloud HSMs that install a key provider
                                    (DigiCert KeyLocker, SSL.com eSigner, ...) and the test
                                    certificate made by tools\make_test_cert.ps1.
      SIGN_PFX=<path to .pfx>       A certificate file (test certificates only: publicly trusted
      SIGN_PFX_PASSWORD=<password>  certificates must keep their key in hardware).
      SIGN_DLIB=<dll> and           Microsoft Artifact Signing (formerly Trusted Signing),
      SIGN_DMDF=<metadata.json>     if your organisation is eligible.

    Optional:
      SIGN_TIMESTAMP_URL   RFC 3161 timestamp server (default: DigiCert, with fallbacks).
      SIGNTOOL             Full path to signtool.exe (otherwise found in the Windows SDK).

.PARAMETER Files       Files to sign.
.PARAMETER BundleDir   Also sign every unsigned .exe/.dll/.pyd in this folder (e.g. libmpv-2.dll).
.PARAMETER Check       Check that signing will work (identity set, certificate installed with its
                       private key and not expired, signtool found) without signing anything.

    Exit codes: 0 = signed / ready, 1 = failed or not ready, 2 = no identity set (-Check).
    The build scripts only call this script when signing.env or SIGN_* settings exist, so a
    missing identity is reported as an error rather than silently skipped.
#>
param(
    [string]$BundleDir = "",
    [switch]$Check,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Files = @()
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# ---- settings: environment variables win over signing.env -----------------------------------
$envFile = Join-Path $root "signing.env"
if (Test-Path -LiteralPath $envFile) {
    foreach ($line in Get-Content -LiteralPath $envFile) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#")) { continue }
        if ($t -match '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name = $Matches[1]; $value = $Matches[2].Trim().Trim('"')
            if (-not (Test-Path -LiteralPath "env:$name")) { Set-Item -LiteralPath "env:$name" -Value $value }
        }
    }
}

$identity = @()
$mode = ""
if ($env:SIGN_CERT_SHA1) {
    $identity = @("/sha1", ($env:SIGN_CERT_SHA1 -replace '\s', '')); $mode = "certificate store"
}
elseif ($env:SIGN_PFX) {
    $identity = @("/f", $env:SIGN_PFX); $mode = "PFX file"
    if ($env:SIGN_PFX_PASSWORD) { $identity += @("/p", $env:SIGN_PFX_PASSWORD) }
}
elseif ($env:SIGN_DLIB -and $env:SIGN_DMDF) {
    $identity = @("/dlib", $env:SIGN_DLIB, "/dmdf", $env:SIGN_DMDF); $mode = "Artifact Signing"
}

if (-not $mode) {
    Write-Host "ERROR: Signing was requested, but no signing identity is set. signing.env (or the"
    Write-Host "environment) needs SIGN_CERT_SHA1, SIGN_PFX, or SIGN_DLIB + SIGN_DMDF."
    Write-Host "To build unsigned instead, delete signing.env."
    if ($Check) { exit 2 } else { exit 1 }
}

# ---- find signtool.exe -------------------------------------------------------------------------
function Find-SignTool {
    if ($env:SIGNTOOL -and (Test-Path -LiteralPath $env:SIGNTOOL)) { return $env:SIGNTOOL }
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (Test-Path -LiteralPath $kits) {
        $found = Get-ChildItem -LiteralPath $kits -Directory |
        Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending |
        ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
        if ($found) { return $found }
    }
    return $null
}
$signtool = Find-SignTool
if (-not $signtool) {
    Write-Host "ERROR: signtool.exe not found. Install the Windows SDK (only 'Signing Tools for Desktop Apps' is needed),"
    Write-Host "or set SIGNTOOL to the full path of signtool.exe."
    exit 1
}

# ---- -Check: make sure signing can really work before a build depends on it ---------------------
if ($Check) {
    if ($env:SIGN_CERT_SHA1) {
        $thumb = ($env:SIGN_CERT_SHA1 -replace '\s', '').ToUpper()
        $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $thumb } | Select-Object -First 1
        if (-not $cert) {
            Write-Host "ERROR: No certificate with thumbprint $thumb is installed on this PC."
            Write-Host "Run tools\make_test_cert.ps1 again, put the right thumbprint in signing.env, or delete"
            Write-Host "signing.env to build unsigned."
            exit 1
        }
        if (-not $cert.HasPrivateKey) {
            Write-Host "ERROR: The certificate $thumb has no private key on this PC (is the USB token plugged in?)."
            exit 1
        }
        if ($cert.NotAfter -lt (Get-Date)) {
            Write-Host "ERROR: The certificate $thumb expired on $($cert.NotAfter.ToString('d'))."
            exit 1
        }
        Write-Host "Signing ready: $($cert.Subject) (valid until $($cert.NotAfter.ToString('d')))"
    }
    elseif ($env:SIGN_PFX) {
        if (-not (Test-Path -LiteralPath $env:SIGN_PFX)) { Write-Host "ERROR: SIGN_PFX file not found: $env:SIGN_PFX"; exit 1 }
        Write-Host "Signing ready: $env:SIGN_PFX"
    }
    else {
        Write-Host "Signing ready: $mode"
    }
    exit 0
}

# ---- what to sign ---------------------------------------------------------------------------------
$targets = New-Object System.Collections.Generic.List[string]
foreach ($f in $Files) { if ($f) { $targets.Add((Resolve-Path -LiteralPath $f).Path) } }
if ($BundleDir -and (Test-Path -LiteralPath $BundleDir)) {
    Get-ChildItem -LiteralPath $BundleDir -Recurse -File |
    Where-Object { @(".exe", ".dll", ".pyd") -contains $_.Extension.ToLower() } |
    Where-Object { (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -eq "NotSigned" } |
    ForEach-Object { $targets.Add($_.FullName) }
}
if ($targets.Count -eq 0) { Write-Host "Nothing to sign."; exit 0 }

# ---- sign, retrying other timestamp servers if one is down ------------------------------------
$servers = @()
if ($env:SIGN_TIMESTAMP_URL) { $servers += $env:SIGN_TIMESTAMP_URL }
$servers += @("http://timestamp.digicert.com", "http://timestamp.sectigo.com",
    "http://timestamp.globalsign.com/tsa/r6advanced1")
Write-Host "Signing $($targets.Count) file(s) with $mode..."
$batchSize = 50
for ($i = 0; $i -lt $targets.Count; $i += $batchSize) {
    $chunk = $targets.GetRange($i, [Math]::Min($batchSize, $targets.Count - $i)).ToArray()
    $ok = $false
    foreach ($ts in $servers) {
        $signArgs = @("sign", "/fd", "sha256", "/tr", $ts, "/td", "sha256", "/d", "Zuqongsoft Music") + $identity + $chunk
        & $signtool @signArgs
        if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        Write-Host "Signing with timestamp server $ts failed; trying the next one..."
        Start-Sleep -Seconds 2
    }
    if (-not $ok) { Write-Host "ERROR: signing failed."; exit 1 }
}

# ---- report ------------------------------------------------------------------------------------------
$untrusted = 0
foreach ($t in $targets) {
    $sig = Get-AuthenticodeSignature -LiteralPath $t
    if ($sig.Status -eq "Valid") { continue }
    if ($sig.SignerCertificate) { $untrusted++ }          # signed, but this PC doesn't trust the certificate
    else { Write-Host "ERROR: $t is not signed ($($sig.Status))."; exit 1 }
}
$signer = (Get-AuthenticodeSignature -LiteralPath $targets[0]).SignerCertificate.Subject
Write-Host "Signed $($targets.Count) file(s). Signer: $signer"
if ($untrusted -gt 0) {
    Write-Host "Note: this PC doesn't trust the signing certificate (normal for a test certificate)."
}
exit 0