import json
from io import BytesIO

from openpyxl import Workbook

from app.documents.parsers import parse_csv, parse_json, parse_xlsx, parse_pptx


def test_parse_csv_preserves_headers_and_rows():
    content = "candidate_id,name,score\nC001,林晓岚,92\n".encode()

    result = parse_csv(content)

    assert result["kind"] == "table"
    assert result["columns"] == ["candidate_id", "name", "score"]
    assert result["rows"] == [{"candidate_id": "C001", "name": "林晓岚", "score": "92"}]


def test_parse_json_preserves_nested_business_data():
    content = json.dumps({"candidate_id": "C001", "skills": ["Python", "FastAPI"]}, ensure_ascii=False).encode()

    result = parse_json(content)

    assert result["kind"] == "json"
    assert result["data"]["skills"] == ["Python", "FastAPI"]


def test_parse_xlsx_returns_each_sheet_and_formula_text():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "绩效记录"
    sheet.append(["candidate_id", "Q1", "Q2", "平均分"])
    sheet.append(["C001", 88, 92, "=AVERAGE(B2:C2)"])
    stream = BytesIO()
    workbook.save(stream)

    result = parse_xlsx(stream.getvalue())

    assert result["kind"] == "workbook"
    assert result["sheets"][0]["name"] == "绩效记录"
    assert result["sheets"][0]["rows"][1][3] == "=AVERAGE(B2:C2)"
