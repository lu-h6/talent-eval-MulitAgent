from app.integrations.storage import build_object_key


def test_object_key_is_tenant_scoped_and_versioned():
    key = build_object_key(
        tenant_id="acme",
        document_id="doc-123",
        version_no=2,
        filename="候选人 简历.pdf",
    )

    assert key == "tenants/acme/documents/doc-123/versions/2/候选人_简历.pdf"


def test_object_key_removes_parent_directory_segments():
    key = build_object_key("acme", "doc-123", 1, "../../secret.json")

    assert key.endswith("/secret.json")
    assert ".." not in key
