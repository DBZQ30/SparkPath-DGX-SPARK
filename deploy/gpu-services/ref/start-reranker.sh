#!/bin/bash
source /etc/profile.d/conda.sh
conda activate ai

export CUDA_HOME=/usr/local/cuda
export PATH=$CUDA_HOME/bin:$CONDA_PREFIX/bin:$PATH

export VLLM_USE_DEEP_GEMM=0
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_DISABLED_KERNELS=CutlassFp8BlockScaledMMKernel
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export TRITON_CACHE_DIR=/home/<DEPLOY_USER>/.triton_cache

exec python -m vllm.entrypoints.openai.api_server \
  --model /home/models/models--Qwen--Qwen3-Reranker-0.6B/snapshots/e61197ed45024b0ed8a2d74b80b4d909f1255473 \
  --served-model-name qwen3-reranker \
  --api-key "${API_KEY:?请先设置环境变量 API_KEY}" \
  --dtype bfloat16 \
  --enforce-eager \
  --disable-custom-all-reduce \
  --gpu-memory-utilization 0.25 \
  --max-model-len 4096 \
  --host 0.0.0.0 \
  --port 8002 \
  --trust-remote-code \\
  

