.PHONY: test smoke integration all

# Unit tests only (no server needed) — runs on pre-commit
smoke:
	python3 -m pytest tests/ --ignore=tests/test_api_integration.py -q --tb=short

# API integration tests (requires server on localhost:5020)
integration:
	python3 -m pytest tests/test_api_integration.py -v --tb=short

# Everything
test:
	python3 -m pytest tests/ -v --tb=short

all: smoke
