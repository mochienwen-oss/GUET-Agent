$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runRoot = Join-Path $projectRoot 'run'
$pgData = Join-Path $runRoot 'postgres-data'

# 项目目录里的 postgres/redis 可执行文件带着沙箱留下的 Low 完整性标签，
# 以低权限运行时写不进数据目录（报错：could not create lock file "postmaster.pid": Permission denied）。
# 所以优先用 %LOCALAPPDATA%\SAI 下的纯英文副本，没有副本时再回退到项目内路径。
$asciiRoot = Join-Path $env:LOCALAPPDATA 'SAI'
$pgBin = Join-Path $asciiRoot 'pgenv/Library/bin'
if (-not (Test-Path -LiteralPath (Join-Path $pgBin 'pg_ctl.exe'))) {
    $pgBin = Join-Path $runRoot 'postgres-env/Library/bin'
}
$redisExe = Join-Path $asciiRoot 'redis/redis-server.exe'
if (-not (Test-Path -LiteralPath $redisExe)) {
    $redisExe = Join-Path $runRoot 'redis-windows/Redis-8.6.7-Windows-x64-msys2/redis-server.exe'
}
$redisPidFile = Join-Path $runRoot 'redis.pid'

if (-not (Test-Path -LiteralPath $pgData)) {
    throw '项目本地 PostgreSQL 数据目录尚未初始化'
}

# 上次被强杀可能留下 postmaster.pid，正常关闭时不会。
$stalePid = Join-Path $pgData 'postmaster.pid'
& (Join-Path $pgBin 'pg_ctl.exe') -D $pgData status *> $null
if ($LASTEXITCODE -ne 0 -and (Test-Path -LiteralPath $stalePid)) {
    try { Remove-Item -LiteralPath $stalePid -Force -ErrorAction Stop } catch {}
}

& (Join-Path $pgBin 'pg_ctl.exe') -D $pgData status *> $null
if ($LASTEXITCODE -ne 0) {
    $pgLog = Join-Path $runRoot 'postgres.log'
    $starter = Start-Process -FilePath (Join-Path $pgBin 'pg_ctl.exe') -ArgumentList @(
        '-D', $pgData, '-l', $pgLog,
        '-o', '"-h 127.0.0.1 -p 55432"', '-w', 'start'
    ) -WindowStyle Hidden -PassThru
    if (-not $starter.WaitForExit(30000) -or $starter.ExitCode -ne 0) {
        throw 'PostgreSQL 启动失败，请查看 run/postgres.log'
    }
}

# Redis 只用于公开资料目录的缓存，缺失时应用会自动回退读文件。
if (Test-Path -LiteralPath $redisExe) {
    $redisProcess = $null
    if (Test-Path -LiteralPath $redisPidFile) {
        $redisProcess = Get-Process -Id ([int](Get-Content -LiteralPath $redisPidFile -Raw)) -ErrorAction SilentlyContinue
    }
    if (-not $redisProcess -or $redisProcess.Path -ne $redisExe) {
        $socket = [System.Net.Sockets.TcpClient]::new()
        $occupied = $false
        try { $socket.Connect('127.0.0.1', 56379); $occupied = $true } catch {} finally { $socket.Dispose() }
        if ($occupied) {
            Write-Output 'Redis 端口 56379 已被占用，跳过启动'
        } else {
            $redisProcess = Start-Process -FilePath $redisExe -ArgumentList @('--bind','127.0.0.1',
                '--port','56379','--protected-mode','yes','--save','""','--appendonly','no') `
                -WorkingDirectory (Split-Path $redisExe) -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $runRoot 'redis.stdout.log') `
                -RedirectStandardError (Join-Path $runRoot 'redis.stderr.log')
            Set-Content -LiteralPath $redisPidFile -Value $redisProcess.Id
        }
    }
} else {
    Write-Output '未找到 Redis 可执行文件，跳过（应用不依赖它）'
}

Write-Output '项目内 PostgreSQL 和 Redis 已启动；应用数据库配置仍由私有 .env 决定。'
