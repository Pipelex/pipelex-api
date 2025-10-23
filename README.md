<div align="center">
  <a href="https://www.pipelex.com/"><img src="https://raw.githubusercontent.com/Pipelex/pipelex/main/.github/assets/logo.png" alt="Pipelex Logo" width="400" style="max-width: 100%; height: auto;"></a>

  <h2 align="center">Pipelex API Server</h2>

The official REST API server for building and executing Pipelex pipelines. Deploy your pipelines as HTTP endpoints and integrate them into any application or workflow.

  <div>
    <a href="https://docs.pipelex.com/pages/api/"><strong>API Documentation</strong></a> -
    <a href="https://github.com/Pipelex/pipelex"><strong>Pipelex Core</strong></a> -
    <a href="https://go.pipelex.com/discord"><strong>Discord</strong></a>
  </div>
  <br/>

  <p align="center">
    <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT License"></a>
    <a href="https://go.pipelex.com/discord"><img src="https://img.shields.io/badge/Discord-5865F2?logo=discord&logoColor=white" alt="Discord"></a>
    <a href="https://docs.pipelex.com/"><img src="https://img.shields.io/badge/Docs-03bb95?logo=read-the-docs&logoColor=white&style=flat" alt="Documentation"></a>
  </p>
</div>

---

# 📑 Table of Contents

- [Introduction](#introduction)
- [Quick Start with Docker](#-quick-start-with-docker)
- [API Documentation](#-api-documentation)
- [Support](#-support)
- [License](#-license)

# Introduction

The **Pipelex API Server** is a FastAPI-based REST API that allows you to execute [Pipelex](https://github.com/Pipelex/pipelex) pipelines via HTTP requests. Deploy your pipelines as HTTP endpoints and integrate them into any application or workflow.

# 🚀 Quick Start with Docker

**Official Docker image available at:** [`pipelex/pipelex-api`](https://hub.docker.com/r/pipelex/pipelex-api)

### 1. Configure Environment

Create a `.env` file with your API key and LLM provider configuration:

```bash
# Required: Your API authentication key. This is the API key that will be required to access the API.
API_KEY=your-api-key-here

# AI inference provider API keys: either using Pipelex Inference API or your own API key(s) (see configuration below)
PIPELEX_INFERENCE_API_KEY=your-pipelex-inference-key
```

> **For complete API key configuration**, see the [API Key Configuration section](https://github.com/Pipelex/pipelex#api-key-configuration) in the main Pipelex repository.

### 2. Run with Docker

**Option A: From Docker Hub (Recommended)**

```bash
docker run --name pipelex-api -p 8081:8081 --env-file .env pipelex/pipelex-api:latest
```

**Option B: Build Locally**

```bash
docker build -t pipelex-api .
docker run -d --name pipelex-api -p 8081:8081 --env-file .env pipelex-api
```

### 3. Verify

```bash
curl http://localhost:8081/health
```

The API is now running at `http://localhost:8081`

# 📖 API Documentation

For complete API documentation, including input formats, error handling, best practices, and client library examples:

**[https://docs.pipelex.com/pages/api/](https://docs.pipelex.com/pages/api/)**

# 💬 Support

- **API Documentation**: [https://docs.pipelex.com/pages/api/](https://docs.pipelex.com/pages/api/)
- **Pipelex Documentation**: [https://docs.pipelex.com/](https://docs.pipelex.com/)
- **Discord Community**: [https://go.pipelex.com/discord](https://go.pipelex.com/discord)
- **Main Repository**: [https://github.com/Pipelex/pipelex](https://github.com/Pipelex/pipelex)

# 📝 License

This project is licensed under the [MIT license](LICENSE). Runtime dependencies are distributed under their own licenses via PyPI.

---

"Pipelex" is a trademark of Evotis S.A.S.

© 2025 Evotis S.A.S.
