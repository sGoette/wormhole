PY      ?= python3
ARGS    ?= info

.PHONY: help check test run turntable owntone-build owntone-run web feed

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

check: ## Phase 0 prerequisite check on the Mac
	@scripts/check-prereqs.sh

test: ## Run unit tests locally
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

run: ## Run wormhole locally (make run ARGS="info")
	WORMHOLE_ENV=config/local.env bin/wormhole $(ARGS)

turntable: ## Fake turntable web UI on :8765
	$(PY) tools/fake_turntable.py

owntone-build: ## Build OwnTone into .local/owntone (Mac, once)
	tools/owntone-mac/build.sh

owntone-run: ## Run local OwnTone in the foreground (web UI :3689)
	tools/owntone-mac/run.sh

web: ## Web UI: speakers, volumes, feed (http://localhost:8766)
	WORMHOLE_ENV=config/local.env bin/wormhole web

feed: ## Feed PCM into OwnTone's pipe (make feed INPUT=tone|file|URL)
	WORMHOLE_ENV=config/local.env bin/wormhole feed $(if $(INPUT),--input "$(INPUT)")
