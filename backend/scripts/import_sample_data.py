"""import_sample_data.py —— 批量导入示例人才材料到后端并触发解析。

遍历 sample-data/generated 目录下所有受支持格式的文件，
逐个调用后端 /api/documents 上传并同步解析，最后汇总各解析器与
状态分布并输出 e2e-report.json 报告；存在失败项时退出码为 1。
"""
import json
import mimetypes
import re
import sys
from collections import Counter
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]  # 项目根目录
DATA = ROOT / "sample-data" / "generated"  # 生成的样例文件目录
API = "http://127.0.0.1:18080/api"  # 后端 API 基址
MAIN_SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx", ".png", ".wav", ".csv", ".json", ".html", ".md"}


def main() -> None:
    """遍历样例文件逐个上传并解析，输出汇总报告。"""
    # 载入候选人基础信息，便于按文件名前缀映射到具体候选人
    candidates = {item["candidate_id"]: item for item in json.loads((ROOT / "sample-data/source/candidates.json").read_text())}
    # 收集所有受支持后缀的文件并按路径排序保证稳定顺序
    files = sorted(path for path in DATA.glob("*/*") if path.suffix.lower() in MAIN_SUFFIXES)
    results = []
    with httpx.Client(timeout=900) as client:  # 长超时以容纳大文件解析
        for path in files:
            # 从文件名提取候选人编号（如 C001_project.pdf -> C001）
            candidate_id = re.match(r"(C\d{3})_", path.name).group(1)
            candidate = candidates[candidate_id]
            # 1) 上传文档
            response = client.post(
                f"{API}/documents",
                files={"file": (path.name, path.read_bytes(), mimetypes.guess_type(path)[0] or "application/octet-stream")},
                data={"candidate_id": candidate_id, "title": path.stem, "document_type": path.parent.name},
            )
            response.raise_for_status()
            document = response.json()
            # 2) 同步触发解析（async_mode=false）
            parse_response = client.post(f"{API}/documents/{document['id']}/parse?async_mode=false")
            parse_response.raise_for_status()
            job = parse_response.json()
            results.append({"file": path.name, "document_id": document["id"], **job})
            print(f"{len(results):02d}/{len(files)} {path.parent.name:8s} {job['status']:16s} {job['parser_name']} {path.name}", flush=True)
    # 汇总统计并输出 JSON 报告
    report = {"total": len(results), "statuses": Counter(row["status"] for row in results), "parsers": Counter(row["parser_name"] for row in results), "items": results}
    report["statuses"] = dict(report["statuses"])  # Counter 转普通字典便于 JSON 序列化
    report["parsers"] = dict(report["parsers"])
    # 写回 e2e-report.json 供后续比对
    output = ROOT / "sample-data/qa/e2e-report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total", "statuses", "parsers")}, ensure_ascii=False, indent=2))
    # 存在失败项时以非零退出码结束，便于 CI 判断
    if report["statuses"].get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()