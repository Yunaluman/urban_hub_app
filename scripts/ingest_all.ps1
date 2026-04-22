param(
  [int]$BackfillYears = 5,
  [string]$EeaCities = "France",
  [int]$EeaMaxFiles = 50,
  [int]$OpenMeteoDays = 7,
  [switch]$SkipCityBikes,
  [switch]$SkipOpenAQ,
  [switch]$SkipEEA,
  [switch]$SkipNoaaParis,
  [switch]$SkipInitLake
)

$ErrorActionPreference = "Stop"

# Force UTF-8 output on Windows terminals (avoids cp1252 crashes on "✓").
try {
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  $env:PYTHONIOENCODING = "utf-8"
} catch {
  # Best-effort: keep going even if host blocks this.
}

function Run-Step([string]$Title, [scriptblock]$Cmd) {
  Write-Host ""
  Write-Host "==> $Title"
  & $Cmd
}

if ($BackfillYears -lt 1 -or $BackfillYears -gt 5) {
  throw "BackfillYears must be between 1 and 5 (requested: $BackfillYears)."
}

$currentYear = (Get-Date).Year
$startYear = $currentYear - ($BackfillYears - 1)

Run-Step "Python sanity check" { python --version }

if (-not $SkipInitLake) {
  Run-Step "Init MinIO buckets (no test data)" { python manage.py init_lake --skip-test-data }
}

if (-not $SkipNoaaParis) {
  Run-Step "Meteo NOAA Paris backfill ($startYear → $currentYear)" { python manage.py fetch_paris_meteo --start-year $startYear }
}

Run-Step "Meteo Open-Meteo (last $OpenMeteoDays day(s))" { python manage.py fetch_openmeteo --days $OpenMeteoDays }

if (-not $SkipCityBikes) {
  Run-Step "Mobility CityBikes (FR networks snapshot)" { python manage.py fetch_citybikes }
}

if (-not $SkipOpenAQ) {
  Run-Step "Pollution OpenAQ (France latest)" { python manage.py fetch_openaq }
}

if (-not $SkipEEA) {
  Run-Step "Pollution EEA historical ($BackfillYears years; cities=$EeaCities; max-files=$EeaMaxFiles)" {
    python manage.py fetch_eea_historical --cities $EeaCities --years $BackfillYears --max-files $EeaMaxFiles
  }
}

Write-Host ""
Write-Host "Done."
