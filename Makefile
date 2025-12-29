PY ?= python3
VENV ?= ./venv
PIP := $(VENV)/bin/pip
UVICORN := $(VENV)/bin/uvicorn

.PHONY: setup dev-api dev-web lint fmt type clean

$(VENV)/bin/activate:
	@test -d $(VENV) || $(PY) -m venv $(VENV)
	@$(PIP) install -U pip
	@$(PIP) install -r requirements.txt

setup: $(VENV)/bin/activate ## Create venv and install deps (no Docker)

dev-api: ## Run FastAPI with auto-reload
	@test -x $(UVICORN) || ($(PY) -m venv $(VENV) && $(PIP) install -r requirements.txt)
	$(UVICORN) app.main:app --reload

dev-web: ## Serve ./web locally on http://localhost:5173
	@cd web && python3 -m http.server 5173

lint: ## Run Ruff
	@$(VENV)/bin/ruff check . || true

fmt: ## Format with Black and isort
	@$(VENV)/bin/black .
	@$(VENV)/bin/isort .

type: ## Type-check (optional)
	@$(VENV)/bin/mypy app agents speech || true

clean: ## Remove caches and temporary files
	rm -rf **/__pycache__ .mypy_cache .ruff_cache .pytest_cache
	mkdir -p tmp && rm -f tmp/*
