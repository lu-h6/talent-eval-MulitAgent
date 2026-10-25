import json
from pathlib import Path

import httpx


report_path = Path(__file__).resolve().parents[2] / "sample-data/qa/e2e-report.json"
report = json.loads(report_path.read_text(encoding="utf-8"))
with httpx.Client(timeout=900) as client:
    for item in report["items"]:
        if not item["file"].lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        response = client.post(f"http://127.0.0.1:18080/api/documents/{item['document_id']}/parse?async_mode=false")
        response.raise_for_status()
        result = response.json()
        item.update(result)
        print(item["file"], item["status"], item["parser_name"])
report["parsers"].pop("tesseract_ocr", None)
report["parsers"]["mineru_pipeline_3.4.4"] = 6
report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
