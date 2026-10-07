param([Parameter(ValueFromRemainingArguments = $true)][string[]] $ResearchArgs)

$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if (-not (Get-Command g++,clang++,cl -ErrorAction SilentlyContinue)) {
        throw "Use an x64 Native Tools prompt or configure GCC/Clang on PATH."
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m autohls research @ResearchArgs
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m autohls research @ResearchArgs
    } else {
        throw "Python 3.10 or higher is required."
    }
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
