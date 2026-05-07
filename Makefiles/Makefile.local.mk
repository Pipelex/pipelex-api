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
