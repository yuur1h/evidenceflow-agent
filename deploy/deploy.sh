#!/usr/bin/env bash
# Push the local checkout to the server and restart the service.
# The server cannot reach GitHub, so code goes over rsync instead of git pull.
#   ./deploy/deploy.sh            sync code + restart
#   ./deploy/deploy.sh --deps     also re-install Python dependencies
set -euo pipefail
cd "$(dirname "$0")/.."
HOST=${DEPLOY_HOST:-lighthouse}
DIR=${DEPLOY_DIR:-/opt/evidenceflow}

rsync -az --delete \
  --exclude .venv --exclude .env --exclude outputs --exclude logs --exclude data \
  --exclude __pycache__ --exclude .pytest_cache --exclude BASIC_AUTH.txt \
  ./ "$HOST:$DIR/"

if [[ "${1:-}" == "--deps" ]]; then
  # Mirrors: the server cannot reach GitHub or huggingface.co directly.
  ssh "$HOST" "cd $DIR && export PATH=\$HOME/.local/bin:\$PATH UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple && uv pip install -q -r pyproject.toml"
fi

ssh "$HOST" "sudo systemctl restart evidenceflow && sleep 5 && systemctl is-active evidenceflow && curl -s -o /dev/null -w 'local / -> %{http_code}\n' http://127.0.0.1:8000/"
