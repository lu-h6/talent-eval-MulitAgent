"""router.py —— 解析器类型路由。

根据文件名后缀决定使用哪一类解析器（LangChain / NATIVE 自定义 / MinerU / 音频），
供上传与解析流程在运行时选择正确的解析实现。
"""
from enum import StrEnum
from pathlib import Path


class ParserKind(StrEnum):
    """解析器类型枚举。"""
    LANGCHAIN = "langchain"  # Langchain 解析器
    NATIVE = "native"  # 自定义解析器（如 Markdown、Excel 等）
    MINERU = "mineru"  # MinerU 解析器（复杂文档）
    AUDIO = "audio"  # 语音识别解析器（如 wav、mp3 等）


# 文件后缀 → 解析器类型 的映射表
PARSER_BY_SUFFIX = {
    # ---- 文本类（走 Langchain 加载器）----
    ".csv": ParserKind.LANGCHAIN,
    ".json": ParserKind.LANGCHAIN,
    ".html": ParserKind.LANGCHAIN,
    ".htm": ParserKind.LANGCHAIN,
    ".docx": ParserKind.LANGCHAIN,
    # ---- 自定义解析（NATIVE）----
    ".md": ParserKind.NATIVE,
    ".markdown": ParserKind.NATIVE,
    ".xlsx": ParserKind.NATIVE,
    ".pptx": ParserKind.NATIVE,
    # ---- 复杂文档（MinerU 深度解析）----
    ".pdf": ParserKind.MINERU,
    ".png": ParserKind.MINERU,
    ".jpg": ParserKind.MINERU,
    ".jpeg": ParserKind.MINERU,
    # ---- 音频（语音识别）----
    ".wav": ParserKind.AUDIO,
    ".mp3": ParserKind.AUDIO,
    ".m4a": ParserKind.AUDIO,
}


def choose_parser(filename: str) -> ParserKind:
    """根据文件名后缀返回对应的解析器类型。

    Args:
        filename: 文件名（含扩展名）。

    Returns:
        对应的 ParserKind 枚举值。

    Raises:
        ValueError: 当文件后缀不在映射表内（或缺少扩展名）时抛出。
    """
    suffix = Path(filename).suffix.lower()  # 取得小写扩展名
    try:
        return PARSER_BY_SUFFIX[suffix]
    except KeyError as exc:
        raise ValueError(f"不支持的文件格式: {suffix or '无扩展名'}") from exc