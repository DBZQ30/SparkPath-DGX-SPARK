#!/bin/bash
source /etc/profile.d/conda.sh
conda activate ai

# SM120 (Blackwell workstation) compatibility
export CUDA_HOME=/usr/local/cuda
export PATH=$CUDA_HOME/bin:$CONDA_PREFIX/bin:$PATH

export VLLM_USE_DEEP_GEMM=0
export VLLM_USE_FLASHINFER_SAMPLER=0
export VLLM_DISABLED_KERNELS=CutlassFp8BlockScaledMMKernel
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export VLLM_WORKER_MULTIPROC_METHOD=spawn
export TRITON_CACHE_DIR=/home/<DEPLOY_USER>/.triton_cache

exec python -m vllm.entrypoints.openai.api_server \
  --model /home/models/models--cyankiwi--Qwen3-VL-8B-Instruct-AWQ-4bit/snapshots/61fa5a2b38f3d57bed938568adb30ddd6224201f \
  --served-model-name qwen3-vl \
  --api-key "${API_KEY:?请先设置环境变量 API_KEY}" \
  --dtype bfloat16 \
  --enforce-eager \
  --disable-custom-all-reduce \
  --gpu-memory-utilization 0.45 \
  --max-model-len 4096 \
  --max-num-seqs 4 \
  --host <GPU_HOST_IP> \
  --port 8006 \
  --trust-remote-code

