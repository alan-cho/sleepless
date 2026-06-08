# Sleepless — common tasks. Run `make` or `make help` for the list.
PYTHON ?= python3

.PHONY: help run deps dev-deps test cov package install uninstall clean

help: ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | sort

run: ## Run the app from source (menu bar)
	$(PYTHON) sleepless.py

deps: ## Install runtime deps (rumps, psutil)
	$(PYTHON) -m pip install --user -r requirements.txt

dev-deps: ## Install test deps (pytest, pytest-cov)
	$(PYTHON) -m pip install --user -r requirements-dev.txt

test: ## Run the unit tests
	$(PYTHON) -m pytest

cov: ## Run tests with the coverage gate (>=85% of the core)
	$(PYTHON) -m pytest --cov=sleepless --cov-report=term-missing --cov-fail-under=85

package: ## Build the standalone .app (py2app)
	$(PYTHON) setup.py py2app

install: ## Install sudoers + login agent + boot daemon (needs sudo)
	bash packaging/install.sh

uninstall: ## Remove all components (restores normal sleep first)
	bash packaging/uninstall.sh

clean: ## Remove build/test artifacts
	rm -rf __pycache__ tests/__pycache__ .pytest_cache .coverage build dist *.egg-info
