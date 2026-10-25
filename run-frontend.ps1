<#
  启动前端开发服务器（Vite，热更新）。
  可在任意目录执行：脚本先切到自身的 frontend 目录，不依赖当前工作目录。
  首次运行且没有 node_modules 时会自动执行 npm install。
#>
param(
    [int]$Port = 15173
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "frontend")

if (-not (Test-Path "node_modules")) {
    npm install
}

npm run dev -- --port $Port
