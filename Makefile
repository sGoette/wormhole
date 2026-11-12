PY      ?= python3
ARGS    ?= info

.PHONY: help check test run deploy deploy-dry rollback nas-status nas-logs nas-ssh

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

check: ## Phase 0 prerequisite check on the Mac
	@scripts/check-prereqs.sh

test: ## Run unit tests locally
	PYTHONPATH=src $(PY) -m unittest discover -s tests -v

run: ## Run wormhole locally (make run ARGS="info")
	WORMHOLE_ENV=config/local.env bin/wormhole $(ARGS)

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
