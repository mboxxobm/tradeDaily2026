# update_gold_csv.ps1 - download latest GOLD_retail_daily.csv into MT5 MQL5\Files
# Usage: powershell -ExecutionPolicy Bypass -File update_gold_csv.ps1 -Dest "C:\...\MQL5\Files"
param(
  [string]$Dest = "$env:APPDATA\MetaQuotes\Terminal\Common\Files",
  [string]$Url  = "https://raw.githubusercontent.com/mboxxobm/tradeDaily2026/main/GOLDandForex/GOLD_retail_daily.csv"
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not (Test-Path $Dest)) { New-Item -ItemType Directory -Path $Dest -Force | Out-Null }
$tmp = Join-Path $Dest "GOLD_retail_daily.csv.tmp"
$out = Join-Path $Dest "GOLD_retail_daily.csv"
Invoke-WebRequest -Uri $Url -OutFile $tmp -UseBasicParsing
if ((Get-Item $tmp).Length -gt 100) { Move-Item -Force $tmp $out; Write-Host "Updated $out" }
else { Remove-Item $tmp; throw "Downloaded file too small" }
