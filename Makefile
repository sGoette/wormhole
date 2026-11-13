PY      ?= python3
ARGS    ?= info

.PHONY: help check test run turntable owntone-build owntone-run feed \
        deploy deploy-dry rollback nas-status nas-logs nas-ssh

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

feed: ## Feed PCM into OwnTone's pipe (make feed INPUT=tone|file|URL)
	WORMHOLE_ENV=config/local.env bin/wormhole feed $(if $(INPUT),--input "$(INPUT)")

deploy: test ## Test, then push a new release to the NAS
	@deploy/deploy.sh deploy

deploy-dry: ## Show what a deploy would copy
	@deploy/deploy.sh dry-run

rollback: ## Switch NAS back to the previous release
	@deploy/deploy.sh rollback

nas-status: ## Show live release on the NAS
	@deploy/deploy.sh status

nas-logs: ## Tail the service log on the NAS
	@deploy/deploy.sh logs

nas-ssh: ## Open a shell on the NAS
	@deploy/deploy.sh ssh
