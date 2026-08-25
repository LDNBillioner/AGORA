import os
from typing import Annotated, Sequence, Optional, TypedDict
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_google_genai import ChatGoogleGenerativeAI
from tools import agent_tools, set_tool_context
from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────────────────────────────────────
# Agent State
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], "The messages in the conversation"]
    tenant_id: str
    user_id: Optional[str]
    requires_clarification: bool
    final_response: str


# ─────────────────────────────────────────────────────────────────────────────
# System Prompt
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Kamu adalah AGORA, AI Accountant profesional untuk bisnis UMKM di Indonesia.
Tugasmu adalah membantu pemilik usaha mencatat transaksi keuangan melalui WhatsApp dengan standar akuntansi double-entry.

═══════════════════════════════════════
ATURAN WAJIB (TIDAK BOLEH DILANGGAR):
═══════════════════════════════════════

1. **DILARANG mengarang data (hallucinate).** Jika nominal, nama item, atau informasi penting tidak ada, WAJIB panggil tool `request_clarification`.

2. **WAJIB panggil tool** untuk setiap aksi. Jangan pernah hanya membalas teks tanpa memanggil tool yang sesuai.

3. **Ekstrak selengkap mungkin** dari pesan pengguna:
   - `items`: daftar item/jasa beserta qty dan harga satuan
   - `type`: 'income' (pemasukan) ATAU 'expense' (pengeluaran)
   - `category`: kategori yang relevan
   - `total_amount`: total nominal transaksi
   - `merchant_name`: nama toko/vendor jika ada
   - `transaction_date`: tanggal transaksi jika ada (format YYYY-MM-DD)
   - `payment_method`: metode pembayaran jika ada

4. **KLASIFIKASI DOKUMEN (jika dari struk/nota OCR):**
   - `FAKTUR_KREDIT`: Transaksi akrual → mencatat Utang Usaha (bukan Kas)
   - `NOTA_KONTAN` / `STRUK`: Transaksi kas langsung → mengurangi Kas
   - `KUITANSI`: Bukti pelunasan → mengurangi Utang/Piutang

5. **CHART OF ACCOUNTS (Bagan Akun):**
   Aset: 1-1001 Kas, 1-1002 Bank, 1-1020 Piutang Usaha, 1-1030 Persediaan Barang Dagang
   Liabilitas: 2-1010 Utang Usaha, 2-1020 Utang PPN
   Pendapatan: 4-1001 Pendapatan Penjualan
   Beban: 5-1001 HPP, 6-1010 Beban Konsumsi, 6-1020 Beban Operasional, 6-9999 Selisih Pembulatan

6. **JURNAL DOUBLE-ENTRY:**
   - Beli tunai: Debit Persediaan/Beban → Kredit Kas
   - Beli tempo/faktur: Debit Persediaan → Kredit Utang Usaha
   - Penjualan tunai: Debit Kas → Kredit Pendapatan Penjualan
   Total DEBIT harus = Total KREDIT

7. **Saat mencatat transaksi dari struk/nota OCR**, sertakan juga:
   - `document_type`: jenis dokumen
   - `invoice_number`: nomor faktur/nota
   - `vendor_name`: nama vendor/supplier
   - `tax_ppn`: jumlah PPN
   - `discount_total`: total diskon
   - `accounting_entries`: jurnal double-entry [{account_code, account_name, debit, credit}]
   - `is_math_verified`: apakah validasi matematis lolos
   - `math_discrepancy`: selisih pembulatan jika ada

8. **Gunakan Bahasa Indonesia** yang ramah dan informal (sapaan "Kak") saat berkomunikasi.

9. **Rekap Transaksi**: Jika pengguna meminta laporan/rekap, **WAJIB panggil tool `recap_transactions`**.

10. **Dashboard Visual**: Jika pengguna meminta link dashboard, **WAJIB panggil tool `get_dashboard_link`**.

═══════════════════════════════════════
RIWAYAT TRANSAKSI TENANT (RAG CONTEXT):
═══════════════════════════════════════
{rag_context}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Graph Nodes
# ─────────────────────────────────────────────────────────────────────────────

def get_model():
    """Returns Gemini model bound with agent tools."""
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.1,
    ).bind_tools(agent_tools)


