import os
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/smoke.db")

print("1) importing app.main ...")
from app.main import app  # noqa
print("   OK, routes:", len(app.routes))

print("2) init db + roles ...")
from app.database import SessionLocal, init_db
from app.services.bootstrap import ensure_roles
init_db()
with SessionLocal() as db:
    ensure_roles(db)
print("   OK")

print("3) embeddings via Ollama nomic-embed-text ...")
from app.services.embeddings import get_embedding_provider
p = get_embedding_provider()
vecs = p.embed_batch(["hello world", "project risk schedule delay"])
print("   OK dims:", len(vecs[0]))

print("4) vector store add/query/delete ...")
from app.services.vector_store import get_vector_store
from app.services.chunking import chunk_plain_text
vs = get_vector_store()
chunks = chunk_plain_text("The API integration is delayed. Rahul will complete it by Friday.", 200, 20)
emb = p.embed_batch([c.text for c in chunks])
n = vs.add_document_chunks(999, 1, "Test.txt", chunks, emb)
print("   added:", n, "count:", vs.count(999))
res = vs.query(999, p.embed_one("who will complete the API integration"), top_k=2)
print("   query hit:", res[0]["original_name"], "|", res[0]["text"][:40])
vs.delete_document(999, 1)
print("   after delete count:", vs.count(999))
vs.reset_project(999)
print("   OK")

print("5) Ollama generation ...")
from app.services.ai.providers import get_provider_manager
mgr = get_provider_manager()
txt, prov, model = mgr.generate("Reply with exactly: OK")
print("   provider:", prov, "model:", model, "reply:", txt.strip()[:30])

print("6) chunk extraction (txt/csv) ...")
from app.services.extract import extract_document
print("   SUCCESS")
