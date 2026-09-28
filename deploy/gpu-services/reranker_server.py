#!/usr/bin/env python3
"""
Qwen3-Reranker FastAPI Server
Standard /v1/rerank API (Jina/Cohere compatible)
Based on bigtt's reranker_server.py
"""

import os
import torch
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field
from transformers import AutoModelForCausalLM, AutoTokenizer
import uvicorn

API_KEY = os.getenv("API_KEY", "")
MODEL_PATH = os.getenv("MODEL_PATH", "/home/models/models--Qwen--Qwen3-Reranker-0.6B/snapshots/e61197ed45024b0ed8a2d74b80b4d909f1255473")
DEFAULT_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)
# 监听地址：默认仅本机(127.0.0.1)；生产由 systemd 经 HOST 传入 GPU 主机地址
HOST = os.getenv("HOST", "127.0.0.1")

app = FastAPI(title="Qwen3-Reranker API", version="1.0.0")

tokenizer = None
model = None
token_true_id = None
token_false_id = None
prefix_tokens = None
suffix_tokens = None


class Document(BaseModel):
    text: str


class RerankRequest(BaseModel):
    model: str = "qwen3-reranker"
    query: str
    documents: List[str] = Field(..., min_length=1)
    top_n: Optional[int] = None
    return_documents: Optional[bool] = True
    instruction: Optional[str] = None


class RerankResult(BaseModel):
    index: int
    document: Optional[str] = None
    relevance_score: float


class RerankResponse(BaseModel):
    model: str
    results: List[RerankResult]
    usage: dict
    object: str = "rerank"


def format_instruction(instruction: str, query: str, doc: str) -> str:
    return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {doc}"


def compute_scores(queries: List[str]) -> List[float]:
    global tokenizer, model, token_true_id, token_false_id, prefix_tokens, suffix_tokens

    # 分块批处理（与 embedding_server 的 batch_size=16 对齐）：DGX 统一内存平台上，
    # 整批 pad 到最长序列会让 caching allocator 高水位随历史峰值单调增长（内存棘轮，
    # 2026-09-27 排障结论：reranker GPU 占用曾涨到 16.9 GiB 并诱发整机 OOM）。
    # rerank 各对独立打分，分块数学等价；每批后 empty_cache 归还临时块。
    batch_size = 16
    max_length = 8192
    scores: List[float] = []

    for i in range(0, len(queries), batch_size):
        batch = queries[i : i + batch_size]
        input_ids_list = []
        for q in batch:
            ids = tokenizer.encode(
                q,
                add_special_tokens=False,
                max_length=max_length - len(prefix_tokens) - len(suffix_tokens),
                truncation=True,
            )
            ids = prefix_tokens + ids + suffix_tokens
            input_ids_list.append(ids)

        max_len = min(max(len(ids) for ids in input_ids_list), max_length)
        input_ids = torch.tensor(
            [
                ids[:max_len] + [0] * (max_len - len(ids[:max_len]))
                for ids in input_ids_list
            ],
            dtype=torch.long,
        )
        attention_mask = torch.tensor(
            [
                [1] * min(len(ids), max_len) + [0] * (max_len - min(len(ids), max_len))
                for ids in input_ids_list
            ],
            dtype=torch.long,
        )

        inputs = {
            "input_ids": input_ids.to(model.device),
            "attention_mask": attention_mask.to(model.device),
            # 只取最后一位 logits 做 yes/no 打分：不加此参数时 transformers 会为
            # 全部位置计算全词表 logits（16 批 × 8192 位 × 152k 词表 × fp32 ≈ 10 GiB
            # 瞬时张量），是 2026-09-27 整机 OOM 的主因（见 README 排障记录）
            "logits_to_keep": 1,
        }

        with torch.no_grad():
            batch_scores = model(**inputs).logits[:, -1, :]
            true_vector = batch_scores[:, token_true_id]
            false_vector = batch_scores[:, token_false_id]
            batch_scores = torch.stack([false_vector, true_vector], dim=1)
            batch_scores = torch.nn.functional.log_softmax(batch_scores, dim=1)
            scores.extend(batch_scores[:, 1].exp().tolist())
        del inputs, input_ids, attention_mask, batch_scores

    torch.cuda.empty_cache()
    return scores


@app.on_event("startup")
async def startup():
    global tokenizer, model, token_true_id, token_false_id, prefix_tokens, suffix_tokens

    print(f"Loading model from {MODEL_PATH}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, padding_side="left")
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH, torch_dtype=torch.float32, device_map="auto"
    ).eval()

    token_false_id = tokenizer.convert_tokens_to_ids("no")
    token_true_id = tokenizer.convert_tokens_to_ids("yes")

    prefix_tokens = tokenizer.encode("yes/no", add_special_tokens=False)
    bos_id = (
        tokenizer.bos_token_id
        if tokenizer.bos_token_id is not None
        else tokenizer.pad_token_id
    )
    prefix_tokens = [bos_id] + prefix_tokens
    suffix_tokens = [tokenizer.eos_token_id]

    print(f"Model loaded. Token IDs - yes: {token_true_id}, no: {token_false_id}")


@app.get("/health")
async def health():
    return {"status": "healthy", "model": "qwen3-reranker"}


@app.post("/rerank", response_model=RerankResponse)
async def rerank_infinity(request: RerankRequest, authorization: str = Header(None)):
    return await rerank(request, authorization)


@app.get("/v1/models")
async def list_models(authorization: str = Header(None)):
    if authorization and authorization.replace("Bearer ", "") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {
        "object": "list",
        "data": [{"id": "qwen3-reranker", "object": "model", "owned_by": "qwen"}],
    }


@app.post("/v1/rerank", response_model=RerankResponse)
async def rerank(request: RerankRequest, authorization: str = Header(None)):
    if authorization and authorization.replace("Bearer ", "") != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    instruction = request.instruction or DEFAULT_INSTRUCTION
    pairs = [
        format_instruction(instruction, request.query, doc) for doc in request.documents
    ]

    scores = compute_scores(pairs)

    results = [
        RerankResult(
            index=i,
            document=request.documents[i] if request.return_documents else None,
            relevance_score=score,
        )
        for i, score in enumerate(scores)
    ]

    results.sort(key=lambda x: x.relevance_score, reverse=True)

    if request.top_n:
        results = results[: request.top_n]

    total_tokens = sum(len(tokenizer.encode(p)) for p in pairs)

    return RerankResponse(
        model=request.model,
        results=results,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8002))
    uvicorn.run(app, host=HOST, port=port)
