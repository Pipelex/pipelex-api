# Pipelex API Guide

This guide covers everything you need to know about using the Pipelex API to execute pipelines with flexible input formats.

## Base URL

```
https://your-pipelex-server-url/api/v1
```

## Authentication

Include your API key in the Authorization header:

```
Authorization: Bearer YOUR_API_KEY
```

## API Endpoints Overview

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

