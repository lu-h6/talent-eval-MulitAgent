import logging
from io import BytesIO

import boto3
from botocore.client import Config

from app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)


class ObjectStore:
    """封装 S3 / MinIO 对象存储的读写操作。

    提供一个对外统一的对象存储接口：写入、读取、删除、生成预签名下载 URL，
    并确保默认桶（bucket）在初始化时已存在。
    """

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        # 用于内部读写（服务端可直接访问的端点）
        self.client = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            config=Config(signature_version="s3v4"),  # 使用 S3 v4 签名
            region_name="us-east-1",
        )
        # 用于生成对外可下载的预签名 URL（使用公共端点）
        self.public_client = boto3.client(
            "s3",
            endpoint_url=self.settings.s3_public_endpoint_url,
            aws_access_key_id=self.settings.s3_access_key,
            aws_secret_access_key=self.settings.s3_secret_key,
            config=Config(signature_version="s3v4"),
            region_name="us-east-1",
        )
        self.ensure_bucket()  # 确保默认桶存在

    def ensure_bucket(self) -> None:
        """确保默认桶存在，不存在则创建。"""
        try:
            # 探测桶是否存在
            self.client.head_bucket(Bucket=self.settings.s3_bucket)
            logger.info("object_store_bucket_ready bucket=%s", self.settings.s3_bucket)
        except Exception as exc:
            # 桶不存在 → 创建
            logger.warning("object_store_bucket_missing bucket=%s reason=%s", self.settings.s3_bucket, exc)
            self.client.create_bucket(Bucket=self.settings.s3_bucket)
            logger.info("object_store_bucket_created bucket=%s", self.settings.s3_bucket)

    def put_bytes(self, key: str, content: bytes, content_type: str) -> None:
        """把字节内容写入对象存储。"""
        logger.info(
            "object_store_put_started bucket=%s key=%s size_bytes=%s content_type=%s",
            self.settings.s3_bucket,
            key,
            len(content),
            content_type,
        )
        # 通过内存字节流上传文件对象，并显式指定 ContentType
        self.client.upload_fileobj(BytesIO(content), self.settings.s3_bucket, key, ExtraArgs={"ContentType": content_type})
        logger.info("object_store_put_completed bucket=%s key=%s", self.settings.s3_bucket, key)

    def get_bytes(self, key: str) -> bytes:
        """从对象存储读取指定对象的内容并返回字节。"""
        logger.info("object_store_get_started bucket=%s key=%s", self.settings.s3_bucket, key)
        content = self.client.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"].read()
        logger.info("object_store_get_completed bucket=%s key=%s size_bytes=%s", self.settings.s3_bucket, key, len(content))
        return content

    def presigned_get(self, key: str, expires: int = 900) -> str:
        """生成一个可对外访问的临时下载 URL（默认 15 分钟有效）。"""
        url = self.public_client.generate_presigned_url("get_object", Params={"Bucket": self.settings.s3_bucket, "Key": key}, ExpiresIn=expires)
        logger.info("object_store_presigned_url_created bucket=%s key=%s expires_seconds=%s", self.settings.s3_bucket, key, expires)
        return url

    def delete(self, key: str) -> None:
        """从对象存储删除指定对象。"""
        logger.info("object_store_delete_started bucket=%s key=%s", self.settings.s3_bucket, key)
        self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        logger.info("object_store_delete_completed bucket=%s key=%s", self.settings.s3_bucket, key)