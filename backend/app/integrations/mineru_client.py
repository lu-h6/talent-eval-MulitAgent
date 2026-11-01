"""MinerU 解析服务客户端：提交文档并取回解析产物。"""

import json
import logging
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.documents.document_parsers import ParseResult


logger = logging.getLogger(__name__)


def parse_with_mineru(path: Path) -> ParseResult:
    """调用 MinerU 文档解析服务，把文档解析为 Markdown 与结构化内容。

    步骤：
      1) 以 multipart 方式上传文件到 MinerU 的 /file_parse 接口；
      2) 校验返回状态并解析 content_list 为结构化数据；
      3) 组装成 ParseResult 返回。

    Args:
        path: 待解析文件的本地路径。

    Returns:
        包含 Markdown 文本、结构化内容与元信息的 ParseResult。

    Raises:
        RuntimeError: 当 MinerU 未返回成功的解析结果时抛出。
    """
    logger.info("mineru_parse_started file=%s size_bytes=%s", path.name, path.stat().st_size)
    # 以文件上传方式调用 MinerU：请求返回 Markdown 与 content_list（结构化正文列表）
    with path.open("rb") as handle:
        response = httpx.post(
            f"{get_settings().mineru_url}/file_parse",
            files={"files": (path.name, handle, "application/octet-stream")},
            data={"backend": "pipeline", "return_md": "true", "return_content_list": "true", "response_format_zip": "false"},
            timeout=900,  # 解析大文件耗时较长，使用较长超时
        )
    response.raise_for_status()  # HTTP 层错误直接抛出
    payload = response.json()

    # 校验任务状态与结果是否存在
    if payload.get("status") != "completed" or not payload.get("results"):
        logger.error("mineru_parse_invalid_response task_id=%s status=%s error=%s", payload.get("task_id"), payload.get("status"), payload.get("error"))
        raise RuntimeError(payload.get("error") or "MinerU 未返回解析结果")

    # results 是 {原始文件名: 结果} 的映射，取第一项作为本次解析结果
    name, result = next(iter(payload["results"].items()))
    # content_list 可能是 JSON 字符串，需反序列化为结构体
    content_list = result.get("content_list", "[]")
    structured = json.loads(content_list) if isinstance(content_list, str) else content_list

    # 组装统一解析结果对象
    result_value = ParseResult(
        "mineru_pipeline_3.4.4",  # 解析器名称/版本
        result.get("md_content", ""),  # Markdown 正文
        {"task_id": payload.get("task_id"), "source_name": name, "backend": payload.get("backend"), "version": payload.get("version")},  # 元信息
        {"content_list": structured},  # 结构化正文列表
    )
    logger.info("mineru_parse_completed task_id=%s source_name=%s text_chars=%s", payload.get("task_id"), name, len(result_value.text))
    return result_value