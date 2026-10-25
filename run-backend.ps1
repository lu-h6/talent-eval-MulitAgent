<#
  启动后端 API（开发模式，带热重载）。
  可在任意目录执行：脚本先切到自身所在目录，再用 --app-dir 把 backend 加入模块搜索路径，
  因此不依赖当前工作目录——直接跑 `uv run uvicorn app.main:app` 时必须先 `cd backend`，否则 import app 会失败。
#>
param(
    [int]$Port = 18080
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

uv run --project backend uvicorn app.main:app --app-dir backend --reload --reload-dir backend --host 0.0.0.0 --port $Port
