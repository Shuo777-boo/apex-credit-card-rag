"""Explicit small real answer validation; never mislabels AI checks as human review."""

from typing import Any

from apex_rag.evaluation.runner import write_json
from apex_rag.schemas import SearchRequest
from apex_rag.services import Services


def verify_answers(services: Services) -> dict[str, Any]:
    """Generate three real diagnostic answers, save citations/latency and manual-review slots.

    Uses no gold responses or relevance IDs as retrieval input. This checks execution and
    citation mappings only; actual policy support/insufficiency requires independent review.
    """
    queries = [
        "What is the balance transfer fee for the Everyday Freedom card?",
        "Does Apex Preferred Travel provide Priority Pass lounge access and guest passes?",
        "What mortgage interest rate and home purchase down payment can Apex guarantee me?",
    ]
    records = []
    for query in queries:
        response = services.answer.answer(SearchRequest(query=query, mode="hybrid_rerank", top_k=5))
        records.append({"query": query, "response": response.model_dump(mode="json"),
            "human_review": {"status": "pending", "reviewer": None, "faithfulness": None,
                "correct_product_scope": None, "conditions_complete": None, "notes": None}})
    output = {"status": "generated_pending_human_review", "models": services.settings.public_models(),
        "samples": records, "calls": services.http.calls,
        "limitation": "Citation identifier validity and model self-refusal do not prove semantic faithfulness."}
    path = services.settings.path(services.settings.reports_dir) / "core-answer-validation.json"
    write_json(path, output)
    return {"artifact": str(path), "status": output["status"], "samples": len(records)}
