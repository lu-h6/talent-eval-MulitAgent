import logging  # Python 标准日志库
import re  # 正则，用于清洗服务名避免非法文件名
import sys  # 标准输出
from collections.abc import Callable  # 回调类型标注
from datetime import date  # 日期（用于按天切分日志文件）
from pathlib import Path  # 路径操作


# 日志单行格式：时间 | 级别 | 记录器名称 | 消息内容
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
# 时间显示格式
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class DailyFileHandler(logging.Handler):
    """按天自动切分日志文件的处理器。

    写入的日志文件名带日期（如 backend-2026-10-13.log），
    跨天后首次写入会自动创建新文件，实现日志按日滚动。
    """

    def __init__(
        self,
        log_dir: Path,
        filename_prefix: str,
        date_provider: Callable[[], date] = date.today,
    ) -> None:
        super().__init__()
        self.log_dir = log_dir                # 日志存放目录
        self.filename_prefix = filename_prefix  # 文件名前缀（如 backend、backend-error）
        self.date_provider = date_provider    # 日期提供函数（便于测试时注入固定日期）
        self._active_date: date | None = None  # 当前正在写入的日志所属日期
        self._file_handler: logging.FileHandler | None = None  # 底层文件处理器缓存
        self.talent_logging_handler = True     # 自定义标记，便于重配置时识别并移除本类处理器

    def _ensure_file_handler(self) -> logging.FileHandler:
        """确保存在匹配当前日期的文件处理器，返回它（必要时切换到新文件）。"""
        current_date = self.date_provider()
        # 日期未变化则复用已有处理器
        if self._file_handler is not None and current_date == self._active_date:
            return self._file_handler
        # 跨天：先关闭旧文件处理器
        if self._file_handler is not None:
            self._file_handler.close()
        # 创建目录并打开当天的新日志文件
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{self.filename_prefix}-{current_date.isoformat()}.log"
        self._file_handler = logging.FileHandler(path, encoding="utf-8")
        self._file_handler.setFormatter(self.formatter)  # 沿用外层设定的格式化器
        self._active_date = current_date
        return self._file_handler

    def setFormatter(self, formatter: logging.Formatter | None) -> None:
        """同步更新底层文件处理器的格式化器。"""
        super().setFormatter(formatter)
        if self._file_handler is not None:
            self._file_handler.setFormatter(formatter)

    def emit(self, record: logging.LogRecord) -> None:
        """真正写出一条日志记录，异常时交给 handleError 兜底。"""
        try:
            self._ensure_file_handler().emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """关闭底层文件处理器并释放资源。"""
        if self._file_handler is not None:
            self._file_handler.close()
            self._file_handler = None
        super().close()


def configure_logging(
    log_dir: str | Path,
    service_name: str,
    *,
    current_date: date | None = None,
) -> None:
    """统一配置应用日志：控制台输出 + 服务日志 + 错误日志（均按日滚动）。

    Args:
        log_dir: 日志输出目录。
        service_name: 服务名，用于生成日志文件名前缀（会自动清洗为安全字符）。
        current_date: 可选，指定日志文件使用的日期（便于测试固定日志文件名）；
                     缺省时使用当天真实日期。
    """
    directory = Path(log_dir)
    # 清洗服务名为安全的字母/数字/中划线组合，避免拼接到文件名时产生非法字符
    safe_service_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", service_name).strip("-") or "service"
    # 若显式传入 current_date 则据此切分日志，否则按真实日期
    date_provider = (lambda: current_date) if current_date is not None else date.today
    # 统一日志格式
    formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # 清理之前配置过的本类处理器，避免重复添加导致日志重复输出
    for handler in list(root_logger.handlers):
        if getattr(handler, "talent_logging_handler", False):
            root_logger.removeHandler(handler)
            handler.close()

    # 1) 控制台处理器：输出至标准输出，级别 INFO
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.talent_logging_handler = True

    # 2) 服务日志处理器：按日滚动文件，级别 INFO
    service_handler = DailyFileHandler(directory, safe_service_name, date_provider)
    service_handler.setLevel(logging.INFO)
    service_handler.setFormatter(formatter)

    # 3) 错误日志处理器：按日滚动文件，级别 ERROR（单独文件便于排查）
    error_handler = DailyFileHandler(directory, f"{safe_service_name}-error", date_provider)
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # 将三个处理器挂到根日志器上
    root_logger.addHandler(console_handler)
    root_logger.addHandler(service_handler)
    root_logger.addHandler(error_handler)