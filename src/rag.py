import os
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_core.documents import Document

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:password@localhost:5432/whatsapp_ai"
)


def get_embeddings():
    """Returns the Google Generative AI embedding model."""
    return GoogleGenerativeAIEmbeddings(model="models/gemini-embedding-2")


def _get_vector_store(tenant_id: str):
    """
    Returns a PGVector store isolated by tenant_id.
    Raises ImportError if pgvector is not available.
    """
    from langchain_community.vectorstores.pgvector import PGVector

    collection_name = f"tenant_{tenant_id}_transactions"
    return PGVector(
        connection_string=DATABASE_URL,
        embedding_function=get_embeddings(),
        collection_name=collection_name,
        use_jsonb=True,
    )


def add_transaction_to_rag(tenant_id: str, text: str, metadata: dict = None):
    """
    Adds a recorded transaction to the PGVector store for future RAG context.
    Called automatically by record_transaction() after a successful DB write.
    """
    try:
        vs = _get_vector_store(tenant_id)
        doc = Document(page_content=text, metadata=metadata or {})
        vs.add_documents([doc])
    except Exception as e:
        # Error tidak fatal, lanjutkan
        print(f"[RAG] add_transaction_to_rag failed: {e}")


def retrieve_past_transactions(tenant_id: str, query: str, k: int = 50) -> str:
    """
    Retrieves the top-K past transactions semantically similar to the query.
    Falls back to a plain DB query if PGVector is unavailable.

    PRD requirement: inject 50 past transactions as context into the agent prompt.
    """
    # Utama: Pencarian semantik PGVector
    try:
        vs = _get_vector_store(tenant_id)
        docs = vs.similarity_search(query, k=k)
        if docs:
            results = [doc.page_content for doc in docs]
            return "\n".join(results)
    except Exception as pgvec_err:
        print(f"[RAG] PGVector search failed, trying DB fallback: {pgvec_err}")

    # Cadangan: Query SQLAlchemy biasa
    try:
        from database import SessionLocal
        import models

        db = SessionLocal()
        try:
            rows = (
                db.query(models.Transaction)
                .filter(models.Transaction.tenant_id == tenant_id)
                .order_by(models.Transaction.created_at.desc())
                .limit(k)
                .all()
            )
            if not rows:
                return "Belum ada riwayat transaksi."

            lines = []
            for r in rows:
                date_str = r.transaction_date.strftime("%Y-%m-%d") if r.transaction_date else "?"
                lines.append(
                    f"[{r.type.upper() if r.type else '?'}] {r.category or '?'} | "
                    f"{r.merchant_name or ''} | {date_str} | "
                    f"Total: Rp {r.total_amount:,.0f}"
                )
            return "\n".join(lines)
        finally:
            db.close()
    except Exception as db_err:
        return f"Tidak dapat mengambil riwayat transaksi: {db_err}"
