import re
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "sample-data" / "generated" / "markdown"
API = "http://127.0.0.1:18080/api"
HEADERS = {"X-Tenant-ID": "course-demo", "X-Permission-Scopes": "hr_private"}


def wait_for(condition, timeout=120, interval=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result is not None:
            return result
        time.sleep(interval)
    return None


def main() -> None:
    files = sorted(DATA.glob("*.md"))
    client = httpx.Client(timeout=300)
    employees = {item["employee_no"]: item["id"] for item in client.get(f"{API}/employees", headers=HEADERS).json()}
    knowledge_bases = client.get(f"{API}/knowledge-bases", headers=HEADERS).json()
    knowledge_base_id = knowledge_bases[0]["id"] if knowledge_bases else None
    for path in files:
        candidate_id = re.match(r"(C\d{3})_", path.name).group(1)
        title = path.stem
        form = {"candidate_id": candidate_id, "title": title, "document_type": "markdown"}
        if employees.get(candidate_id):
            form["employee_id"] = employees[candidate_id]
        if knowledge_base_id:
            form["knowledge_base_id"] = knowledge_base_id
        upload = client.post(
            f"{API}/documents",
            files={"file": (path.name, path.read_bytes(), "text/markdown")},
            data=form,
            headers=HEADERS,
        )
        upload.raise_for_status()
        document_id = upload.json()["id"]

        def parse_done():
            detail = client.get(f"{API}/documents/{document_id}", headers=HEADERS).json()
            status = detail["parse_status"]
            return detail if status in {"succeeded", "failed"} else None

        detail = wait_for(parse_done)
        if not detail or detail["parse_status"] != "succeeded":
            print(f"{path.name}: parse {detail['parse_status'] if detail else 'timeout'}", file=sys.stderr)
            continue

        chunk = client.post(
            f"{API}/documents/{document_id}/chunks",
            json={"chunk_size": 800, "chunk_overlap": 100},
            headers=HEADERS,
        )
        chunk.raise_for_status()

        index = client.post(f"{API}/documents/{document_id}/evidence-index", headers=HEADERS)
        index.raise_for_status()

        def index_done():
            item = client.get(f"{API}/documents/{document_id}", headers=HEADERS).json()
            status = item["index_status"]
            return item if status in {"succeeded", "failed"} else None

        final = wait_for(index_done)
        if not final or final["index_status"] != "succeeded":
            print(f"{path.name}: index {final['index_status'] if final else 'timeout'}", file=sys.stderr)
            continue
        print(
            f"{candidate_id}  {path.stem}  chunk={chunk.json()['strategy']}  index={final['index_status']}",
            flush=True,
        )


if __name__ == "__main__":
    main()
