"""提示词脱敏：发送给外部模型前，先把个人身份信息替换为占位符。"""
from __future__ import annotations

from collections import Counter
import re

# 此处用正则表达式获取需要替换的内容，也可以使用外部NER模型等其他方式
LABELED_NAME = re.compile(
    r"(?P<prefix>(?:候选人姓名|员工姓名|姓名)[：:\s]+)"
    r"(?P<value>[\u4e00-\u9fff·]{2,20})(?=[，,。；;\s]|$)"
)
DETECTORS = (
    ("email", re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
)


def sanitize_prompt(original_prompt: str) -> str:
    """把姓名、邮箱、身份证号与手机号替换为带编号的占位符，返回脱敏后的文本。"""
    indexes: Counter[str] = Counter()
    placeholders: dict[tuple[str, str], str] = {} # 映射关系

    def replace(pii_type: str, value: str) -> str:
        """为同一类、同一取值的敏感片段分配稳定占位符（同类按出现顺序编号）。"""
        key = (pii_type, value)
        if key not in placeholders:
            indexes[pii_type] += 1
            placeholders[key] = f"__PII_{pii_type.upper()}_{indexes[pii_type]:03d}__"
        return placeholders[key]

    result = original_prompt
    names = [match.group("value") for match in LABELED_NAME.finditer(result)]
    for name in dict.fromkeys(names):
        result = result.replace(name, replace("person", name))
    for pii_type, pattern in DETECTORS:
        result = pattern.sub(
            lambda match, kind=pii_type: replace(kind, match.group(0)),
            result,
        )
    return result
