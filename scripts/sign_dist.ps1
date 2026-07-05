<#
.SYNOPSIS
    Self-signs dist/sopforge/sopforge.exe and dist/sopforge-server/sopforge-server.exe
    with a local code-signing certificate, creating that certificate (once) if it
    doesn't already exist.

.DESCRIPTION
    No paid/CA certificate is required for a local-only tool: this creates a
    self-signed code-signing cert (CN=SOPForge, Cert:\CurrentUser\My), and adds
    it to CurrentUser\Root + CurrentUser\TrustedPublisher so Windows trusts it
    on this machine specifically (removes the "Unknown Publisher" UAC prompt
    for these two EXEs here). It signs in place with Set-AuthenticodeSignature
    -- no signtool.exe / Windows SDK dependency, since Set-AuthenticodeSignature
    ships with PowerShell itself.

    A self-signed cert is trusted only on machines where it's been imported
    into Root/TrustedPublisher (this one). It does not make the EXEs trusted
    on a machine they're copied to fresh -- re-run this after copying dist/ to
    a new machine, or import scripts/sopforge-signing-cert.cer there via
    Import-Certificate -CertStoreLocation Cert:\CurrentUser\Root.

Usage: powershell -File scripts/sign_dist.ps1
#>
param(
    [string]$Subject = "CN=SOPForge"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Targets = @(
    (Join-Path $RepoRoot "dist\sopforge\sopforge.exe"),
    (Join-Path $RepoRoot "dist\sopforge-server\sopforge-server.exe")
)

$cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
    Where-Object { $_.Subject -eq $Subject -and $_.NotAfter -gt (Get-Date) } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
        -KeyUsage DigitalSignature -KeyExportPolicy Exportable `
        -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(5)
    Write-Output "Created new self-signed code-signing certificate: $($cert.Thumbprint)"

    $cerPath = Join-Path $PSScriptRoot "sopforge-signing-cert.cer"
    Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null

    foreach ($storeName in "Root", "TrustedPublisher") {
        $store = [System.Security.Cryptography.X509Certificates.X509Store]::new(
            $storeName, "CurrentUser")
        $store.Open("ReadWrite")
        $store.Add($cert)
        $store.Close()
    }
    Write-Output "Trusted it locally (CurrentUser\Root + CurrentUser\TrustedPublisher)."
} else {
    Write-Output "Reusing existing certificate: $($cert.Thumbprint)"
}

foreach ($exe in $Targets) {
    if (-not (Test-Path $exe)) {
        Write-Warning "Skipping (not built yet): $exe"
        continue
    }
    $result = Set-AuthenticodeSignature -FilePath $exe -Certificate $cert -HashAlgorithm SHA256
    if ($result.Status -ne "Valid") {
        throw "Signing failed for $exe : $($result.StatusMessage)"
    }
    Write-Output "Signed: $exe"
}
