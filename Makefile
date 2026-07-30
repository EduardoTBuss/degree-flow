# Degree Flow — atalhos (Linux/macOS/Git-Bash). Windows: use ./run.ps1
PY := backend/.venv/bin/python
PORT ?= 8000

.PHONY: run backend-deps frontend-build dev-backend dev-frontend test clean

## run: bootstrap completo + sobe API+SPA em http://localhost:8000
run: backend-deps frontend-build
	$(PY) -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $(PORT)

backend-deps:
	test -d backend/.venv || python3 -m venv backend/.venv
	$(PY) -m pip install --quiet --upgrade pip
	$(PY) -m pip install --quiet -r backend/requirements.txt

frontend-build:
	test -d frontend/dist || (cd frontend && npm ci && npm run build)

## dev-backend: backend com autoreload (API em :8000)
dev-backend: backend-deps
	$(PY) -m pip install --quiet -r backend/requirements-dev.txt
	$(PY) -m uvicorn app.main:app --app-dir backend --reload --port $(PORT)

## dev-frontend: Vite dev server com proxy p/ o backend (UI em :5173)
dev-frontend:
	cd frontend && npm ci && npm run dev

## test: roda a suíte pytest (motor + contrato de API)
test: backend-deps
	$(PY) -m pip install --quiet -r backend/requirements-dev.txt
	cd backend && .venv/bin/python -m pytest

clean:
	rm -rf data frontend/dist backend/.venv frontend/node_modules
