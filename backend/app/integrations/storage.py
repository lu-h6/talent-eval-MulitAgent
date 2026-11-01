"""对象存储的桶名与路径约定。"""

import re
from pathlib import PurePath


def build_object_key(tenant_id: str, document_id: str, version_no: int, filename: str) -> str:
    """构造对象存储中文件的键（对象键），用于按租户/文档/版本组织文件。

    会清洗文件名，仅保留安全的字符（字母/数字/下划线/横线/点/中文），
    避免包含路径分隔符或非法字符导致存储安全问题。

    Args:
        tenant_id: 租户标识。
        document_id: 文档 ID。
        version_no: 文档版本号。
        filename: 原始文件名。

    Returns:
        形如 "tenants/{tenant}/documents/{doc}/versions/{v}/{安全文件名}" 的对象键。
    """
    # 取纯文件名（丢弃任何目录前缀），并把非安全字符替换为下划线
    safe_name = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", PurePath(filename).name)
    return f"tenants/{tenant_id}/documents/{document_id}/versions/{version_no}/{safe_name}"