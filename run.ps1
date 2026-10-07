$ErrorActionPreference = "Stop"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 "$PSScriptRoot\server.py" @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python "$PSScriptRoot\server.py" @args
} else {
    throw "未找到 Python 3，请先安装 Python 3.10 或更高版本。"
}
