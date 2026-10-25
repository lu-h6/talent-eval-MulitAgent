"""
底层内容解析工具模块：针对 CSV、JSON、XLSX、PPTX 等格式，基于各自专有的
第三方库（csv/json 标准库、openpyxl、python-pptx）做"轻量、结构化"解析。

与 document_parsers.py 的区别：
- 本模块主要处理"二进制内容"（bytes）或文件路径，输出带有 kind 标记的结构字典。
- document_parsers.py 再把这些字典组装成统一的 ParseResult。
这些函数既可作为独立的解析能力使用，也可被上层主解析流程复用。
"""

import csv
import json
from io import BytesIO, StringIO
from typing import Any

from openpyxl import load_workbook


def parse_csv(content: bytes) -> dict[str, Any]:
    """
    解析 CSV 二进制内容，输出表格结构。

    - 用 utf-8-sig 解码，可自动剥离 UTF-8 文件的 BOM 头，兼容 Excel 导出的 CSV。
    - 使用 csv.DictReader（按首行作为字段名）逐行读取。
    - 返回包含 kind、columns（列名）、rows（字典列表，key 为列名）的结构。

    Args:
        content: CSV 文件的二进制字节内容。

    Returns:
        {"kind": "table", "columns": [...], "rows": [逐行字典,...]}。
    """
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))  # StringIO 便于对内存字符串执行标准库读取
    return {
        "kind": "table",
        "columns": reader.fieldnames or [],  # fieldnames 为 None 时兜底为空列表
        "rows": list(reader),
    }


def parse_json(content: bytes) -> dict[str, Any]:
    """
    解析 JSON 二进制内容。

    先将 bytes 按 utf-8-sig 解码成字符串，再用 json.loads 解析为 Python 对象，
    以 {"kind": "json", "data": <解析结果>} 结构返回。

    Args:
        content: JSON 文件的二进制字节内容。

    Returns:
        {"kind": "json", "data": 反序列化后的 Python 对象}。
    """
    return {"kind": "json", "data": json.loads(content.decode("utf-8-sig"))}


def parse_xlsx(content: bytes) -> dict[str, Any]:
    """
    解析 XLSX 二进制内容为多工作表结构。

    openpyxl 配置说明：
    - BytesIO(content)：直接在内存中读取 bytes，无需落盘临时文件。
    - data_only=False：读取公式单元格时得到公式本身（而非缓存的计算值），保留原始语义。
    - read_only=True：只读模式，性能更好、占用内存更少，适合只取数据。
    - iter_rows(values_only=True)：按行迭代并只返回单元格的值（不含坐标/样式）。

    Args:
        content: XLSX 文件的二进制字节内容。

    Returns:
        {"kind": "workbook", "sheets": [{"name":..., "rows":[[值,...],...]}, ...]}。
    """
    workbook = load_workbook(BytesIO(content), data_only=False, read_only=True)
    # 遍历所有工作表，每张表保存 名称(title) 和 值二维列表(rows)
    sheets = [
        {"name": sheet.title, "rows": [list(row) for row in sheet.iter_rows(values_only=True)]}
        for sheet in workbook.worksheets
    ]
    return {"kind": "workbook", "sheets": sheets}


def parse_pptx(file_path: str) -> dict[str, Any]:
    """
    解析 PPTX 文件，输出每张幻灯片的文本内容与区域定位信息。

    返回结构包含三个 key：
    - blocks:        纯文本块列表，顶层元数据（## Slide N、### 标题）用于后期渲染。
    - content_list:  结构化内容块列表，每块带 type 和 locator（定位信息）。
    - slide_count:   幻灯片总张数。

    locator 用于记录每块文本在真实幻灯片上的位置，便于端上做高亮/回跳定位：
    - 整张幻灯片（kind="slide"）：仅记录 slide（第几张）。
    - 幻灯片内区域（kind="slide_region"）：额外记录 shape 序号、bbox 包围盒
      （[left, top, right, bottom]，单位为 PPTX 的 EMU）和 coordinate_space 坐标系标识。

    Args:
        file_path: PPTX 文件路径。

    Returns:
        {"blocks": [...], "content_list": [...], "slide_count": N}。
    """
    from pptx import Presentation  # 延迟导入，避免首次加载模块即引入 python-pptx 依赖

    presentation = Presentation(file_path)  # 加载 pptx 文件
    blocks: list[str] = []  # 纯文本块（含标题级标记）
    content_list: list[dict[str, Any]] = []  # 结构化内容块（含定位信息）
    # enumerate 从 1 开始编号，与用户视角"第 1 张幻灯片"一致
    for number, slide in enumerate(presentation.slides, start=1):
        # 每张幻灯片先添加一个 "## Slide N" 元信息块
        blocks.append(f"## Slide {number}")
        content_list.append(
            {"type": "title", "text": f"Slide {number}", "locator": {"kind": "slide", "slide": number}}
        )
        # 遍历幻灯片内所有形状，编号同样从 1 开始
        for shape_index, shape in enumerate(slide.shapes, start=1):
            # 有些形状（如纯图片占位符）可能没有 text 属性，取不到就视为无文本
            text = shape.text.strip() if hasattr(shape, "text") else ""
            if not text:  # 跳过无文字的空白形状
                continue
            # 通过与 slide.shapes.title 比较判断是否为该页的标题占位符
            is_title = shape == slide.shapes.title
            blocks.append(f"### {text}" if is_title else text)
            # 记录该文本块的类型与页面内的包围盒定位信息
            content_list.append(
                {
                    "type": "title" if is_title else "text",
                    "text": text,
                    "locator": {
                        "kind": "slide_region",
                        "slide": number,
                        "shape": shape_index,
                        # bbox 为左上角 (left, top) 到右下角 (left+width, top+height)，单位 EMU
                        "bbox": [
                            int(shape.left),
                            int(shape.top),
                            int(shape.left + shape.width),
                            int(shape.top + shape.height),
                        ],
                        "coordinate_space": "pptx_emu",
                    },
                }
            )
    return {"blocks": blocks, "content_list": content_list, "slide_count": len(presentation.slides)}