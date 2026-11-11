"""Verified public list prices and arithmetic on actual provider usage only.

No tokenizer, payload byte estimate, free-quota assumption or inference call in this module.
Project reservations live separately in jobs.py; this is neither a quote of unknown token
totals nor a provider invoice. Evidence snapshots are in reports/core-evidence/pricing-r9.
"""

from decimal import Decimal
from typing import Any

from apex_rag.schemas import ModelPrice, PricingCatalog, UsageCost

PRICE_URL = "https://help.aliyun.com/zh/model-studio/model-pricing"
PRICE_SHA = "1a31681cdaba44befffef008ff744c9b234da52c86837c0228225d2fc2c239d8"
PRICE_PER_MILLION = {"text-embedding-v4": .5, "qwen3-rerank": .5}


def official_pricing(region: str = "beijing") -> PricingCatalog:
    """Return the2026-11-10verified Beijing synchronous-online public tariff snapshot.

    applicability is explicit: a Beijing price must not silently price Singapore usage.
    Our synchronous multi-text request is not the discounted asynchronous Batch product.
    """
    return PricingCatalog(version="aliyun-cn-beijing-online-2026-11-10", verified_on="2026-11-10",
        applicable=region == "beijing", models=[
            ModelPrice(model="text-embedding-v4", input_price=.5,
                billing_rule="按输入 Token 计费，输出不计费；多条文本的输入 Token 合计。当前同步请求不适用异步 Batch 折扣。",
                source_url=PRICE_URL, source_sha256=PRICE_SHA,
                protocol_url="https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api",
                protocol_sha256="9408ef64d6ae2a8c374664a0e3469e97dc9ed82c4dd984c8d5e6623d2b6c1a1b"),
            ModelPrice(model="qwen3-rerank", input_price=.5,
                billing_rule="按输入 Token 计费，输出不计费。请求输入为 Query Tokens × Document 数量 + Document Tokens 总和；真实 usage.total_tokens 已包含计量总数，计算费用时不再乘文档数。",
                source_url=PRICE_URL, source_sha256=PRICE_SHA,
                protocol_url="https://help.aliyun.com/zh/model-studio/text-rerank-api",
                protocol_sha256="6cfc8e56bfc152a1345abeb416d5860676bd7388d37ea9ca3481f3bbad7c867e")],
        notes=["官方公开单价，不是任务总费用报价。执行前准确 Token 及候选未知，总费用为未知。",
               "执行后仅以真实供应商 usage 按单价折算用量费用；不等于实际账单，不推定免费额度、折扣或税务结算。",
               "核验范围为中国内地华北2（北京）的同步在线调用；其他地域、Batch 产品及其他模型不在本次报价范围。",
               "UTF-8 字节安全预留、重试余量及3/12元硬上限均为项目预算，不是官方报价。",
               "核验日期不是供应商承诺的价格有效期；价格可能变更，应以官方最新价格及账单为准。"])


def usage_cost(records: list[dict[str, Any]] | None, *, pricing: PricingCatalog,
               terminal: bool, logical_calls: int = 0, pending: bool = False,
               legacy: bool = False, zero_call_confirmed: bool = False,
               http_attempts: int | None = None) -> UsageCost:
    """Value observable usage once; any uncovered request/attempt keeps the total unknown.

    total_tokens is the native protocol's input accounting field for these two models.
    With retries, the final usage cannot prove the earlier attempt had no charge; retain
    that coverage hole even when all returned usage can be arithmetically valued.
    """
    result = UsageCost(valuation_basis="current_verified_prices_for_legacy_usage" if legacy else "provider_usage_at_verified_unit_prices",
        notes=["可核算部分仅按真实 usage.total_tokens × 官方输入单价 ÷ 1000000；每份 usage 只计算一次。",
               "实际账单未知；失败、重试未报告用量及缺失记录不能记为免费。"])
    if legacy:
        result.notes.append("历史任务按当前核验的官方单价折算，不证明执行当时适用费率，未改写历史原始记录。")
    if zero_call_confirmed and terminal and not pending and not records and logical_calls == 0 and http_attempts in (None, 0):
        result.status = "complete"
        result.known_usage_cost_cny = result.total_usage_cost_cny = 0.0
        result.notes.append("已确认没有模型调用；零调用不等于缺失 usage。")
        return result
    if not pricing.applicable:
        result.notes.append("任务地域不在已核验费率范围，无法计算用量费用。")
        result.unknown_calls = max(logical_calls, len(records or []))
        return result
    if records is None:
        result.unknown_calls = logical_calls
        result.notes.append("供应商调用账本缺失或不可读，总费用未知。")
        return result
    prices = {p.model: p for p in pricing.models}
    known = Decimal(0)
    for record in records:
        model = record.get("model")
        usage = record.get("usage")
        tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        has_usage = isinstance(model, str) and model in prices and type(tokens) is int and tokens >= 0
        if has_usage:
            assert isinstance(model, str) and isinstance(tokens, int)
            price = prices[model]
            known += Decimal(tokens) * Decimal(str(price.input_price)) / Decimal(price.unit_tokens)
            result.known_tokens_by_model[model] = result.known_tokens_by_model.get(model, 0) + tokens
            result.known_calls += 1
        # A reported final usage does not cover previous retries or failed envelopes.
        if not has_usage or record.get("status") != "completed" or type(record.get("attempts")) is not int or record.get("attempts") != 1:
            result.unknown_calls += 1
    result.unknown_calls += max(0, logical_calls - len(records))
    covered_attempts = sum(r["attempts"] for r in records if type(r.get("attempts")) is int and r["attempts"] >= 0)
    if http_attempts is not None and http_attempts != covered_attempts:
        result.unknown_calls += 1
        result.notes.append("持久化HTTP尝试数与调用账本覆盖不一致，总费用未知。")
    if pending:
        result.unknown_calls += 1
        result.notes.append("存在未确认完成的在途请求意图，总费用未知。")
    if result.known_calls:
        result.known_usage_cost_cny = float(known)
        result.status = "partial"
    if terminal and records and not result.unknown_calls and len(records) == logical_calls:
        result.status = "complete"
        result.total_usage_cost_cny = float(known)
    elif not terminal:
        result.notes.append("任务尚未结束；后续用量未知，当前金额仅为已报告用量部分。")
    return result
