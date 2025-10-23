#################################################################################
#################################    HELPERS    #################################
#################################################################################

# Environment variables
ENV ?= local

define HELP_LOCAL
	$(GREEN)Local API Commands:$(RESET)
	$(YELLOW)API Commands:$(RESET)
	make local-build-api$(RESET): Builds the Docker image for the API locally.
	make local-run-api$(RESET): Runs the FastAPI Docker container locally.

endef
export HELP_LOCAL

LOCAL_DOCKER_IMAGE_NAME := local-api

#################################################################################
#################################    API    #################################
#################################################################################
# Local build for FastAPI
local-build-api:
	@echo "\n############################## LOCAL DOCKER BUILD ##############################"
	docker build --progress=plain -t $(LOCAL_DOCKER_IMAGE_NAME) .

# Local run for FastAPI
local-run-api:
	@echo "\n############################## RUNNING LOCAL DOCKER CONTAINER ##############################"
	docker run -p 8081:8081 \
		-e ENV=$(ENV) \
		-e API_KEY="${API_KEY}" \
		-e PIPELEX_INFERENCE_API_KEY="${PIPELEX_INFERENCE_API_KEY}" \
		$(LOCAL_DOCKER_IMAGE_NAME)
