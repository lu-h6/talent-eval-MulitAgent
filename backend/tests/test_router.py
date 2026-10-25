import pytest

from app.documents.router import ParserKind, choose_parser


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("candidate.csv", ParserKind.LANGCHAIN),
        ("candidate.json", ParserKind.LANGCHAIN),
        ("candidate.html", ParserKind.LANGCHAIN),
        ("interview.docx", ParserKind.LANGCHAIN),
        ("performance.xlsx", ParserKind.NATIVE),
        ("resume.pdf", ParserKind.MINERU),
        ("review.pptx", ParserKind.NATIVE),
        ("certificate.png", ParserKind.MINERU),
        ("interview.mp3", ParserKind.AUDIO),
    ],
)
def test_choose_parser_routes_supported_formats(filename, expected):
    assert choose_parser(filename) == expected


def test_choose_parser_rejects_unsupported_format():
    with pytest.raises(ValueError, match="不支持的文件格式"):
        choose_parser("archive.exe")
