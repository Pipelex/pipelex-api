#################################################################################
#################################    HELPERS    #################################
#################################################################################

LOCAL_IMAGE     := pipelex-api:local
ENV_FILE        ?= .env
CONTAINER_NAME  ?= pipelex-api

define HELP_LOCAL
	$(GREEN)Run the API locally — two ways:$(RESET)

	$(YELLOW)Native (uvicorn, hot reload — fastest dev loop):$(RESET)
	make run$(RESET):           Run the API with uvicorn (requires `make install` first).

	$(YELLOW)Docker (closest to what's deployed):$(RESET)
	make docker-build$(RESET):  Build the API image from local source.
	make docker-run$(RESET):    Build + run the image on http://localhost:8081 (foreground, Ctrl+C to stop).
	make docker-stop$(RESET):   Force-stop the Docker container.
	make docker-logs$(RESET):   Tail Docker container logs.

	$(YELLOW)Local Temporal dev server (when TEMPORAL is enabled in config):$(RESET)
	make temporal-server$(RESET) ($(GREEN)ts$(RESET)):           Start a local Temporal dev server (:7233, UI :8233) WITH search attributes registered.
	make temporal-server-bare$(RESET) ($(GREEN)ts-bare$(RESET)): Start it WITHOUT search attributes (reproduce the missing-attribute error).
	make temporal-stop$(RESET) ($(GREEN)tstop$(RESET)):          Stop the local Temporal dev server.

endef
export HELP_LOCAL

#################################################################################
#################################    API    #################################
#################################################################################

# Build the generic pipelex-api image from local source.
docker-build:
	@echo "\n=== Build $(LOCAL_IMAGE) from local source ==="
	docker build --platform linux/amd64 -t $(LOCAL_IMAGE) .

# Run the API on http://localhost:8081, foreground. Reads all env from .env.
# Required: PIPELEX_GATEWAY_API_KEY (only if you use the default routing profile).
# Optional: AUTH_MODE, API_KEY, JWT_SECRET_KEY (see .env.example).
docker-run: docker-build
	@docker rm -f $(CONTAINER_NAME) 2>/dev/null || true
	@test -f $(ENV_FILE) || (echo "ERROR: $(ENV_FILE) not found. Copy .env.example to .env and fill it in." && exit 1)
	@echo "\n=== Run $(LOCAL_IMAGE) on http://localhost:8081  —  Ctrl+C to stop ==="
	docker run --rm --name $(CONTAINER_NAME) -p 8081:8081 \
		--env-file $(ENV_FILE) \
		$(LOCAL_IMAGE)

docker-stop:
	@docker rm -f $(CONTAINER_NAME) 2>/dev/null && echo "Stopped $(CONTAINER_NAME)" || echo "$(CONTAINER_NAME) was not running"

docker-logs:
	docker logs -f $(CONTAINER_NAME)

#################################################################################
#################################   TEMPORAL   #################################
#################################################################################

# Pipelex attaches these five custom Keyword search attributes to every workflow
# start. A namespace missing them rejects StartWorkflow with "Namespace <ns> has
# no mapping defined for search attribute ...". `temporal-server` pre-registers
# them so /pipeline/execute and /pipeline/start work out of the box;
# `temporal-server-bare` omits them so you can reproduce the missing-attribute
# error (SearchAttributeRegistrationError). Keep in lockstep with
# [temporal.search_attributes].attributes in the pipelex config.
TEMPORAL_SEARCH_ATTRS := PipeCode PipelineRunId SessionId UserId DomainCode

# Start a local Temporal dev server WITH the search attributes registered. Only
# needed when the API runs with TEMPORAL enabled (selected_server pointed at the
# local server). Runs in the foreground — start it in its own terminal.
temporal-server:
	@if ! command -v temporal >/dev/null 2>&1; then \
		echo "ERROR: 'temporal' CLI not found. Install it with: brew install temporal"; \
		exit 1; \
	fi
	@echo "• Temporal Web UI at http://localhost:8233"
	@echo "• Temporal gRPC service at localhost:7233"
	@echo "• Registering search attributes: $(TEMPORAL_SEARCH_ATTRS)"
	@echo "• Press Ctrl+C to stop"
	temporal server start-dev $(foreach attr,$(TEMPORAL_SEARCH_ATTRS),--search-attribute $(attr)=Keyword)

ts: temporal-server

# Start the dev server WITHOUT the search attributes, to reproduce the
# missing-search-attribute error on /pipeline/execute and /pipeline/start.
temporal-server-bare:
	@if ! command -v temporal >/dev/null 2>&1; then \
		echo "ERROR: 'temporal' CLI not found. Install it with: brew install temporal"; \
		exit 1; \
	fi
	@echo "• Temporal Web UI at http://localhost:8233"
	@echo "• Temporal gRPC service at localhost:7233"
	@echo "• NO search attributes registered — workflow starts are rejected when [temporal.search_attributes].enabled = true (use this to test the error path)"
	@echo "• Press Ctrl+C to stop"
	temporal server start-dev

ts-bare: temporal-server-bare
tsb: temporal-server-bare

# Stop the local Temporal dev server (kills whatever is listening on :7233).
temporal-stop:
	@PID=$$(lsof -tiTCP:7233 -sTCP:LISTEN 2>/dev/null); \
	if [ -z "$$PID" ]; then \
		echo "• No process found on port 7233"; \
	else \
		kill $$PID && echo "• Killed Temporal server (PID $$PID)"; \
	fi

tstop: temporal-stop
