#!/bin/bash
export HOME=/home/<DEPLOY_USER>
export PATH=/home/<DEPLOY_USER>/mineru-env/bin:/home/<DEPLOY_USER>/.local/bin:/usr/local/cuda/bin:$PATH
export CUDA_HOME=/usr/local/cuda
export MINERU_HOST=<GPU_HOST_IP>
export MINERU_PORT=8005
export MINERU_API_KEY="${MINERU_API_KEY:?请先设置环境变量 MINERU_API_KEY}"
export MINERU_API_OUTPUT_ROOT=/home/<DEPLOY_USER>/mineru_output
export MINERU_API_TASK_RETENTION_SECONDS=86400
export MINERU_API_ENABLE_VLM_PRELOAD=true
export MINERU_MODEL_SOURCE=local
export MINERU_API_MAX_CONCURRENT_REQUESTS=3

exec /home/<DEPLOY_USER>/mineru-env/bin/python -m mineru.cli.fast_api --host <GPU_HOST_IP> --port 8005
