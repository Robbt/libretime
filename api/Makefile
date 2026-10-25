all: lint

include ../tools/python.mk

PIP_INSTALL := \
	--editable ../api-client \
	--editable ../shared \
	--editable .[dev,sentry]
PYLINT_ARG := libretime_api
MYPY_ARG := libretime_api
BANDIT_ARG := --exclude '*/tests/*' libretime_api || true

export DJANGO_SETTINGS_MODULE=libretime_api.settings.testing

format: .format
lint: .format-check .pylint .mypy .bandit
test: .pytest
test-coverage: .coverage
clean: .clean

SCHEMA_FILE ?= schema.yml

# Prettier formats the generated document, as it does every other yaml and json file in
# this repository. api/schema.yml is deliberately excluded from the pre-commit hook, so
# this is the only thing that ever formats it — which is why the version is pinned here
# rather than left to whatever npx happens to fetch: an unpinned formatter produces
# different bytes as of the formatter's next release, and the diff lands in whichever pull
# request happens to touch the api that week. Keep in step with the prettier rev in
# .pre-commit-config.yaml; tools/schema_pins_test.py fails if they drift.
PRETTIER_VERSION ?= 3.9.10

# Unconditional, and deliberately not `if command -v npx`: skipping the formatter when node
# is missing is what let an unformatted document be committed at all. The development image
# carries node (shared/packages.ini), so a failure here means the environment is genuinely
# not the one this repository expects, and there is nothing to silently fall back to.
schema: $(VENV)
	$(VENV)/bin/libretime-api spectacular --file $(SCHEMA_FILE)
	npx --yes prettier@$(PRETTIER_VERSION) --write $(SCHEMA_FILE)
