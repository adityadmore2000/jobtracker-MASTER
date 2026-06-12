#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="job-tracker-faster-whisper:cuda"
HF_CACHE_DIR="${HOME}/.cache/huggingface"
WORKSPACE="/workspace"

usage() {
  cat <<'EOF'
Usage:
  ./evaluation/fw_container.sh eval [evaluate.py args...]
  ./evaluation/fw_container.sh summary [summarize_results.py args...]
  ./evaluation/fw_container.sh shell
  ./evaluation/fw_container.sh exec [command...]
  ./evaluation/fw_container.sh --help

Example:
  ./evaluation/fw_container.sh eval \
    --model medium \
    --device cuda \
    --compute-type float16 \
    --vad off \
    --beam-size 5 \
    --run-label fw_medium_fp16_vad_off_prompt_off
EOF
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed or unavailable in PATH." >&2
    exit 1
  fi
}

require_image() {
  if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    cat >&2 <<EOF
Error: Docker image $IMAGE_NAME is missing.

Build it with:
  docker build \\
    -f docker/faster-whisper.Dockerfile \\
    -t $IMAGE_NAME .
EOF
    exit 1
  fi
}

run_container() {
  mkdir -p "$HF_CACHE_DIR"

  local tty_args=()
  if [ -t 0 ] && [ -t 1 ]; then
    tty_args=(-it)
  else
    tty_args=(-i)
  fi

  docker run --rm "${tty_args[@]}" \
    --gpus all \
    --ipc=host \
    --shm-size=2g \
    -v "$REPO_ROOT:$WORKSPACE" \
    -v "$HF_CACHE_DIR:/root/.cache/huggingface" \
    -w "$WORKSPACE" \
    "$IMAGE_NAME" \
    "$@"
}

cmd_eval() {
  run_container python3 evaluation/evaluate.py "$@"
}

cmd_summary() {
  run_container python3 evaluation/summarize_results.py "$@"
}

cmd_shell() {
  run_container bash
}

cmd_exec() {
  if [ "$#" -eq 0 ]; then
    echo "Usage: ./evaluation/fw_container.sh exec [command...]" >&2
    exit 1
  fi

  run_container "$@"
}

main() {
  local command="${1:---help}"
  case "$command" in
    --help|-h|help)
      usage
      ;;
    eval)
      shift
      require_docker
      require_image
      cmd_eval "$@"
      ;;
    summary)
      shift
      require_docker
      require_image
      cmd_summary "$@"
      ;;
    shell)
      shift
      require_docker
      require_image
      cmd_shell "$@"
      ;;
    exec)
      shift
      require_docker
      require_image
      cmd_exec "$@"
      ;;
    *)
      echo "Unknown command: $command" >&2
      usage
      exit 1
      ;;
  esac
}

main "$@"
