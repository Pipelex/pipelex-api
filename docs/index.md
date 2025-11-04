# Pipelex API Documentation

Welcome to the Pipelex API documentation. The API provides programmatic access to the Pipelex system.

## What the API Offers

The API currently allows you to:

1. **Create** a Pipelex pipeline from natural language descriptions
2. **Run** any Pipelex pipeline with flexible inputs
3. **Validate** any Pipelex pipeline to ensure correctness

## Deployment

The Pipelex API is currently available for local deployment only. You can deploy it yourself using our Docker image: [`pipelex/pipelex-api`](https://hub.docker.com/r/pipelex/pipelex-api)

### 1. Configure Environment

Create a `.env` file with your API key and LLM provider configuration:

```bash
# Required: Your API authentication key
API_KEY=your-api-key-here

# AI inference provider API key
PIPELEX_INFERENCE_API_KEY=your-pipelex-inference-api-key

# TEMPORARY: Required for image generation (will be integrated into unified inference system)
FAL_API_KEY=your-fal-api-key
```

You can get a free Pipelex Inference API key ($20 of free credits) by joining our [Discord community](https://go.pipelex.com/discord).

> For complete API key configuration, see the [API Key Configuration section](https://github.com/Pipelex/pipelex#api-key-configuration) in the main Pipelex repository.

### 2. Run with Docker

**Option A: Using Docker Compose (Recommended)**

```bash
docker-compose up
```

**Option B: Using Docker Run**

```bash
docker run --name pipelex-api -p 8081:8081 \
  -e API_KEY=your-api-key-here \
  -e PIPELEX_INFERENCE_API_KEY=your-pipelex-inference-api-key \
  -e FAL_API_KEY=your-fal-api-key \
  pipelex/pipelex-api:latest
```

**Option C: Build Locally**

```bash
docker build -t pipelex-api .
docker run --name pipelex-api -p 8081:8081 \
  -e API_KEY=your-api-key-here \
  -e PIPELEX_INFERENCE_API_KEY=your-pipelex-inference-api-key \
  -e FAL_API_KEY=your-fal-api-key \
  pipelex-api
```

### 3. Verify

```bash
curl http://localhost:8081/health
```

## Base URL

Once deployed locally, the API is available at:

```
http://localhost:8081/api/v1
```

## Authentication

Include your API key in the Authorization header:

```
Authorization: Bearer YOUR_API_KEY
```

## API Endpoints

The Pipelex API provides three main capabilities:

### 1. Pipe Builder
Generate pipelines from natural language descriptions and create executable Python code.

- **Build Pipeline** - Generate PLX content from a brief description
- **Generate Runner Code** - Create Python code to execute a pipeline

[Learn more →](pipe-builder.md)

### 2. Pipe Run
Execute pipelines with flexible input formats, either synchronously or asynchronously.

- **Execute Pipeline** - Run a pipeline and wait for completion
- **Start Pipeline** - Start a pipeline execution without waiting

[Learn more →](pipe-run.md)

### 3. Pipe Validate
Validate PLX content to ensure pipelines are correctly defined before execution.

- **Validate PLX** - Parse, validate, and dry-run pipelines

[Learn more →](pipe-validate.md)
