# Local dev: run the FastAPI backend on your machine against real Azure resources,
# and serve the Vite web player at http://localhost:5173 pointed at it.
#
#   make env       copy the deployed API's config into .azure/backend.env (needs `az login`)
#   make dev       backend on :8000 + Vite on :5173, Ctrl+C stops both
#   make backend   backend only
#   make web       Vite only, proxied to the local backend
#   make web-remote  Vite only, proxied to a deployed API (no local backend)
#   make install   uv sync + npm ci
#   make test      Python and web unit tests
#   make clean-env delete .azure/backend.env so the next run fetches it again
#
# The API container app is discovered in the current `az` subscription. Override with
#   make env API_APP=ca-api-xxxx API_RG=rg-xxxx

ENV_FILE    := .azure/backend.env
PORT        ?= 8000
API_APP     ?=
API_RG      ?=
REMOTE_API  ?= https://ca-api-bhdr37v3onmcs.thankfulglacier-9473c5b7.eastus2.azurecontainerapps.io

# Managed-identity and container-only settings that must not leak into a local run
# (AZURE_CLIENT_ID would point DefaultAzureCredential at an identity your laptop can't use).
ENV_SKIP := AZURE_CLIENT_ID|MATCHLENS_SERVICE|OTEL_SERVICE_NAME|APPLICATIONINSIGHTS_CONNECTION_STRING

.PHONY: help install env backend web web-remote dev test clean-env

help:
	@sed -n '1,15p' Makefile | sed 's/^# \{0,1\}//'

install:
	uv sync
	cd web && npm ci

env: $(ENV_FILE)

$(ENV_FILE):
	@az account show >/dev/null 2>&1 || { echo "Run 'az login' first."; exit 1; }
	@app="$(API_APP)"; rg="$(API_RG)"; \
	if [ -z "$$app" ]; then \
	  app="$$(az containerapp list --query "[?starts_with(name,'ca-api-')] | [0].name" -o tsv)"; \
	  rg="$$(az containerapp list --query "[?starts_with(name,'ca-api-')] | [0].resourceGroup" -o tsv)"; \
	elif [ -z "$$rg" ]; then \
	  rg="$$(az containerapp list --query "[?name=='$$app'] | [0].resourceGroup" -o tsv)"; \
	fi; \
	[ -n "$$app" ] && [ -n "$$rg" ] || { echo "No ca-api-* container app in subscription '$$(az account show --query name -o tsv)'. Pass API_APP= / API_RG= or 'az account set'."; exit 1; }; \
	echo "Pulling config from $$app ($$rg)"; \
	mkdir -p $(dir $(ENV_FILE)); \
	az containerapp show -n "$$app" -g "$$rg" \
	  --query "properties.template.containers[0].env[?value!=null].[name,value]" -o tsv \
	  | awk -F'\t' '$$1 !~ /^($(ENV_SKIP))$$/ { print $$1 "=" $$2 }' > $(ENV_FILE); \
	echo "CORS_DEV=1" >> $(ENV_FILE); \
	echo "Wrote $(ENV_FILE) ($$(wc -l < $(ENV_FILE) | tr -d ' ') vars)"

clean-env:
	rm -f $(ENV_FILE)

backend: $(ENV_FILE)
	PORT=$(PORT) uv run --env-file $(ENV_FILE) python -m matchlens.services.run api

web:
	cd web && DEV_API_PROXY=http://localhost:$(PORT) npm run dev

web-remote:
	cd web && DEV_API_PROXY=$(REMOTE_API) npm run dev

dev: $(ENV_FILE)
	@trap 'kill 0' INT TERM EXIT; \
	$(MAKE) --no-print-directory backend & \
	$(MAKE) --no-print-directory web & \
	wait

test:
	uv run pytest
	cd web && npm test
