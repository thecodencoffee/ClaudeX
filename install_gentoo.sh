#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if [[ ! -f .env ]]; then cp .env.example .env; chmod 600 .env; fi
echo
echo "ClaudeX dependencies installed. Edit .env or use Settings in the web app."
echo "Start with: source .venv/bin/activate && python app.py"
