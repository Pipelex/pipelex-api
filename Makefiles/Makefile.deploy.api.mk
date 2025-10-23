#################################################################################
#################################    HELPERS    #################################
#################################################################################

define HELP_DEPLOY_API
	$(GREEN)Deployment Commands:$(RESET)
	make deploy-api                         - Deploy the image to AWS ECR (create or update service).
	make deploy-docker-hub                  - Deploy the image to Docker Hub.
	make deploy-all                         - Deploy to both AWS ECR and Docker Hub.
endef
export HELP_DEPLOY_API

###########################################################################
################################ AWS Deploy ###############################
###########################################################################

deploy-api:
	@echo "\n############################## AWS Build ##############################"
	aws ecr get-login-password --region $(AWS_REGION) | docker login --username AWS --password-stdin $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com

	docker build --platform linux/amd64 -t pipelex-api:$(VERSION) .
	docker tag pipelex-api:$(VERSION) $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/pipelex-api:$(VERSION)
	docker tag pipelex-api:$(VERSION) $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/pipelex-api:latest

	docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/pipelex-api:$(VERSION)
	docker push $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/pipelex-api:latest

	@echo "Built and pushed image pipelex-api:$(VERSION) and latest to AWS ECR"

###########################################################################
############################ Docker Hub Deploy ############################
###########################################################################

deploy-docker-hub:
	@echo "\n########################### Docker Hub Build ##########################"
	@echo "Logging in to Docker Hub..."
	@echo "$(DOCKER_HUB_TOKEN)" | docker login --username pipelex --password-stdin

	@echo "Building Docker image for Docker Hub..."
	docker build --platform linux/amd64 -t pipelex-api:$(VERSION) .
	docker tag pipelex-api:$(VERSION) pipelex/pipelex-api:$(VERSION)
	docker tag pipelex-api:$(VERSION) pipelex/pipelex-api:latest

	@echo "Pushing to Docker Hub..."
	docker push pipelex/pipelex-api:$(VERSION)
	docker push pipelex/pipelex-api:latest

	@echo "✓ Built and pushed image pipelex-api:$(VERSION) and latest to Docker Hub"
	@echo "✓ Image available at: https://hub.docker.com/r/pipelex/pipelex-api"

###########################################################################
############################ Deploy All ###################################
###########################################################################

deploy-all: deploy-api deploy-docker-hub
	@echo "\n✓ Successfully deployed to both AWS ECR and Docker Hub"

