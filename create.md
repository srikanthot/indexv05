$zipPath = "C:\AzureIndex\function_app\function_app_deploy.zip.zip"
$publishProfilePath = "$env:USERPROFILE\Downloads\azureindex-function.PublishSettings"

[xml]$xml = Get-Content $publishProfilePath

$profile = $xml.publishData.publishProfile |
    Where-Object { $_.publishMethod -eq "ZipDeploy" } |
    Select-Object -First 1

if (-not $profile) {
    $profile = $xml.publishData.publishProfile | Select-Object -First 1
}

$user = $profile.userName
$pass = $profile.userPWD
$publishUrl = $profile.publishUrl.Split(":")[0]

$pair = "$user`:$pass"
$basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))

Invoke-WebRequest `
  -Uri "https://$publishUrl/api/zipdeploy?isAsync=true" `
  -Method POST `
  -InFile $zipPath `
  -Headers @{ Authorization = "Basic $basic" } `
  -ContentType "application/zip"
