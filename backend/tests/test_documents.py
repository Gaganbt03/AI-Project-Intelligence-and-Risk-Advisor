def _upload(client, headers, project_id, name: str, content: bytes, upload_type="text/plain"):
    return client.post(
        f"/api/documents/upload?project_id={project_id}",
        headers=headers,
        files={"file": (name, content, upload_type)},
    )


def _fetched(client, headers, doc):
    return client.get(f"/api/documents/{doc['id']}", headers=headers).json()


def test_upload_txt_processes_inline(client, employee_headers, project_a):
    content = (
        b"The Orion platform integration is behind schedule.\n"
        b"Rahul owns the API integration and expects to finish by Friday.\n"
        b"The marketing team needs the onboarding flow before the launch.\n"
        b"Risk: vendor dependency may delay the audit trail module.\n"
    )
    r = _upload(client, employee_headers, project_a["id"], "meeting-notes.txt", content)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["original_name"] == "meeting-notes.txt"
    assert doc["file_type"] == "txt"
    processed = _fetched(client, employee_headers, doc)
    assert processed["status"] == "Processed"
    assert processed["chunk_count"] > 0
    assert processed["embedding_status"] in ("Completed", "Not Started", "Failed")


def test_upload_csv(client, employee_headers, project_a):
    content = b"name,status,owner\nPayments API,Behind,Abdul\nAuth service,On track,Sara\n"
    r = _upload(client, employee_headers, project_a["id"], "status.csv", content, "text/csv")
    assert r.status_code == 201
    fetched = _fetched(client, employee_headers, r.json())
    assert fetched["file_type"] == "csv"
    assert fetched["chunk_count"] > 0


def test_download_returns_original_bytes(client, employee_headers, project_a):
    content = b"This is the raw source material, byte for byte.\nActually two lines."
    doc = _upload(client, employee_headers, project_a["id"], "raw.txt", content).json()
    dl = client.get(f"/api/documents/{doc['id']}/download", headers=employee_headers)
    assert dl.status_code == 200
    assert dl.content == content
    assert "attachment" in dl.headers.get("content-disposition", "")


def test_employee_cannot_upload_to_unassigned_project(client, employee_headers, admin_headers):
    p = client.post(
        "/api/projects", headers=admin_headers,
        json={"name": "Locked Vault", "member_ids": []},
    ).json()
    r = _upload(client, employee_headers, p["id"], "p.txt", b"hello")
    assert r.status_code == 403
    # employee also cannot read it
    assert client.get(f"/api/documents?project_id={p['id']}", headers=employee_headers).status_code == 403


def test_employee_permissions_on_documents(client, employee_headers, admin_headers, project_a):
    doc = _upload(client, employee_headers, project_a["id"], "perm.txt", b"some text").json()
    # Reprocess is admin-only
    assert client.post(f"/api/documents/{doc['id']}/reprocess", headers=employee_headers).status_code == 403
    # Delete is admin-only
    assert client.delete(f"/api/documents/{doc['id']}", headers=employee_headers).status_code == 403
    # Preview accessible
    prev = client.get(f"/api/documents/{doc['id']}/preview", headers=employee_headers)
    assert prev.status_code == 200
    assert prev.json()["text"]
    # Admin delete works
    assert client.delete(f"/api/documents/{doc['id']}", headers=admin_headers).status_code == 200


def test_delete_removes_document_artifacts(client, admin_headers, project_a):
    doc = _upload(
        client,
        admin_headers,
        project_a["id"],
        "delete-lifecycle.txt",
        b"delete this document and all of its project-scoped artifacts",
    ).json()

    from pathlib import Path
    from app.database import SessionLocal
    from app.models import DocumentChunk, ProjectDocument, Risk

    with SessionLocal() as db:
        record = db.query(ProjectDocument).filter(ProjectDocument.id == doc["id"]).one()
        storage_path = record.storage_path
        db.add(
            Risk(
                project_id=project_a["id"],
                title="Linked risk",
                source_document_id=doc["id"],
            )
        )
        db.commit()
        risk_id = db.query(Risk).filter(Risk.title == "Linked risk").one().id

    response = client.delete(f"/api/documents/{doc['id']}", headers=admin_headers)
    assert response.status_code == 200

    with SessionLocal() as db:
        assert db.query(ProjectDocument).filter(ProjectDocument.id == doc["id"]).count() == 0
        assert db.query(DocumentChunk).filter(DocumentChunk.document_id == doc["id"]).count() == 0
        assert db.query(Risk).filter(Risk.id == risk_id, Risk.source_document_id.is_(None)).count() == 1
    assert not Path(storage_path).exists()


def test_upload_rejects_empty_or_wrong_type(client, employee_headers, project_a):
    r = _upload(client, employee_headers, project_a["id"], "script.exe", b"MZ\x90\x00")
    assert r.status_code in (400, 415)


def test_list_filters_by_type_and_status(client, employee_headers, project_a):
    _upload(client, employee_headers, project_a["id"], "list1.txt", b"alpha")
    _upload(client, employee_headers, project_a["id"], "list2.txt", b"beta")
    docs = client.get("/api/documents?file_type=txt", headers=employee_headers).json()
    assert all(d["file_type"] == "txt" for d in docs)
    proc = client.get("/api/documents?status=Processed", headers=employee_headers).json()
    assert proc, "expected at least one processed document"
    assert all(d["status"] == "Processed" for d in proc)