#!/usr/bin/env python3
"""
Qwen3-Embedding FastAPI Server (transformers, GPU)
Compatible with /v1/embeddings API
Based on bigtt's embedding_server_cpu.py
"""

import os
import torch
from typing import List, Optional, Union
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from transformers import AutoModel, AutoTokenizer
import uvicorn

API_KEY = os.getenv("API_KEY", "")
MODEL_PATH = os.getenv("MODEL_PATH", "/home/models/models--Qwen--Qwen3-Embedding-0.6B/snapshots/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3")
PORT = int(os.getenv("PORT", 8001))
# 监听地址：默认仅本机(127.0.0.1)；生产由 systemd 经 HOST 传入 GPU 主机地址
HOST = os.getenv("HOST", "127.0.0.1")

app = FastAPI(title="Qwen3-Embedding API", version="1.0.0")

tokenizer = None
model = None


class EmbeddingRequest(BaseModel):
    model: str = "qwen3-embedding"
    input: Union[str, List[str]]
    encoding_format: Optional[str] = "float"


class EmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: List[float]
    index: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: List[EmbeddingData]
    model: str
    usage: dict


def last_token_pooling(last_hidden_states, attention_mask):
    left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]


def get_detailed_instruct(task_description: str, query: str) -> str:
    return f"Instruct: {task_description}\nQuery: {query}"


@app.on_event("startup")
async def startup():
    global tokenizer, model
    print(f"Loading embedding model from {MODEL_PATH}...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, padding_side="left")
    model = AutoModel.from_pretrained(MODEL_PATH, torch_dtype=torch.float32, device_map="auto").eval()
    print(f"Model loaded. Device: CUDA")


@app.get("/health")
async def health():
    dev = "not-loaded"
    if model is not None:
        try:
            dev = str(next(model.parameters()).device)
        except Exception:
            dev = "unknown"
    return {"status": "healthy", "model": "qwen3-embedding", "device": dev}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [{"id": "qwen3-embedding", "object": "model", "owned_by": "qwen"}],
    }


@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest):
    texts = [request.input] if isinstance(request.input, str) else request.input
    task_desc = "Given a web search query, retrieve relevant passages that answer the query"
    texts = [get_detailed_instruct(task_desc, t) for t in texts]

    batch_size = 16
    all_embeddings = []
    total_tokens = 0

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        inputs = tokenizer(batch, padding=True, truncation=True, max_length=512, return_tensors="pt")
        total_tokens += inputs["attention_mask"].sum().item()

        inputs = {k: v.to(model.device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = model(**inputs)
            embeddings = last_token_pooling(outputs.last_hidden_state, inputs["attention_mask"])
            embeddings = torch.nn.functional.normalize(embeddings, p=2, dim=1)
            all_embeddings.extend(embeddings.tolist())
        del inputs, outputs

    # 统一内存平台：caching allocator 高水位只增不减（2026-09-27 排障结论，
    # embedding GPU 占用曾涨到 7.4 GiB），批处理完立即归还临时块。
    torch.cuda.empty_cache()

    data = [
        EmbeddingData(object="embedding", embedding=emb, index=i)
        for i, emb in enumerate(all_embeddings)
    ]

    return EmbeddingResponse(
        object="list",
        data=data,
        model=request.model,
        usage={"prompt_tokens": total_tokens, "total_tokens": total_tokens},
    )


if __name__ == "__main__":
    uvicorn.run(app, host=HOST, port=PORT)
