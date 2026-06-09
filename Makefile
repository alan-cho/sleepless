# Sleepless — common tasks. Run `make` or `make help` for the list.
PYTHON ?= python3

.PHONY: help run deps dev-deps test cov spec-check hooks icons package install restart uninstall clean

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

spec-check: ## Verify the spec documents every Config field (drift guard)
	$(PYTHON) scripts/check_spec_sync.py

hooks: ## Activate the git pre-commit spec-drift hook (.githooks)
	git config core.hooksPath .githooks
	@echo "pre-commit spec-drift hook active. Bypass once with: git commit --no-verify"

icons: ## Regenerate the menu-bar template icons from SF Symbols (macOS)
	$(PYTHON) scripts/make_icons.py

package: ## Build the standalone .app (py2app)
	$(PYTHON) setup.py py2app

install: ## Install sudoers + login agent + boot daemon (needs sudo)
	bash packaging/install.sh

restart: ## Restart the menu-bar app to reload code changes
	bash packaging/restart.sh

uninstall: ## Remove all components (restores normal sleep first)
	bash packaging/uninstall.sh

clean: ## Remove build/test artifacts
	rm -rf __pycache__ tests/__pycache__ .pytest_cache .coverage build dist *.egg-info
