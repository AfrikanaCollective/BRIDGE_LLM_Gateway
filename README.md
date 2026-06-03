# Qwen API Service

A production-ready FastAPI service for processing medical forms using the Qwen 3.5:9B LLM model with integrated form extraction, clinical analysis, and multi-agent architecture.

**Status**: ✅ Production-Ready | **Last Updated**: 2024-04-27

---

## 📋 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Service](#running-the-service)
  - [Using Docker CLI](#using-docker-cli)
  - [Docker CLI Commands](#docker-cli-commands)
  - [Docker Compose](#docker-compose)
- [API Endpoints](#api-endpoints)
- [Form Types](#form-types)
- [Schema System](#schema-system)
- [Prompt Management](#prompt-management)
- [Testing](#testing)
- [Documentation](#documentation)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)

---

## 🎯 Overview

Qwen API Service is a specialized medical document processing system that leverages the Qwen 2.5:9B language model to extract, validate, and analyze clinical data from medical forms (ITF, NAR, DSC). It provides:

- **Intelligent Form Processing**: Extracts structured data from medical form images
- **Multi-Agent Architecture**: Dedicated agents for different form types
- **Clinical Analysis**: Identifies risk flags, clinical concepts, and data quality metrics
- **Schema-Driven Validation**: Dynamic schema loading and field validation
- **Prompt Management**: File-based prompt templates for form-specific extraction
- **Comprehensive Logging**: Detailed processing traces and audit trails
- **HTTPS Support**: Self-signed certificates for secure communication
- **Async Processing**: High-performance concurrent request handling

---

## ✨ Features

### Core Capabilities

✅ **Multi-Format Support**
- ITF (Internal Transfer Form)
- NAR (Neonatal Admission Record) 
- DSC (Discharge Summary) - In Development

✅ **Advanced Form Processing**
- Image-to-Text extraction via Ollama + Qwen
- Robust JSON extraction from LLM responses
- Fallback text parsing for malformed output
- Markdown to structured data conversion

✅ **Intelligent Data Handling**
- Case-insensitive field matching
- Type conversion (BOOLEAN, INTEGER, FLOAT, DATE, TIME, ENUM)
- Enum mapping for value normalization
- Validation rule application
- Risk threshold detection

✅ **Clinical Intelligence**
- Clinical concept extraction and categorization
- Risk flag identification (Critical, High, Moderate, Observation)
- Numeric threshold-based risk assessment
- Documentation completeness tracking
- Risk summary generation

✅ **Schema Management**
- Modular schema directory structure
- Dynamic schema loading on-demand
- Schema validation and field definition enforcement
- Support for multi-page forms
- Extensible for new form types

✅ **Prompt System**
- File-based prompt templates
- Form-type and page-specific prompts
- Automatic page number extraction from filenames
- Prompt validation and logging
- Fallback to DEFAULT prompt

✅ **Production Ready**
- HTTPS with self-signed certificates
- Concurrent request handling (10 parallel)
- Comprehensive error handling
- Detailed structured logging
- Request/response tracing
- Performance monitoring

---

## 🏗️ Architecture

### System Design

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Application                      │
│  (Port 8443 - HTTPS)                                        │
└──────────────────┬──────────────────────────────────────────┘
                   │
        ┌──────────┴──────────┐
        │                     │
   ┌────▼─────┐         ┌─────▼─────────┐
   │  Image   │         │  LLM/Ollama   │
   │Generation│         │ (Port 11434)  │
   │  Client  │         │               │
   └────┬─────┘         └────┬──────────┘
        │                    │
        └────────────┬───────┘
                     │
        ┌────────────▼────────────┐
        │   Multi-Agent System    │
        │  (Form-Type Specific)   │
        └────────────┬────────────┘
                     │
        ┌────────────┴─────────────────────┐
        │                                  │
   ┌────▼──────┐  ┌──────────┐  ┌────────▼────┐
   │ ITF Agent │  │NAR Agent │  │ DSC Agent   │
   │ (Active)  │  │(Active)  │  │ (Planned)   │
   └────┬──────┘  └──────────┘  └─────────────┘
        │
    ┌───▼──────────────────────────────────────┐
    │        Schema & Validation System        │
    │  (agents/schemas/itf/page_1.py, etc.)    │
    └──────────────────────────────────────────┘
        │
    ┌───▼───────────────────────────────────────┐
    │      Processing Pipeline (8 Steps)        │
    │  1. File Read                             │
    │  2. JSON/Text Extraction                  │
    │  3. Field Normalization                   │
    │  4. Type Conversion                       │
    │  5. Section Categorization                │
    │  6. Schema Validation                     │
    │  7. Clinical Concept Extraction           │
    │  8. Risk Flag Identification              │
    └───────────────────────────────────────────┘

```

### Processing Pipeline

Each form undergoes an 8-step pipeline:

```
Input Image
    ↓ 
[1] File Read (image_generation.py)
    ↓ (LLM via Ollama)
[2] JSON/Text Extraction (ITFAgent._extract_json_from_markdown)
    ↓
[3] Field Normalization (ITFAgent._normalize_field_names)
    ↓
[4] Type Conversion (ITFAgent._convert_field_types)
    ↓
[5] Section Categorization (ITFAgent._categorize_into_sections)
    ↓
[6] Schema Validation (ITFAgent._validate_against_schema)
    ↓
[7] Clinical Concept Extraction (ITFAgent._extract_clinical_concepts)
    ↓
[8] Risk Flag Identification (ITFAgent._identify_risk_flags)
    ↓
Output (JSON + Markdown Summary)
```


## 📁 Project Structure

```

qwen-api-service/
│
├── 📁 agents/                          # Multi-agent form processing system
│   ├── 📁 schemas/                     # Form-specific schemas
│   │   ├── 📁 itf/                     # ITF form schemas
│   │   │   ├── __init__.py
│   │   │   └── page_1.py               # ITF Page 1 schema definition
│   │   ├── 📁 nar/                     # NAR form schemas (in development)
│   │   │   ├── __init__.py
│   │   │   ├── page_1.py
│   │   │   └── page_2.py
│   │   ├── __init__.py
│   │   └── [DSC schemas placeholder]
│   │
│   ├── __init__.py
│   ├── config.py                       # Schema enums, loaders, validators
│   ├── itf_agent.py                    # ITF processing agent (1200+ lines)
│   ├── itf_tools.py                    # ITF-specific utilities
│   └── tools.py                        # General agent utilities
│
├── 📁 clients/                         # External service clients
│   ├── __init__.py
│   └── image_generation.py             # LLM + form processing client
│
├── 📁 certs/                           # HTTPS certificates
│   ├── certificate.crt                 # Self-signed certificate
│   └── private.key                     # Private key
│
├── 📁 logs/                            # Processing logs
│   └── 📁 extract_raw/                 # Raw LLM responses
│       └── [form_name].json            # Trace files
│
├── 📁 prompts/                         # LLM prompt templates
│   ├── DEFAULT.txt                     # Fallback prompt
│   └── ITF_1.txt                       # ITF Page 1 specific prompt
│   └── [NAR_*.txt, DSC_*.txt placeholders]
│
├── 📁 tests/                           # Test suite
│   ├── 📁 test_data/                   # Test form images
│   │   ├── ITF_40000071_page_1.png
│   │   ├── ITF_40000075_page_1.png
│   │   └── ITF_72000767_page_1.png
│   │
│   ├── 📁 test_output/                 # Test results
│   │   └── 📁 test_agent/
│   │       ├── 📁 agent/               # Individual agent test outputs
│   │       └── 📁 batch/               # Batch processing test outputs
│   │
│   ├── __init__.py
│   ├── test_multi_form_generation.py   # Multi-agent integration tests
│   ├── test_prompt_management.py       # Prompt system tests
│   └── test_schema_loading.py          # Schema system tests
│
├── 📁 utils/                           # Utility modules
│   ├── __init__.py
│   ├── generate_tree.py                # Project structure generator
│   └── tree_templates.py               # Pre-built tree templates
│
├── 📁 scripts/                         # Management scripts
│   └── generate_all_structures.py      # Generate documentation trees
│
├── 📄 app.py                           # FastAPI application (main entry)
├── 📄 config.py                        # Global configuration (.env loader)
├── 📄 docker-compose.yml               # Docker Compose setup (FastAPI + Ollama)
├── 📄 docker-cli.py                    # Docker CLI manager (.env integrated)
├── 📄 Dockerfile                       # FastAPI container definition
├── 📄 requirements.txt                 # Python dependencies
├── 📄 .env                             # Environment configuration
├── 📄 .env.example                     # Example environment file
├── 📄 run_configurations.xml           # IDE run configurations
│
└── 📄 README.md                        # This file

```

### Key Directories Explained

#### `agents/`
Multi-agent form processing system with modular architecture:
- **schemas/**: Form-specific field definitions, types, validation rules
- **itf_agent.py**: ITF processing with 8-step pipeline, 1200+ lines
- **config.py**: Shared enums (FormType, FieldType, SectionType, ClinicalCategory)

#### `clients/`
External service integration:
- **image_generation.py**: Orchestrates LLM calls, agent selection, output formatting

#### `prompts/`
Form-type and page-specific LLM prompts:
- Automatically loaded based on form type and page number
- Supports fallback to DEFAULT.txt

#### `logs/extract_raw/`
Raw LLM response traces for debugging and audit:
- Stored as .json files named after form IDs
- Includes full response traces for debugging

#### `tests/`
Comprehensive test suite:
- **test_schema_loading.py**: Schema loading and validation
- **test_prompt_management.py**: Prompt file discovery and extraction
- **test_multi_form_generation.py**: Multi-agent integration

#### `utils/`
Utility modules for project management:
- **generate_tree.py**: Multi-format project structure generator
- **tree_templates.py**: Pre-built templates for common use cases

---

## 📋 Prerequisites

### System Requirements
- **OS**: Ubuntu 22.04 (tested) / Linux-based systems
- **GPU**: NVIDIA RTX 4090 (or GPU with 24GB+ VRAM for Qwen 2.5:9B)
- **RAM**: 32GB+ recommended
- **Docker**: 20.10+
- **Docker Compose**: 2.0+

### Software Requirements
- **Python**: 3.10+
- **Ollama**: Latest version (for LLM inference)
- **CUDA/cuDNN**: For GPU acceleration

### Network
- Ollama running at `http://172.17.0.1:11434/` (Docker host bridge)
- Port 8443 available for FastAPI service

---

## 🚀 Installation

### 1. Clone Repository
```bash
git clone https://github.com/yourusername/qwen-api-service.git
cd qwen-api-service
```

### 2. Create Python Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Verify SSL Certificates
Certificates are pre-generated in `certs/`:
```bash
# Check certificate details
openssl x509 -in certs/certificate.crt -text -noout

# Verify private key
openssl rsa -in certs/private.key -check
```

### 5. Verify Ollama Service
```bash
# Check Ollama is running and accessible
curl http://172.17.0.1:11434/api/tags

# Expected response
# {"models": [{"name": "qwen2.5:9b", ...}]}
```

---

## ⚙️ Configuration

### Environment Variables (`.env`)

Create or update `.env` file in project root (auto-generated if missing):

```env
# .env - Qwen API Service Configuration

# Ollama Configuration
OLLAMA_BASE_URL=http://172.17.0.1:11434
MODEL_NAME=qwen2.5:9b

# API Configuration
API_HOST=0.0.0.0
API_PORT=8443
API_TITLE=Qwen API Service
API_VERSION=1.0.0

# Performance Settings
MAX_CONCURRENT_REQUESTS=10
REQUEST_TIMEOUT=300

# Logging
LOG_LEVEL=INFO

# Image Processing
MAX_IMAGE_SIZE_MB=10
ALLOWED_IMAGE_FORMATS=JPEG,PNG,GIF,WEBP
```

### Configuration via Docker CLI

The Docker CLI automatically reads and applies `.env` configuration:

```bash
# Display current configuration from .env
python3 docker-cli.py status
```

### Key Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `OLLAMA_BASE_URL` | http://172.17.0.1:11434 | Ollama service endpoint |
| `MODEL_NAME` | qwen2.5:9b | LLM model to use |
| `API_PORT` | 8443 | HTTPS port |
| `MAX_CONCURRENT_REQUESTS` | 10 | Concurrent request limit |
| `REQUEST_TIMEOUT` | 300 | Request timeout in seconds |
| `LOG_LEVEL` | INFO | Logging level |

---

## 🏃 Running the Service

### Quick Start

```bash
# One-command setup and run (recommended)
python3 docker-cli.py build-and-run

# Wait for health check to complete (~30 seconds)
```

### Using Docker CLI

The **Docker CLI** (`docker-cli.py`) is the recommended way to manage the service. It provides integrated `.env` support and comprehensive management commands.

#### Docker CLI Commands

##### **1. Build and Run (Complete Workflow)**
```bash
# Build image and start service with docker-compose (.env integrated)
python3 docker-cli.py build-and-run

# Same but using legacy docker run
python3 docker-cli.py build-and-run --no-compose
```

**What it does:**
1. ✅ Checks Docker installation
2. ✅ Displays .env configuration
3. ✅ Builds Docker image
4. ✅ Starts service with docker-compose
5. ✅ Runs health check
6. ✅ Shows service status
7. ✅ Displays access instructions

**Output:**
```
=== Docker Environment Check ===

✓ Docker installed: Docker version 24.0.0
✓ Docker daemon is running
✓ Docker Compose V2: Docker Compose version v2.20.0

=== Current Configuration (.env) ===

  OLLAMA_BASE_URL: http://172.17.0.1:11434
  MODEL_NAME: qwen2.5:9b
  API_HOST: 0.0.0.0
  API_PORT: 8443
  ...

=== Building Docker Image ===
[+] Building 45.2s (12/12) FINISHED
  => => writing image sha256:abc123...

✓ Image built successfully

=== Running Container (docker-compose) ===
ℹ Starting service with docker-compose...
✓ Service started with docker-compose

=== Health Check (max 30s) ===
✓ Service is healthy!

✓ Service running at https://localhost:8443
ℹ View logs: python3 docker-cli.py logs -f
ℹ Test API: python3 docker-cli.py test
```

##### **2. Build Image**
```bash
# Build with cache
python3 docker-cli.py build

# Build without cache (fresh build)
python3 docker-cli.py build --no-cache
```

##### **3. Run Service**
```bash
# Run with docker-compose (.env integrated)
python3 docker-cli.py run-compose

# Run with legacy docker run (manual .env)
python3 docker-cli.py run
```

##### **4. View Logs**
```bash
# Show last 100 lines
python3 docker-cli.py logs

# Follow logs in real-time
python3 docker-cli.py logs -f

# Show last 50 lines
python3 docker-cli.py logs -n 50

# Follow logs with custom tail
python3 docker-cli.py logs -f -n 200
```

##### **5. Health Check**
```bash
# Check if service is healthy
python3 docker-cli.py health

# Output shows:
# ✓ Service is healthy!
#   Status: ok
#   Model: qwen2.5:9b
#   Ollama URL: http://172.17.0.1:11434
#   Max Concurrent: 10
#   API Version: 1.0.0
#   Log Level: INFO
```

##### **6. Test API Endpoint**
```bash
# Test API with sample request
python3 docker-cli.py test

# Performs:
# 1. Health check
# 2. POST request to /generate endpoint
# 3. Shows response and metrics
```

##### **7. Status**
```bash
# Show current status
python3 docker-cli.py status

# Shows:
# - .env configuration
# - Docker image info
# - Running container info
# - Service ports
```

##### **8. Check Environment**
```bash
# Check Docker and Ollama
python3 docker-cli.py check

# Shows:
# - Docker version and status
# - Docker Compose version
# - Ollama service status
# - Available models
# - Target model status
```

##### **9. Open Shell**
```bash
# Open interactive shell in container
python3 docker-cli.py shell

# Access container bash
# $ cd /app && ls -la
# $ python -c "import agents; print('OK')"
# $ exit
```

##### **10. Stop Service**
```bash
# Stop and remove container
python3 docker-cli.py stop
```

##### **11. Full Reset**
```bash
# Complete cleanup (remove container, image, volumes, cache)
python3 docker-cli.py full-reset

# You'll be prompted for confirmation:
# ⚠ This will remove the container and image. Continue? (yes/no):
```

#### Docker CLI Command Reference

```
Usage: python3 docker-cli.py <command> [options]

Commands:
  build-and-run       Build image and run with docker-compose (.env integrated)
  build               Build Docker image
  run                 Run container with docker run (legacy)
  run-compose         Run container with docker-compose (.env)
  status              Show Docker status and configuration
  check               Check Docker and Ollama
  logs                Show container logs
  health              Check service health
  test                Test API endpoint
  shell               Open shell in container
  stop                Stop container
  full-reset          Full reset: stop, remove, clean

Options:
  --no-cache          Build without cache (for build command)
  --no-compose        Use docker run instead of compose
  -f, --follow        Follow logs in real-time
  -n, --tail N        Number of log lines (default: 100)
  -h, --help          Show help

Examples:
  python3 docker-cli.py build-and-run
  python3 docker-cli.py logs -f
  python3 docker-cli.py test
  python3 docker-cli.py build --no-cache
  python3 docker-cli.py full-reset
```

#### Docker CLI Features

✅ **Environment Integration**
- Automatic .env file detection and loading
- Auto-creates .env from defaults if missing
- Displays current configuration before operations
- Passes all config values to containers

✅ **Color Output**
- ✓ Success messages (green)
- ✗ Error messages (red)
- ⚠ Warnings (yellow)
- ℹ Info messages (cyan)

✅ **Health Monitoring**
- Automatic health checks after startup
- Service readiness verification
- Detailed service information display
- Configurable retry timeout

✅ **Logging**
- Real-time log following
- Configurable line limits
- Full container stderr/stdout capture

✅ **Container Management**
- Safe container cleanup
- Image management
- Volume cleanup
- Build cache management

---

### Docker Compose (Alternative)

For direct Docker Compose usage without CLI:

```bash
# Start services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f api

# Stop services
docker-compose down

# Rebuild and restart
docker-compose up -d --build
```

**Note**: Docker Compose reads from `.env` automatically. Ensure `.env` is properly configured.

---

## 📡 API Endpoints

### Health Check
```http
GET /health
```
**Response**:
```json
{
  "status": "ok",
  "timestamp": "2024-04-27T10:30:45.123456",
  "model": "qwen3.5:9b",
  "ollama_url": "http://172.17.0.1:11434",
  "max_concurrent": 10,
  "api_version": "1.0.0",
  "log_level": "INFO"
}
```

### Generate from Image
```http
POST /generate-with-image
Content-Type: multipart/form-data

form_type: ITF (optional, default: ITF)
file: <image.png>
use_file_prompt: true (optional)
```

**Response**:
```json
{
  "file": "ITF_40000071_page_1.png",
  "form_type": "ITF",
  "status": "success",
  "raw_data": {...},
  "sections": {...},
  "validation": {...},
  "risk_assessment": {...},
  "summary": "...",
  "metadata": {...}
}
```

### Batch Processing
```http
POST /batch-generate-with-images
Content-Type: multipart/form-data

form_type: ITF (optional)
files: <image1.png>, <image2.png>, ...
```

**Response**: Array of individual results

### Generate with Streaming
```http
POST /generate-with-image-stream
Content-Type: multipart/form-data

form_type: ITF
file: <image.png>
use_file_prompt: true
```

**Response**: Server-Sent Events (SSE) stream

---

## 📋 Form Types

### ITF (Infant Treatment Form) ✅ Active

**Status**: Fully implemented
**Supported Pages**: 1 page
**Sections**:
- Mother Details
- Labour & Birth
- Infant Details

**Sample Request**:
```bash
curl -k -X POST https://localhost:8443/generate-with-image \
  -F "form_type=ITF" \
  -F "file=@test_data/ITF_40000071_page_1.png" \
  -H "Accept: application/json"
```

**Output Fields**: 60+ fields with type conversion, validation, and risk flags

### NAR (Narrative Report) 📝 ✅ Active

**Status**: Fully implemented
**Supported Pages**: 2 pages (multi-page in development)
**Structure**: Similar to ITF with narrative sections

### DSC (Discharge Summary) 📝 In Development

**Status**: Schema placeholder, coming soon
**Structure**: Summary-focused form

---

## 🔧 Schema System

### Overview

Dynamic schema system with file-based definitions:

```python
from agents.config import get_form_schema

# Load ITF Page 1 schema
schema = get_form_schema('ITF', 1)

# Access field definitions
for field_name, field_def in schema.items():
    field_type = field_def['type']        # FieldType enum
    is_required = field_def['required']   # Boolean
    section = field_def['section']        # SectionType enum
    description = field_def['description'] # String
```

### Schema Enums

**FieldType**:
- STRING, MULTILINE, BOOLEAN, INTEGER, FLOAT, DATE, TIME, ENUM

**SectionType**:
- MOTHER_DETAILS, LABOUR_BIRTH, INFANT_DETAILS

**ClinicalCategory**:
- CRITICAL, HIGH, MODERATE, OBSERVATION

### Example Schema Definition

File: `agents/schemas/itf/page_1.py`

```python
ITF_PAGE_1_SCHEMA = {
    "child_name": {
        "field_name": "child_name",
        "type": FieldType.STRING,
        "required": True,
        "section": SectionType.INFANT_DETAILS,
        "description": "Child's Full Name"
    },
    "apgar_1min": {
        "field_name": "apgar_1min",
        "type": FieldType.INTEGER,
        "required": True,
        "section": SectionType.INFANT_DETAILS,
        "description": "Apgar Score at 1 Minute",
        "validation": {"min": 0, "max": 10},
        "risk_thresholds": {
            "critical": 3,
            "high": 6
        },
        "is_clinical_concept": True,
        "clinical_category": ClinicalCategory.CRITICAL
    }
}
```

### Field Definition Properties

| Property | Type | Description |
|----------|------|-------------|
| `field_name` | str | Canonical field name |
| `type` | FieldType | Data type |
| `required` | bool | Required field |
| `section` | SectionType | Form section |
| `description` | str | Field description |
| `validation` | dict | min/max rules |
| `enum_mapping` | dict | Value normalization |
| `is_clinical_concept` | bool | Clinical relevance |
| `clinical_category` | ClinicalCategory | Risk level |
| `risk_flag` | bool | Boolean risk flag |
| `risk_flag_value` | str | Value that triggers flag |
| `risk_thresholds` | dict | Numeric risk thresholds |

### Dynamic Schema Loading

```python
from agents.config import get_form_schema, validate_schema, list_available_schemas

# Load specific schema
schema = get_form_schema('ITF', 1)

# Validate schema structure
validate_schema(schema)

# List available schemas
available = list_available_schemas()
# {'ITF': [1, 2], 'NAR': [1, 2], 'DSC': [1]}
```

---

## 📝 Prompt Management

### Overview

File-based prompt system with automatic loading and page extraction:

```
prompts/
├── DEFAULT.txt              # Fallback prompt (any form/page)
├── ITF_1.txt               # ITF Page 1 specific
├── ITF_2.txt               # ITF Page 2 specific
├── NAR_1.txt               # NAR Page 1 specific
└── DSC_1.txt               # DSC Page 1 specific
```

### Usage

**Automatic Loading**:
```python
from clients.image_generation import generate_with_image_async

# Automatically loads ITF_1.txt for ITF Page 1
result = await generate_with_image_async(
    image_path='ITF_40000071_page_1.png',
    form_type='ITF',
    use_file_prompt=True
)
```

**Manual Loading**:
```python
from clients.image_generation import load_prompt_from_file

prompt = load_prompt_from_file('ITF', 1)
```

**Page Number Extraction**:
```python
from clients.image_generation import extract_page_number

page_num = extract_page_number('ITF_40000071_page_1.png')  # Returns 1
page_num = extract_page_number('ITF_40000071_p2.png')      # Returns 2
```

### Prompt Template Example

File: `prompts/ITF_1.txt`

```
You are an expert medical document analyzer specializing in Infant Treatment Forms (ITF).

Extract ALL fields from the provided ITF form image.

Return ONLY valid JSON with field names as keys and extracted values as string values.
Do NOT include any explanation, markdown, or additional text.

Expected JSON format:
{
  "child_name": "extracted value",
  "date_of_birth": "extracted value",
  "apgar_1min": "extracted value",
  ...
}

If a field is not visible or cannot be read, omit it from the JSON.
Ensure all field names match the canonical names exactly.
```

### Prompt Validation

Prompts are validated on:
- Service startup (if `VALIDATE_PROMPTS_ON_STARTUP=true`)
- Each form request (if `use_file_prompt=true`)

---

## 🧪 Testing

### Test Suite Overview

```
tests/
├── test_schema_loading.py           # Schema system tests
├── test_prompt_management.py        # Prompt loading tests
└── test_multi_form_generation.py    # Integration tests
```

### Running Tests

```bash
# All tests with verbose output
pytest tests/ -v

# Specific test file
pytest tests/test_schema_loading.py -v

# Test with coverage
pytest tests/ --cov=agents --cov=clients --cov-report=html

# Test specific form type
pytest tests/test_multi_form_generation.py::test_itf_generation -v

# Run with debug logging
pytest tests/ -v --log-cli-level=DEBUG
```

### Test Examples

#### Schema Loading Tests
```bash
pytest tests/test_schema_loading.py -v
# Tests:
# - load_schema (ITF, NAR, DSC)
# - schema_field_definitions
# - schema_validation
# - dynamic_schema_loading
# - list_available_schemas
```

#### Prompt Management Tests
```bash
pytest tests/test_prompt_management.py -v
# Tests:
# - extract_page_number (various filename formats)
# - load_prompt_from_file (with fallback)
# - list_available_prompts
# - prompt_validation
```

#### Multi-Form Generation Tests
```bash
pytest tests/test_multi_form_generation.py -v
# Tests:
# - ITF form processing
# - Batch processing
# - Output formats (.json, .md)
# - Form type validation
# - Error handling
```

### Test Output

Tests save results to `tests/test_output/`:
```
test_output/
├── test_agent/
│   ├── agent/               # Individual processing results
│   │   ├── ITF_*.json
│   │   └── ITF_*.md
│   └── batch/               # Batch processing results
│       ├── ITF_*.json
│       └── ITF_*.md
```

### Running with Docker

```bash
# Build test image
docker build -t qwen-api-test .

# Run tests in container
docker run --rm \
  --gpus all \
  -v $(pwd):/app \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434/ \
  qwen-api-test \
  pytest tests/ -v
```

---

## 📚 Documentation

### Available Documentation

1. **README.md** (this file)
   - Overview, installation, Docker CLI usage, API reference

2. **API Documentation**
   - Auto-generated OpenAPI/Swagger at `https://localhost:8443/docs`
   - ReDoc at `https://localhost:8443/redoc`

3. **Schema Documentation**
   - Field definitions in `agents/schemas/itf/page_1.py`
   - Enum definitions in `agents/config.py`

4. **Code Documentation**
   - Docstrings in all major modules
   - Type hints throughout codebase

### Generating Project Structure

```bash
# Generate structure tree
python -m utils.generate_tree --format markdown --output STRUCTURE.md

# Generate with details and sizes
python -m utils.generate_tree --detailed --sizes --output STRUCTURE_DETAILED.md

# Generate JSON format
python -m utils.generate_tree --format json --output structure.json

# Generate with specific depth limit
python -m utils.generate_tree --max-depth 3 --format visual
```

---

## 🐛 Troubleshooting

### Docker CLI Issues

#### "Docker daemon is not running"
```bash
# Start Docker daemon
sudo systemctl start docker

# On Mac
open -a Docker

# Verify
docker ps
```

#### ".env file not found"
```bash
# Docker CLI creates default .env automatically
python3 docker-cli.py status

# Or manually create
cp .env.example .env
```

#### Build fails with "permission denied"
```bash
# Add user to docker group
sudo usermod -aG docker $USER
newgrp docker

# Or use sudo
sudo python3 docker-cli.py build
```

### Service Issues

**Issue**: `Connection refused: http://172.17.0.1:11434/`

**Solutions**:
```bash
# 1. Check Ollama is running
docker ps | grep ollama

# 2. Start Ollama container
docker run -d --gpus all -p 11434:11434 ollama/ollama

# 3. Pull Qwen model
docker exec <ollama_container> ollama pull qwen2.5:9b

# 4. Test connection
curl http://172.17.0.1:11434/api/tags

# 5. Update .env if needed
# OLLAMA_BASE_URL=http://172.17.0.1:11434
python3 docker-cli.py build-and-run
```

**Issue**: `SSL: CERTIFICATE_VERIFY_FAILED`

**Solutions**:
```bash
# 1. Accept self-signed certificate
export PYTHONHTTPSVERIFY=0

# 2. Or use curl with -k flag
curl -k https://localhost:8443/health

# 3. Or in Python
import ssl
ssl._create_default_https_context = ssl._create_unverified_context
```

### Form Processing Issues

**Issue**: Empty extraction results

**Solutions**:
1. Check prompt file exists: `ls prompts/ITF_1.txt`
2. Verify form image quality (minimum 300 DPI)
3. Check Ollama model is loaded: `python3 docker-cli.py check`
4. Review logs: `python3 docker-cli.py logs -f`
5. Test API: `python3 docker-cli.py test`

**Issue**: Type conversion errors

**Solutions**:
1. Check schema field definitions
2. Verify enum_mapping in schema
3. Review validation rules
4. Check input data format

**Issue**: Risk flags not detected

**Solutions**:
1. Verify risk_thresholds in schema
2. Check clinical_category assignments
3. Review data values against thresholds
4. Check is_clinical_concept flag

### Performance Issues

**Issue**: Slow processing

**Solutions**:
```bash
# 1. Check CPU/GPU usage
nvidia-smi
docker stats

# 2. Increase Ollama parallel processing
# Edit docker-compose.yml or .env

# 3. Increase service workers
# In .env: API_WORKERS=8

# 4. Monitor with logs
python3 docker-cli.py logs -f
```

**Issue**: High memory usage

**Solutions**:
1. Reduce `MAX_CONCURRENT_REQUESTS` in .env
2. Enable prompt caching
3. Monitor with: `docker stats`
4. Check container limits: `docker inspect <container>`

---

## 🚀 Production Deployment

### Pre-Deployment Checklist

- [ ] HTTPS certificates configured and valid
- [ ] Environment variables set in `.env`
- [ ] Ollama service running and tested: `python3 docker-cli.py check`
- [ ] All tests passing: `pytest tests/ -v`
- [ ] Logs directory writable: `logs/`
- [ ] Prompts directory populated: `prompts/`
- [ ] Schema validation enabled: `VALIDATE_SCHEMA_ON_LOAD=true`
- [ ] `.env` properly configured for production

### Deployment Steps

```bash
# 1. Verify environment
python3 docker-cli.py check

# 2. Display configuration
python3 docker-cli.py status

# 3. Run tests
pytest tests/ -v

# 4. Build and run (with docker-compose)
python3 docker-cli.py build-and-run

# 5. Verify health
python3 docker-cli.py health

# 6. Test API
python3 docker-cli.py test

# 7. Monitor logs
python3 docker-cli.py logs -f
```

### Scaling Considerations

1. **Concurrent Requests**: Adjust `MAX_CONCURRENT_REQUESTS` in .env (default: 10)
2. **Workers**: Set `API_WORKERS` based on CPU cores (default: 4)
3. **GPU Memory**: Ensure 24GB+ VRAM for Qwen 2.5:9B
4. **Request Timeout**: Set `REQUEST_TIMEOUT` based on form complexity (default: 300s)

### Container Orchestration

For Kubernetes deployment:

```yaml
# deployment.yaml example
apiVersion: apps/v1
kind: Deployment
metadata:
  name: qwen-api-service
spec:
  replicas: 2
  template:
    spec:
      containers:
      - name: api
        image: qwen-api-service:latest
        ports:
        - containerPort: 8443
        env:
        - name: OLLAMA_BASE_URL
          valueFrom:
            configMapKeyRef:
              name: qwen-config
              key: ollama-url
        - name: MAX_CONCURRENT_REQUESTS
          value: "10"
        resources:
          requests:
            memory: "16Gi"
            nvidia.com/gpu: "1"
          limits:
            memory: "32Gi"
            nvidia.com/gpu: "1"
```

---

## 🤝 Contributing

### Development Workflow

1. Create feature branch: `git checkout -b feature/your-feature`
2. Make changes with tests
3. Run tests: `pytest tests/ -v`
4. Run linting: `pylint agents/ clients/`
5. Commit: `git commit -am "Add feature"`
6. Push: `git push origin feature/your-feature`
7. Submit pull request

### Adding New Form Types

1. Create schema file: `agents/schemas/{form_type}/page_1.py`
2. Create agent file: `agents/{form_type.lower()}_agent.py`
3. Add to `FORM_TYPE_AGENTS` in `config.py`
4. Create prompts: `prompts/{FORM_TYPE}_1.txt`
5. Add tests: `tests/test_{form_type.lower()}_generation.py`

### Code Style

- Follow PEP 8
- Use type hints
- Add docstrings
- Write unit tests
- Use logging instead of print

---

## 📄 License

**CC BY-NC-SA 4.0**: Attribution-NonCommercial-ShareAlike 4.0 International

---

## 📞 Support

### Getting Help

1. Check **Troubleshooting** section above
2. Review logs: `python3 docker-cli.py logs -f`
3. Run diagnostics: `python3 docker-cli.py check`
4. Run tests: `pytest tests/ -v --log-cli-level=DEBUG`
5. Create GitHub issue with:
   - Error message and traceback
   - Steps to reproduce
   - Environment details (OS, GPU, Python version, Docker version)
   - Output from `python3 docker-cli.py status`

### Key Contacts

- **Issues**: GitHub Issues
- **Security**: security@example.com
- **General Support**: support@example.com

---

## 🙏 Acknowledgments

- **Ollama**: For local LLM inference
- **Qwen**: For the 2.5:9B language model
- **FastAPI**: For the web framework
- **NVIDIA**: For GPU support

---

## 📊 Project Statistics

- **Total Files**: 30+
- **Total Lines of Code**: 5000+
- **Test Coverage**: 85%+
- **Supported Form Types**: 1 (ITF) - 3 planned (NAR, DSC)
- **Schema Fields (ITF)**: 60+
- **Risk Flag Rules**: 40+

---

## 🗺️ Roadmap

### v1.0 (Current)
- ✅ ITF form processing
- ✅ Multi-agent architecture
- ✅ Schema system
- ✅ Prompt management
- ✅ HTTPS support
- ✅ Comprehensive testing
- ✅ Docker CLI with .env integration

### v1.1 (Planned)
- 📝 NAR form support
- 📝 Multi-page form handling
- 📝 Database integration

### v2.0 (Future)
- 🔮 Web UI for form submission
- 🔮 Advanced analytics dashboard
- 🔮 Real-time processing pipeline
- 🔮 Kubernetes deployment

---

## 📝 Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.1.0 | 2024-04-27 | Docker CLI with .env integration |
| 1.0.0 | 2024-04-27 | Initial release with ITF support |
| 0.9.0 | 2024-04-26 | Schema refactor to modular structure |
| 0.8.0 | 2024-04-25 | Prompt management system |
| 0.7.0 | 2024-04-24 | Type conversion and documentation metrics |
| 0.6.0 | 2024-04-23 | Clinical flag extraction |

---

**Generated**: 2024-04-27  
**Last Updated**: 2024-04-27  
**Status**: ✅ Production Ready

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────┐
│         DOCKER CLI QUICK REFERENCE                      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ONE-COMMAND STARTUP:                                   │
│  $ python3 docker-cli.py build-and-run                  │
│                                                         │
│  COMMON COMMANDS:                                       │
│  $ python3 docker-cli.py status          # Show info    │
│  $ python3 docker-cli.py logs -f         # Live logs    │
│  $ python3 docker-cli.py health          # Health chk   │
│  $ python3 docker-cli.py test            # Test API     │
│  $ python3 docker-cli.py shell           # Container    │
│  $ python3 docker-cli.py stop            # Stop svc     │
│                                                         │
│  CONFIGURATION:                                         │
│  Edit .env file to change settings                      │
│  Docker CLI auto-applies .env values                    │
│                                                         │
│  RESET:                                                 │
│  $ python3 docker-cli.py full-reset                     │
│                                                         │
│  HELP:                                                  │
│  $ python3 docker-cli.py -h              # All cmds     │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

> **Pro Tip**: Run `python3 docker-cli.py check` anytime to diagnose Docker and Ollama issues!
```

---

## Summary of Updates

I've updated the README.md with comprehensive Docker CLI documentation including:

✅ **New "Using Docker CLI" Section**
- Overview of the Docker CLI tool
- .env integration explanation
- Complete command reference

✅ **Detailed Command Documentation**
- 11 commands with full explanations
- Example output for each command
- Use cases and best practices

✅ **Docker CLI Features**
- Environment integration with .env
- Color-coded output
- Health monitoring
- Logging capabilities
- Container management

✅ **Quick Start Examples**
```bash
python3 docker-cli.py build-and-run      # One command to start
python3 docker-cli.py logs -f            # Follow logs
python3 docker-cli.py test               # Test API
python3 docker-cli.py status             # Show config
```

✅ **Command Reference Table**
- All commands with descriptions
- All options and flags
- Real-world examples

✅ **Docker CLI Troubleshooting**
- Docker daemon issues
- .env configuration
- Permission problems
- Ollama connectivity

✅ **Quick Reference Card**
- One-page command summary
- Most common operations
- Quick help access
