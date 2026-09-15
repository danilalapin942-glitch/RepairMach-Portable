$ErrorActionPreference = 'Stop'

$runDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$inputRelative = 'inputs/retry_half_M1p20_A0.json'
$reportPath = Join-Path $runDir 'reports/retry_half_M1p20_A0_report.json'
$logPath = Join-Path $runDir 'logs/retry_half_M1p20_A0.log'
$statusPath = Join-Path $runDir 'retry_half_status.json'
$workspace = Split-Path (Split-Path $runDir -Parent) -Parent
$machline = Join-Path $workspace 'RepairMach_Portable_Candidate/engines/MachLine/machline.exe'
$started = Get-Date

@{
    state = 'running'
    case = 'retry_half_M1p20_A0'
    pid = $PID
    started_at = $started.ToString('o')
    mesh = 'MiG25_MeshFix02_coarse8_open_gondola_pad020_M1p2_half_ypos.tri'
    mirror_about = 'xz'
} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8

$env:OMP_NUM_THREADS = '1'
Push-Location -LiteralPath $runDir
try {
    $output = @($inputRelative) | & $machline 2>&1
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
$output | ForEach-Object { $_.ToString() } | Set-Content -LiteralPath $logPath -Encoding utf8

$state = 'failed'
$residualNorm = $null
$solverStatusCode = $null
if (Test-Path -LiteralPath $reportPath) {
    $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    $residualNorm = [double]$report.solver_results.residual.norm
    $solverStatusCode = [int]$report.solver_results.solver_status_code
    if ($exitCode -eq 0 -and $solverStatusCode -eq 0 -and $residualNorm -le 1.0e-5) {
        $state = 'complete'
    }
    else {
        $state = 'unreliable'
    }
}

@{
    state = $state
    case = 'retry_half_M1p20_A0'
    pid = $PID
    started_at = $started.ToString('o')
    updated_at = (Get-Date).ToString('o')
    elapsed_seconds = ((Get-Date) - $started).TotalSeconds
    exit_code = $exitCode
    solver_status_code = $solverStatusCode
    residual_norm = $residualNorm
    report = $reportPath
    log = $logPath
    mesh = 'MiG25_MeshFix02_coarse8_open_gondola_pad020_M1p2_half_ypos.tri'
    mirror_about = 'xz'
} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
