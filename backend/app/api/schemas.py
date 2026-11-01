"""接口层共享的请求/响应模型定义。"""

from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    """创建文档时的请求体校验模型。

    用于规范文档创建接口入参，强制要求必填字段的格式与取值范围。
    """
    candidate_id: str = Field(min_length=1, max_length=64)  # 候选人业务标识，必填，长度 1-64
    tenant_id: str = Field(min_length=1, max_length=64)  # 租户标识，必填，长度 1-64
    title: str = Field(min_length=1, max_length=255)  # 文档标题，必填
    document_type: str = Field(min_length=1, max_length=64)  # 文档类型（如简历、测评等）
    source: str = "manual_upload"  # 文档来源，默认手动上传
    permission_scope: str = "hr_private"  # 权限范围，默认 HR 私有
    confidentiality_level: str = "internal"  # 机密级别，默认内部