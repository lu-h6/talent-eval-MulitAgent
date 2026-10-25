"""
文档解析模块：负责将各种格式的文件（Word、CSV、JSON、HTML、Markdown、
PPT、Excel、音频等）统一解析为结构化的 ParseResult 对象。

设计目标：
1. 对不同格式提供统一的解析入口 parse_document(path)。
2. 解析结果同时包含纯文本（text/纯文本）与结构化内容（structured/结构块），
   便于后续做向量化检索或直接消费。
3. 音频依赖 faster_whisper 做中文语音识别，模型采用懒加载（首次使用时才加载）。
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docx import Document as WordDocument
from docx.table import Table
from docx.text.paragraph import Paragraph
from langchain_community.document_loaders import BSHTMLLoader, CSVLoader, JSONLoader

from app.documents.parsers import parse_xlsx, parse_pptx

# 模块级全局变量：缓存语音识别模型，避免每次解析音频都重新加载模型（耗时长、占内存）
_whisper_model = None


@dataclass(slots=True)
class ParseResult:
    """
    统一解析结果的数据结构。

    slots=True 表示禁用 __dict__，可减少内存占用并加快属性访问。
    各字段含义：
    - parser_name: 所用解析器的名称标识（如 "python_docx_markdown"）。
    - text:        解析出的纯文本内容，用于向量化/关键词检索。
    - metadata:    附加的元信息字典（如段落数、页数等）。
    - structured:  可选的结构化表示（如内容块列表、表格行、音频片段等）。
    """

    parser_name: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] | None = None


def join_documents(documents) -> str:
    """
    将多个 langchain Document 对象拼接为一段连续文本。

    具体做法是提取每个 document 的 page_content，用空行（"\n\n"）连接，
    并过滤掉内容为空白/空的文档，避免输出大量无意义空行。

    Args:
        documents: 待拼接的 langchain Document 对象集合（可迭代）。

    Returns:
        拼接后的连续纯文本；若没有非空文档则返回空字符串。
    """
    return "\n\n".join(document.page_content for document in documents if document.page_content.strip())


def markdown_content_list(markdown: str) -> list[dict[str, Any]]:
    """
    把一段 Markdown 文本切分为结构化的内容块列表。

    切分规则：以空行（一个或多个连续换行）作为段落分隔符，将 markdown 拆成若干块。
    每块会被标记为两种类型之一：
    - "title": 以 1~6 个 "#" 开头的标题行（"#"将作为标题标记被剔除）。
    - "text":  普通文本段落。

    Args:
        markdown: 待切分的 Markdown 原文。

    Returns:
        内容块字典列表，每个字典含 type（title/text）与 text（清洗后的文本）两个键。
    """
    # 用正则按"空行"拆分：\n\s*\n 匹配连续换行（中间可能夹带空格/制表符），同时去除首尾空白并过滤空块
    blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]
    result = []
    for block in blocks:
        result.append(
            {
                # 通过正则判断是否以 "# " 或 "## " 等标题语法开头
                "type": "title" if re.match(r"^#{1,6}\s+", block) else "text",
                # 去掉开头的 "#" 标记，只保留标题文字本身
                "text": re.sub(r"^#{1,6}\s+", "", block),
            }
        )
    return result


def parse_docx_markdown(path: Path) -> ParseResult:
    """
    解析 docx 文档，并转换为 Markdown 风格文本 + 结构块列表。

    处理策略：
    - 使用 python-docx 的 iter_inner_content() 按文档顺序遍历段落和表格（保证正文顺序）。
    - 表格：将每一行用 " | " 拼接为类管道表格文本，过滤全是分隔符的空行；
      结构块 type 记为 "table"。
    - 段落：根据段落样式生成 Markdown 标题层级。
      * 样式 "Title"      -> "# "（一级标题）
      * 样式 "Heading N"  -> (N+1) 个 "#"（二级及以下标题）
      * 其他样式          -> 普通文本

    Args:
        path: docx 文件路径。

    Returns:
        包含 Markdown 文本、段落数元信息与内容块列表的 ParseResult。
    """
    document = WordDocument(path)  # 加载 docx 文档
    markdown_blocks: list[str] = []  # 用于生成纯文本 / Markdown 的块
    content_list: list[dict[str, Any]] = []  # 用于生成结构化内容列表
    for block in document.iter_inner_content():  # 遍历文档中的所有内容块（段落、表格等）
        # 情况一：当前内容块是表格
        if isinstance(block, Table):
            # 将每行单元格文本用 " | " 连接成管道表格的一行
            rows = [" | ".join(cell.text.strip() for cell in row.cells) for row in block.rows]
            # 去除全是空白或分隔符的空行，避免输出无意义行
            table_text = "\n".join(row for row in rows if row.strip(" |"))
            if table_text:
                markdown_blocks.append(table_text)
                content_list.append(
                    {
                        "type": "table",
                        "text": table_text,
                    }
                )
            continue  # 表格处理完毕，跳过后续段落逻辑
        # 情况二：只处理段落（其它类型如基础文本/图片占位符则跳过）
        if not isinstance(block, Paragraph):
            continue
        paragraph = block
        text = paragraph.text.strip()
        if not text:  # 忽略空段落（纯空白/只有空格的文字）
            continue
        # 根据段落样式判断其在 Markdown 中的标题层级
        style_name = paragraph.style.name if paragraph.style else "Normal"
        if style_name == "Title":
            markdown_text = f"# {text}"
            item_type = "title"
        else:
            # 匹配 "Heading 1"、"Heading 2" 等样式，忽略大小写
            match = re.match(r"Heading\s+(\d+)", style_name, flags=re.IGNORECASE)
            if match:
                # 标题层级 = 样式序号 + 1（因为一级已由 "Title" 占用，且最多 6 级）
                markdown_text = f"{'#' * min(int(match.group(1)) + 1, 6)} {text}"
                item_type = "title"
            else:
                markdown_text = text
                item_type = "text"
        markdown_blocks.append(markdown_text)
        content_list.append(
            {
                "type": item_type,
                "text": text,
            }
        )
    return ParseResult(
        "python_docx_markdown",
        "\n\n".join(markdown_blocks),  # 纯文本/Markdown 全文
        {"paragraph_count": len(document.paragraphs)},  # 元信息：段落总数（不含表格）
        {"content_list": content_list},  # 结构化内容块列表
    )


def transcript_segments_to_text(rows: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """
    将语音识别输出的时间片（segment）列表转换为纯文本和结构化片段信息。

    每个片段至少包含 start（开始秒）、end（结束秒）、text（文本），
    若原始数据带 speaker（说话人）字段则一并保留。

    Args:
        rows: 语音识别输出片段列表，每个片段为包含 start/end/text（可选 speaker）的字典。

    Returns:
        二元组 (纯文本全文, {"segments": [...]})，segments 中为清洗后的结构化片段。
    """
    segments: list[dict[str, Any]] = []  # 结构化片段列表
    blocks: list[str] = []  # 纯文本块列表
    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:  # 跳过无内容的片段
            continue
        blocks.append(text)
        segment = {
            "start": float(row["start"]),
            "end": float(row["end"]),
            "text": text,
        }
        speaker = str(row.get("speaker") or "").strip()
        if speaker:  # 仅当存在说话人信息时才写入该字段
            segment["speaker"] = speaker
        segments.append(segment)
    return "\n\n".join(blocks), {"segments": segments}


def parse_document(path: Path) -> ParseResult:
    """
    统一解析入口：根据文件后缀名分发到对应的解析实现。

    支持的文件格式及其解析器：
    - .docx      -> python_docx，转 Markdown
    - .csv       -> langchain CSVLoader（UTF-8 BOM）
    - .json      -> langchain JSONLoader，输出规范化 JSON
    - .html/.htm -> langchain BSHTMLLoader
    - .md/.markdown -> 直接读取文本
    - .pptx      -> python-pptx，输出幻灯片结构
    - .xlsx      -> openpyxl，输出工作表结构
    - .pdf/.png/.jpg/.jpeg -> 抛错（统一交由 MinerU 处理）
    - .wav/.mp3/.m4a -> faster-whisper 中文语音识别
    遇到未注册的后缀会抛出 ValueError。

    Args:
        path: 待解析文件的路径。

    Returns:
        统一结构的 ParseResult（含 parser_name/text/metadata/structured）。

    Raises:
        ValueError: 当文件后缀不支持，或 PDF/图片（应交给 MinerU）时抛出。
    """
    global _whisper_model  # 全局变量，用于缓存已加载的模型
    suffix = path.suffix.lower()  # 统一转为小写，避免大小写不一致导致匹配失败

    if suffix == ".docx":
        return parse_docx_markdown(path)

    if suffix == ".csv":
        # encoding="utf-8-sig" 可自动去除 BOM，兼容带 BOM 的 Excel 导出文件
        return ParseResult("langchain_csv", join_documents(CSVLoader(str(path), encoding="utf-8-sig").load()))

    if suffix == ".json":
        # jq_schema="." 表示取整个对象；text_content=False 表示保留结构化字段而非纯字符串读取
        loader = JSONLoader(str(path), jq_schema=".", text_content=False)
        loaded = loader.load()
        normalized = []
        for document in loaded:
            # langchain 返回的 page_content 是 JSON 字符串，这里再解析后按缩进格式重排，
            # 得到一个格式统一规范的文本，便于后续处理
            value = json.loads(document.page_content)
            normalized.append(json.dumps(value, ensure_ascii=False, indent=2))
        return ParseResult("langchain_json", "\n\n".join(normalized))

    if suffix in {".html", ".htm"}:
        # 用 BeautifulSoup 方式加载 HTML，去除标签后得到正文文本
        return ParseResult("langchain_bshtml", join_documents(BSHTMLLoader(str(path), open_encoding="utf-8").load()))

    if suffix in {".md", ".markdown"}:
        markdown = path.read_text(encoding="utf-8")
        # 除了返回原文，还附带按段落切分的结构化内容列表
        return ParseResult("markdown_text", markdown, structured={"content_list": markdown_content_list(markdown)})

    if suffix == ".pptx":
        res = parse_pptx(str(path))  # 返回 blocks / content_list / slide_count 三个 key
        return ParseResult(
            "pptx_ooxml",
            "\n\n".join(res["blocks"]),
            {"slide_count": res["slide_count"]},
            {"content_list": res["content_list"]},
        )

    if suffix == ".xlsx":
        structured = parse_xlsx(path.read_bytes())  # 传入 bytes 而非路径，避免内存文件限制
        text_parts = []
        for sheet in structured["sheets"]:
            # 每个工作表以 "## 名称" 作为一个小标题
            text_parts.append(f"## {sheet['name']}")
            # 行内 None 值替换为空字符串，再用 " | " 连接为管道表格行
            text_parts.extend(" | ".join("" if value is None else str(value) for value in row) for row in sheet["rows"])
        return ParseResult(
            "openpyxl",
            "\n".join(text_parts),
            {"sheet_count": len(structured["sheets"])},
            structured,
        )

    # 以下格式采用"抛错引导到专门处理流程"的策略，明确交给 MinerU 处理
    if suffix == ".pdf":
        raise ValueError("PDF 统一交给 MinerU 保留标题层级、页码与 bbox")
    if suffix in {".png", ".jpg", ".jpeg"}:
        raise ValueError("图片统一交给 MinerU 保留版面坐标与中文 OCR 结果")

    if suffix in {".wav", ".mp3", ".m4a"}:
        # 懒加载 whisper 模型：首次使用时才导入并实例化，避免拖慢其他文件类型的解析速度
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            _whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        # vad_filter=True 开启语音活动检测，跳过无人声的静音片段；language="zh" 指定中文
        segments, info = _whisper_model.transcribe(str(path), language="zh", vad_filter=True)
        # 将识别结果逐段取整到 2 位小数秒
        rows = [{"start": round(item.start, 2), "end": round(item.end, 2), "text": item.text.strip()} for item in segments]
        text, structured = transcript_segments_to_text(rows)
        return ParseResult(
            "faster_whisper_tiny",
            text,
            {"language": info.language, "duration": info.duration},
            structured,
        )

    # 未注册的后缀：抛出明确错误，提示用户当前解析器不支持的格式
    raise ValueError(f"尚未注册解析器: {suffix}")