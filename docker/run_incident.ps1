param(
    [Parameter(Mandatory = $true)]
    [string]$DbInstance,
    [Parameter(Mandatory = $true)]
    [string]$Region,
    [Parameter(Mandatory = $true)]
    [string]$Start,
    [Parameter(Mandatory = $true)]
    [string]$End,
    [string]$AwsProfile = "default",
    [string]$Model = "gpt-4.1"
)
$ErrorActionPreference = "Stop"
$BaseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$OutputDir = Join-Path $BaseDir "output"
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
Write-Host "[1/4] Building Docker image..."
docker build -t rds-incident-rca:latest $BaseDir
Write-Host "[2/4] Collecting AWS/RDS diagnostics..."
docker run --rm `
    -v "${OutputDir}:/app/output" `
    -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
    -e AWS_PROFILE=$AwsProfile `
    rds-incident-rca:latest `
    /app/scripts/collect_aws_rds_diag.py `
    --db-instance $DbInstance `
    --region $Region `
    --start $Start `
    --end $End `
    --period 60 `
    --out /app/output/aws_rds
Write-Host "[3/4] Collecting PostgreSQL diagnostics..."
docker run --rm `
    -v "${OutputDir}:/app/output" `
    --env-file "$BaseDir\env\pg.env" `
    rds-incident-rca:latest `
    /app/scripts/collect_pg_diag.py `
    --out /app/output/postgresql `
    --long-query-seconds 30 `
    --long-xact-seconds 120
Write-Host "[4/4] Running AI RCA analysis..."
docker run --rm `
    -v "${OutputDir}:/app/output" `
    --env-file "$BaseDir\env\openai.env" `
    rds-incident-rca:latest `
    /app/scripts/analyze_incident_ai.py `
    --aws-input /app/output/aws_rds `
    --pg-input /app/output/postgresql `
    --output /app/output/final_rca_report.md `
    --model $Model `
    --dump-evidence
Write-Host ""
Write-Host "[DONE] RCA report:"
Write-Host "$OutputDir\final_rca_report.md"
