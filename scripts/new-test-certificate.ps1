[CmdletBinding()]
param([Parameter(Mandatory)][string]$Directory)
$ErrorActionPreference = 'Stop'
$taskCertificateDirectory = (Resolve-Path -LiteralPath $Directory).Path
$taskCertificatePath = Join-Path $taskCertificateDirectory 'fullchain.pem'
$taskKeyPath = Join-Path $taskCertificateDirectory 'privkey.pem'
if ((Test-Path -LiteralPath $taskCertificatePath) -or (Test-Path -LiteralPath $taskKeyPath)) { throw '不会覆盖已有证书或私钥。' }
$taskRsa = [Security.Cryptography.RSA]::Create(2048)
try {
    $taskRequest = [Security.Cryptography.X509Certificates.CertificateRequest]::new('CN=localhost', $taskRsa, [Security.Cryptography.HashAlgorithmName]::SHA256, [Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $taskSan = [Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
    $taskSan.AddDnsName('localhost')
    $taskSan.AddIpAddress([Net.IPAddress]::Parse('127.0.0.1'))
    $taskRequest.CertificateExtensions.Add($taskSan.Build())
    $taskRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))
    $taskUsage = [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature -bor [Security.Cryptography.X509Certificates.X509KeyUsageFlags]::KeyEncipherment
    $taskRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new($taskUsage, $true))
    $taskOids = [Security.Cryptography.OidCollection]::new()
    $null = $taskOids.Add([Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.1'))
    $taskRequest.CertificateExtensions.Add([Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($taskOids, $false))
    $taskCertificate = $taskRequest.CreateSelfSigned([DateTimeOffset]::UtcNow.AddMinutes(-5), [DateTimeOffset]::UtcNow.AddDays(7))
    try {
        [IO.File]::WriteAllText($taskCertificatePath, $taskCertificate.ExportCertificatePem(), [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllText($taskKeyPath, $taskRsa.ExportPkcs8PrivateKeyPem(), [Text.UTF8Encoding]::new($false))
    } finally { $taskCertificate.Dispose() }
} finally { $taskRsa.Dispose() }
Write-Output '七天有效的 localhost 测试证书已生成；未导入系统信任库，不能作为公网受信任证书。'