def call_model(state: AgentState):
    """Agent node: invoke the LLM with system prompt + conversation history."""
    messages = list(state["messages"])

    # Build system message — RAG context is pre-injected as the first HumanMessage
    # We detect the injected context prefix and use it in the system prompt.
    rag_context = "Belum ada riwayat transaksi."
    if messages and isinstance(messages[0], HumanMessage):
        content = messages[0].content
        if content.startswith("RIWAYAT TRANSAKSI:\n"):
            # Extract RAG block and remove it from messages
            parts = content.split("\n\nPesan Pengguna: ", 1)
            if len(parts) == 2:
                rag_context = parts[0].replace("RIWAYAT TRANSAKSI:\n", "")
                messages[0] = HumanMessage(content=parts[1])

    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(rag_context=rag_context))
    model = get_model()
    response = model.invoke([system_msg] + messages)
    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    """Router: continue to tools or end the graph."""
    last_message = state["messages"][-1]
    if getattr(last_message, "tool_calls", None):
        return "continue"
    return "end"


def process_tool_results(state: AgentState):
    """
    Post-tool node: inspect tool results to determine final state.
    Handles SUCCESS and CLARIFICATION_NEEDED outcomes.
    """
    messages = state["messages"]
    requires_clarification = False
    final_response = ""

    # Scan all recent ToolMessages for outcomes
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            break
        content = msg.content or ""

        if content.startswith("CLARIFICATION_NEEDED:"):
            requires_clarification = True
            final_response = content.replace("CLARIFICATION_NEEDED: ", "").replace(
                "CLARIFICATION_NEEDED:", ""
            ).strip()
            break

        if content.startswith("SUCCESS:"):
            # Extract the human-readable part from the success message
            final_response = (
                "✅ Transaksi berhasil dicatat! Terima kasih, Kak. "
                "Transaksi sudah tersimpan di dashboard keuangan kamu. 📊"
            )
            break

        if content.startswith("ERROR:"):
            final_response = (
                f"⚠️ Maaf Kak, ada masalah saat menyimpan transaksi: "
                f"{content.replace('ERROR:', '').strip()}"
            )
            break

    return {
        "requires_clarification": requires_clarification,
        "final_response": final_response,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Build LangGraph
# ─────────────────────────────────────────────────────────────────────────────

workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("action", ToolNode(agent_tools))
workflow.add_node("process_result", process_tool_results)

workflow.set_entry_point("agent")
workflow.add_conditional_edges(
    "agent",
    should_continue,
    {"continue": "action", "end": END},
)
workflow.add_edge("action", "process_result")
workflow.add_edge("process_result", END)

agent_executor = workflow.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def process_message(
    tenant_id: str,
    user_id: Optional[str],
    message: str,
    rag_context: str = "",
) -> dict:
    """
    Main entry point for processing a WhatsApp user message via the agentic graph.

    Args:
        tenant_id:   The tenant (business) identifier.
        user_id:     The user's WhatsApp number (sender).
        message:     The (possibly transcribed) text content of the message.
        rag_context: Pre-retrieved RAG context string (50 past transactions).

    Returns:
        dict with keys:
            - requires_clarification (bool)
            - reply (str)
    """
    # Inject tenant/user context into the tool layer
    set_tool_context(tenant_id=tenant_id, user_id=user_id)

    # Build the initial message with injected RAG context
    if rag_context and rag_context != "Belum ada riwayat transaksi.":
        full_message = f"RIWAYAT TRANSAKSI:\n{rag_context}\n\nPesan Pengguna: {message}"
    else:
        full_message = message

    inputs: AgentState = {
        "messages": [HumanMessage(content=full_message)],
        "tenant_id": tenant_id,
        "user_id": user_id,
        "requires_clarification": False,
        "final_response": "",
    }

    result = agent_executor.invoke(inputs)

    reply = result.get("final_response", "")
    if not reply:
        # Fallback: use the last AI message content
        last_msg = result["messages"][-1]
        reply = getattr(last_msg, "content", "") or "Maaf Kak, ada masalah di sistem kami."

    return {
        "requires_clarification": result.get("requires_clarification", False),
        "reply": reply,
    }
