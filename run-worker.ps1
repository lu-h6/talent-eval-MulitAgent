<#
  启动本地 worker（消费 talent:parse:queue / talent:index:queue）。
  可在任意目录执行：脚本会先切到 backend/ 再运行。
  app 是源码包，且日志目录 logs/ 相对当前工作目录解析，因此必须在 backend/ 下运行。
  运行前请先启动中间件容器（postgres / redis / minio / milvus）。
#>
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "backend")

uv run python -m app.worker
