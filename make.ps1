<#
  Windows helper: the project interface is the Makefile, but Windows has no
  make. This wrapper exposes the same targets.

      .\make.ps1 up
      .\make.ps1 check
      .\make.ps1 revision -m "add hunter tables"
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][string]$Target = "check",
    [Parameter()][Alias("m")][string]$Message = ""
)

$ErrorActionPreference = "Stop"

$compose = @("docker", "compose")
function Invoke-Compose {
    param([string[]]$ComposeArgs)
    & $compose[0] $compose[1..($compose.Length - 1)] @ComposeArgs
    if ($LASTEXITCODE -ne 0) { throw "compose falhou: $($ComposeArgs -join ' ')" }
}
function Invoke-Web {
    param([string[]]$Cmd, [switch]$WithDb)
    $prefix = if ($WithDb) { @("run", "--rm", "web") } else { @("run", "--rm", "--no-deps", "web") }
    Invoke-Compose ($prefix + $Cmd)
}

switch ($Target) {
    "up"        { Invoke-Compose @("up", "-d", "--build") }
    "down"      { Invoke-Compose @("down") }
    "logs"      { Invoke-Compose @("logs", "-f", "web", "worker") }
    "migrate"   { Invoke-Web -WithDb @("alembic", "upgrade", "head") }
    "revision"  {
        if (-not $Message) { throw 'use: .\make.ps1 revision -m "mensagem"' }
        Invoke-Web -WithDb @("alembic", "revision", "--autogenerate", "-m", $Message)
    }
    "test"      { Invoke-Web -WithDb @("pytest") }
    "lint"      {
        Invoke-Web @("ruff", "check", "app", "tests")
        Invoke-Web @("ruff", "format", "--check", "app", "tests")
    }
    "format"    {
        Invoke-Web @("ruff", "format", "app", "tests")
        Invoke-Web @("ruff", "check", "--fix", "app", "tests")
    }
    "typecheck" { Invoke-Web @("pyright") }
    "arch"      { Invoke-Web @("lint-imports") }
    "check"     {
        & $PSCommandPath lint
        & $PSCommandPath typecheck
        & $PSCommandPath arch
        & $PSCommandPath test
    }
    "seed"      { Invoke-Web -WithDb @("python", "-m", "app.shared.seed") }
    "shell"     { Invoke-Web -WithDb @("bash") }
    default     { throw "alvo desconhecido: $Target" }
}
