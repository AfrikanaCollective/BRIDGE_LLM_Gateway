#!/usr/bin/env python3
"""
Complete Docker management CLI for Qwen API Service
Handles reset, build, run, and debugging operations
Now integrates with .env configuration file
"""

import os
import sys
import subprocess
import json
import time
import argparse
from pathlib import Path
from typing import Optional, Tuple, List, Dict
from datetime import datetime
from dotenv import load_dotenv


# ============================================================================
# Color Codes for Terminal Output
# ============================================================================

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    @staticmethod
    def header(text: str) -> str:
        return f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}"

    @staticmethod
    def success(text: str) -> str:
        return f"{Colors.OKGREEN}✓ {text}{Colors.ENDC}"

    @staticmethod
    def error(text: str) -> str:
        return f"{Colors.FAIL}✗ {text}{Colors.ENDC}"

    @staticmethod
    def warning(text: str) -> str:
        return f"{Colors.WARNING}⚠ {text}{Colors.ENDC}"

    @staticmethod
    def info(text: str) -> str:
        return f"{Colors.OKCYAN}ℹ {text}{Colors.ENDC}"

    @staticmethod
    def bold(text: str) -> str:
        return f"{Colors.BOLD}{text}{Colors.ENDC}"


# ============================================================================
# Environment Configuration Manager
# ============================================================================

class EnvConfig:
    """Load and manage .env configuration"""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.env_file = project_root / ".env"
        self.env_example_file = project_root / ".env.example"
        self.config: Dict[str, str] = {}
        self.load()

    def load(self):
        """Load .env file"""
        if self.env_file.exists():
            load_dotenv(self.env_file)
            self.config = {
                "OLLAMA_BASE_URL": os.getenv("OLLAMA_BASE_URL", "http://172.17.0.1:11434"),
                "MODEL_NAME": os.getenv("MODEL_NAME", "qwen3.5:9b"),
                "API_HOST": os.getenv("API_HOST", "0.0.0.0"),
                "API_PORT": os.getenv("API_PORT", "8000"),
                "API_TITLE": os.getenv("API_TITLE", "Qwen API Service"),
                "API_VERSION": os.getenv("API_VERSION", "1.1.0"),
                "MAX_CONCURRENT_REQUESTS": os.getenv("MAX_CONCURRENT_REQUESTS", "10"),
                "REQUEST_TIMEOUT": os.getenv("REQUEST_TIMEOUT", "300"),
                "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
                "MAX_IMAGE_SIZE_MB": os.getenv("MAX_IMAGE_SIZE_MB", "10"),
                "ALLOWED_IMAGE_FORMATS": os.getenv("ALLOWED_IMAGE_FORMATS", "JPEG,PNG,GIF,WEBP"),
            }
            return True
        else:
            print(Colors.warning(f".env file not found at {self.env_file}"))
            print(Colors.info("Creating .env from defaults..."))
            self.create_default_env()
            return False

    def create_default_env(self):
        """Create default .env file"""
        default_env = """# .env - Qwen API Service Configuration
# Auto-generated if not present

# Ollama Configuration
OLLAMA_BASE_URL=http://172.17.0.1:11434
MODEL_NAME=qwen3.5:9b

# API Configuration
API_HOST=0.0.0.0
API_PORT=8000
API_TITLE=Qwen API Service
API_VERSION=1.1.0

# Performance Settings
MAX_CONCURRENT_REQUESTS=10
REQUEST_TIMEOUT=300

# Logging
LOG_LEVEL=INFO

# Image Processing
MAX_IMAGE_SIZE_MB=10
ALLOWED_IMAGE_FORMATS=JPEG,PNG,GIF,WEBP
"""
        try:
            self.env_file.write_text(default_env)
            print(Colors.success(f".env file created at {self.env_file}"))
            self.load()
        except Exception as e:
            print(Colors.error(f"Failed to create .env: {e}"))

    def get(self, key: str, default: str = "") -> str:
        """Get config value"""
        return self.config.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        """Get config value as integer"""
        try:
            return int(self.config.get(key, default))
        except ValueError:
            return default

    def display(self):
        """Display current configuration"""
        print(Colors.header("\n=== Current Configuration (.env) ===\n"))
        for key, value in self.config.items():
            # Mask sensitive values
            if "URL" in key or "HOST" in key:
                display_value = value
            else:
                display_value = value
            print(f"  {key}: {display_value}")
        print()


# ============================================================================
# Docker CLI Manager
# ============================================================================

class DockerCLI:
    """Main Docker CLI manager with .env integration"""

    def __init__(self):
        self.project_root = Path(__file__).parent
        self.env_config = EnvConfig(self.project_root)

        # Docker settings (fixed)
        self.image_name = "qwen-llm-gateway"
        self.container_name = "qwen-llm-gateway"

        # Load from .env
        self.port = self.env_config.get_int("API_PORT", 8000)
        self.ollama_url = self.env_config.get("OLLAMA_BASE_URL", "http://172.17.0.1:11434")
        self.model_name = self.env_config.get("MODEL_NAME", "qwen3.5:9b")
        self.max_concurrent = self.env_config.get_int("MAX_CONCURRENT_REQUESTS", 10)
        self.request_timeout = self.env_config.get_int("REQUEST_TIMEOUT", 300)
        self.log_level = self.env_config.get("LOG_LEVEL", "INFO")

        # Logging
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_dir = self.project_root / "logs"
        self.log_dir.mkdir(exist_ok=True)

    def run_command(
            self, cmd: List[str], check: bool = True, capture: bool = False
    ) -> Tuple[int, str, str]:
        """Execute shell command safely"""
        print(Colors.info(f"Running: {' '.join(cmd)}"))
        try:
            result = subprocess.run(
                cmd,
                capture_output=capture,
                text=True,
                check=False,
            )
            return result.returncode, result.stdout, result.stderr
        except FileNotFoundError as e:
            print(Colors.error(f"Command not found: {e}"))
            return 1, "", str(e)

    def check_docker(self) -> bool:
        """Check if Docker is installed and running"""
        print(Colors.header("\n=== Docker Environment Check ===\n"))

        returncode, stdout, stderr = self.run_command(
            ["docker", "--version"], capture=True
        )
        if returncode != 0:
            print(Colors.error("Docker is not installed"))
            return False
        print(Colors.success(f"Docker installed: {stdout.strip()}"))

        returncode, stdout, stderr = self.run_command(
            ["docker", "info"], capture=True
        )
        if returncode != 0:
            print(Colors.error("Docker daemon is not running"))
            return False
        print(Colors.success("Docker daemon is running"))

        returncode, stdout, stderr = self.run_command(
            ["docker", "compose", "version"], capture=True
        )
        if returncode == 0:
            print(Colors.success(f"Docker Compose V2: {stdout.strip()}"))
        else:
            print(Colors.warning("Docker Compose V2 not found (optional)"))

        return True

    def check_ollama(self) -> bool:
        """Check if Ollama is accessible"""
        print(Colors.header("\n=== Ollama Service Check ===\n"))

        try:
            import requests
            response = requests.get(f"{self.ollama_url}/api/tags", timeout=5)
            if response.status_code == 200:
                print(Colors.success(f"Ollama is accessible at {self.ollama_url}"))
                data = response.json()
                models = data.get("models", [])
                if models:
                    print(Colors.info(f"Available models: {len(models)}"))
                    for model in models[:5]:
                        name = model.get("name", "unknown")
                        print(f"  - {name}")
                    if len(models) > 5:
                        print(f"  ... and {len(models) - 5} more")

                # Check for target model
                model_names = [m.get("name", "") for m in models]
                if any(self.model_name in name for name in model_names):
                    print(Colors.success(f"Target model '{self.model_name}' is available"))
                else:
                    print(Colors.warning(f"Model '{self.model_name}' not found"))
                    print(Colors.info(f"Pull it with: ollama pull {self.model_name}"))
                return True
            else:
                print(Colors.error(f"Ollama returned status {response.status_code}"))
                return False
        except Exception as e:
            print(Colors.warning(f"Ollama not accessible: {e}"))
            print(Colors.info(f"Make sure Ollama is running at {self.ollama_url}"))
            print(Colors.info("Start it with: ollama serve"))
            return False

    def get_container_info(self) -> Optional[dict]:
        """Get info about running container"""
        returncode, stdout, stderr = self.run_command(
            ["docker", "ps", "--filter", f"name={self.container_name}", "--format", "json"],
            capture=True
        )
        if returncode == 0 and stdout.strip():
            try:
                containers = json.loads(f"[{stdout.strip().replace(chr(10), ',')}]")
                return containers[0] if containers else None
            except json.JSONDecodeError:
                return None
        return None

    def get_image_info(self) -> Optional[dict]:
        """Get info about built image"""
        returncode, stdout, stderr = self.run_command(
            ["docker", "images", self.image_name, "--format", "json"],
            capture=True
        )
        if returncode == 0 and stdout.strip():
            try:
                return json.loads(stdout.strip())
            except json.JSONDecodeError:
                return None
        return None

    def status(self):
        """Show current Docker status"""
        print(Colors.header("\n=== Docker Status ===\n"))

        if not self.check_docker():
            return

        # Show configuration
        self.env_config.display()

        # Check image
        image = self.get_image_info()
        if image:
            print(Colors.success(f"Image exists: {self.image_name}"))
            print(f"  Tag: {image.get('Tag', 'N/A')}")
            print(f"  Size: {image.get('Size', 'N/A')}")
            print(f"  Created: {image.get('CreatedAt', 'N/A')}")
        else:
            print(Colors.warning(f"Image not found: {self.image_name}"))

        # Check container
        container = self.get_container_info()
        if container:
            status = container.get("State", "unknown")
            status_color = Colors.OKGREEN if status == "running" else Colors.WARNING
            print(Colors.success(f"Container exists: {self.container_name}"))
            print(f"  {status_color}Status: {status}{Colors.ENDC}")
            print(f"  ID: {container.get('ID', 'N/A')[:12]}")
            print(f"  Ports: {container.get('Ports', 'N/A')}")
        else:
            print(Colors.warning(f"Container not found: {self.container_name}"))

    def clean_images(self):
        """Remove old images"""
        print(Colors.header("\n=== Cleaning Images ===\n"))

        returncode, stdout, stderr = self.run_command(
            ["docker", "rmi", f"{self.image_name}:latest"],
            check=False,
            capture=True
        )
        if returncode == 0:
            print(Colors.success("Removed image: qwen-api-service:latest"))
        else:
            print(Colors.info("No image to remove"))

    def clean_containers(self):
        """Stop and remove containers"""
        print(Colors.header("\n=== Cleaning Containers ===\n"))

        # Stop container
        returncode, stdout, stderr = self.run_command(
            ["docker", "stop", self.container_name],
            check=False,
            capture=True
        )
        if returncode == 0:
            print(Colors.success(f"Stopped container: {self.container_name}"))
        else:
            print(Colors.info(f"Container {self.container_name} not running"))

        # Remove container
        returncode, stdout, stderr = self.run_command(
            ["docker", "rm", self.container_name],
            check=False,
            capture=True
        )
        if returncode == 0:
            print(Colors.success(f"Removed container: {self.container_name}"))
        else:
            print(Colors.info(f"Container {self.container_name} not found"))

    def clean_volumes(self):
        """Remove dangling volumes"""
        print(Colors.header("\n=== Cleaning Volumes ===\n"))

        returncode, stdout, stderr = self.run_command(
            ["docker", "volume", "prune", "-f"],
            capture=True
        )
        if returncode == 0:
            print(Colors.success("Cleaned dangling volumes"))
        else:
            print(Colors.warning("Volume cleanup returned error"))

    def clean_build_cache(self):
        """Clean build cache"""
        print(Colors.header("\n=== Cleaning Build Cache ===\n"))

        returncode, stdout, stderr = self.run_command(
            ["docker", "builder", "prune", "-a", "-f"],
            capture=True
        )
        if returncode == 0:
            print(Colors.success("Cleaned build cache"))
        else:
            print(Colors.warning("Build cache cleanup failed (optional)"))

    def full_reset(self):
        """Complete Docker reset"""
        print(Colors.header("\n╔════════════════════════════════════════════════╗"))
        print(Colors.header("║         FULL DOCKER RESET & CLEANUP            ║"))
        print(Colors.header("╚════════════════════════════════════════════════╝\n"))

        confirmation = input(
            f"{Colors.warning('This will remove the container and image. Continue? (yes/no): ')}"
        ).lower().strip()

        if confirmation != "yes":
            print(Colors.info("Reset cancelled"))
            return False

        self.clean_containers()
        self.clean_images()
        self.clean_volumes()
        self.clean_build_cache()

        print(Colors.success("\n✓ Full reset complete!\n"))
        return True

    def build(self, no_cache: bool = False):
        """Build Docker image"""
        print(Colors.header("\n=== Building Docker Image ===\n"))

        if not self.check_docker():
            print(Colors.error("Docker not available"))
            return False

        # Verify Dockerfile exists
        dockerfile = self.project_root / "Dockerfile"
        if not dockerfile.exists():
            print(Colors.error(f"Dockerfile not found at {dockerfile}"))
            return False

        # Show configuration being used
        print(Colors.info("Using configuration from .env:"))
        self.env_config.display()

        cmd = [
            "docker", "build",
            "--tag", f"{self.image_name}:latest",
            "--progress", "plain",
        ]

        if no_cache:
            cmd.append("--no-cache")
            print(Colors.info("Building without cache"))

        cmd.append(str(self.project_root))

        returncode, stdout, stderr = self.run_command(cmd, capture=False)

        if returncode == 0:
            print(Colors.success("✓ Image built successfully\n"))
            return True
        else:
            print(Colors.error(f"Build failed with return code {returncode}\n"))
            return False

    def run_with_compose(self):
        """Run container using docker-compose with .env"""
        print(Colors.header("\n=== Running Container (docker-compose) ===\n"))

        if not self.check_docker():
            print(Colors.error("Docker not available"))
            return False

        # Check if docker-compose.yml exists
        compose_file = self.project_root / "docker-compose.yml"
        if not compose_file.exists():
            print(Colors.error(f"docker-compose.yml not found at {compose_file}"))
            return False

        print(Colors.info("Starting service with docker-compose..."))
        print(Colors.info(f"Using .env file: {self.env_config.env_file}"))

        # Stop existing
        self.run_command(["docker-compose", "-f", str(compose_file), "down"], capture=True)

        # Start with compose
        returncode, stdout, stderr = self.run_command(
            ["docker-compose", "-f", str(compose_file), "up", "-d", "--build"],
            capture=False
        )

        if returncode == 0:
            print(Colors.success("✓ Service started with docker-compose\n"))
            return True
        else:
            print(Colors.error(f"docker-compose failed: {stderr}\n"))
            return False


    def run(self):
        """Run container with manual docker run (legacy)"""
        print(Colors.header("\n=== Running Container (docker run) ===\n"))

        if not self.check_docker():
            print(Colors.error("Docker not available"))
            return False

        # Check if container already running
        container = self.get_container_info()
        if container and container.get("State") == "running":
            print(Colors.warning(f"Container {self.container_name} is already running"))
            print(Colors.info(f"Stop it first: docker stop {self.container_name}"))
            return False

        # Remove old container if exists
        self.clean_containers()

        print(Colors.info("Configuration from .env:"))
        print(f"  OLLAMA_BASE_URL: {self.ollama_url}")
        print(f"  MODEL_NAME: {self.model_name}")
        print(f"  MAX_CONCURRENT_REQUESTS: {self.max_concurrent}")
        print(f"  LOG_LEVEL: {self.log_level}")
        print(f"  API_PORT: {self.port}\n")

        # Run container with .env values
        cmd = [
            "docker", "run",
            "--detach",
            "--name", self.container_name,
            "--publish", f"{self.port}:8000",
            "--env", f"OLLAMA_BASE_URL={self.ollama_url}",
            "--env", f"MODEL_NAME={self.model_name}",
            "--env", f"MAX_CONCURRENT_REQUESTS={self.max_concurrent}",
            "--env", f"LOG_LEVEL={self.log_level}",
            "--env", "PYTHONUNBUFFERED=1",
            "--restart", "unless-stopped",
            f"{self.image_name}:latest",
        ]

        returncode, stdout, stderr = self.run_command(cmd, capture=True)

        if returncode == 0:
            container_id = stdout.strip()[:12]
            print(Colors.success(f"Container started: {container_id}\n"))
            return True
        else:
            print(Colors.error(f"Failed to start container: {stderr}\n"))
            return False


    def logs(self, follow: bool = False, tail: int = 100):
        """Show container logs"""
        print(Colors.header(f"\n=== Container Logs (tail={tail}) ===\n"))

        cmd = ["docker", "logs"]
        if follow:
            cmd.append("-f")
        cmd.extend(["--tail", str(tail), self.container_name])

        self.run_command(cmd, capture=False)


    def health_check(self, retries: int = 30) -> bool:
        """Check if service is healthy"""
        print(Colors.header(f"\n=== Health Check (max {retries}s) ===\n"))

        try:
            import requests
        except ImportError:
            print(Colors.warning("requests not installed, skipping health check"))
            return True

        url = f"http://localhost:{self.port}/health"

        for i in range(retries):
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    print(Colors.success(f"Service is healthy!"))
                    try:
                        data = response.json()
                        print(f"\n  Status: {data.get('status', 'unknown')}")
                        print(f"  Model: {data.get('model', 'unknown')}")
                        print(f"  Ollama URL: {data.get('ollama_url', 'unknown')}")
                        print(f"  Max Concurrent: {data.get('max_concurrent', 'unknown')}")
                        print(f"  API Version: {data.get('api_version', 'unknown')}")
                        print(f"  Log Level: {data.get('log_level', 'unknown')}\n")
                    except Exception:
                        pass
                    return True
            except Exception:
                pass

            if i < retries - 1:
                print(f"Waiting for service... ({i + 1}/{retries})")
                time.sleep(1)

        print(Colors.error(f"Service health check failed after {retries}s"))
        print(Colors.info(f"Check logs: docker logs {self.container_name}"))
        return False


    def test_endpoint(self):
        """Test API endpoint"""
        print(Colors.header(f"\n=== Testing API Endpoint ===\n"))

        try:
            import requests
        except ImportError:
            print(Colors.warning("requests not installed, cannot test"))
            return False

        url = f"http://localhost:{self.port}/generate"
        payload = {
            "prompt": "What is 2+2?",
            "stream": False,
        }

        print(Colors.info(f"POST {url}"))
        print(f"Payload: {json.dumps(payload, indent=2)}\n")

        try:
            response = requests.post(url, json=payload, timeout=30)

            if response.status_code == 200:
                print(Colors.success("✓ Request successful\n"))
                data = response.json()

                # Pretty print relevant fields
                print(f"Model: {data.get('model')}")
                print(f"Response: {data.get('response')[:200]}...")
                print(f"Eval Count: {data.get('eval_count')} tokens")
                print(f"Total Duration: {data.get('total_duration') / 1e9:.2f}s\n")

                return True
            else:
                print(Colors.error(f"Status {response.status_code}"))
                print(response.text)
                return False
        except Exception as e:
            print(Colors.error(f"Request failed: {e}"))
            return False


    def shell(self):
        """Open shell in running container"""
        print(Colors.header(f"\n=== Opening Shell in Container ===\n"))

        container = self.get_container_info()
        if not container or container.get("State") != "running":
            print(Colors.error(f"Container {self.container_name} is not running"))
            return False

        self.run_command(["docker", "exec", "-it", self.container_name, "/bin/bash"])
        return True


    def full_build_and_run(self, use_compose: bool = True):
        """Complete build and run workflow"""
        print(Colors.header("\n╔════════════════════════════════════════════════╗"))
        print(Colors.header("║       FULL BUILD AND RUN WORKFLOW              ║"))
        print(Colors.header("╚════════════════════════════════════════════════╝\n"))

        # 1. Check environment
        if not self.check_docker():
            return False

        # 2. Show configuration
        self.env_config.display()

        # 3. Build image
        if not self.build():
            return False

        # 4. Run container
        if use_compose:
            if not self.run_with_compose():
                return False
        else:
            if not self.run():
                return False

        # 5. Health check
        time.sleep(2)
        self.health_check()

        # 6. Show info
        self.status()

        print(Colors.header("\n╔════════════════════════════════════════════════╗"))
        print(Colors.header("║              SETUP COMPLETE!                   ║"))
        print(Colors.header("╚════════════════════════════════════════════════╝\n"))
        print(Colors.success(f"Service running at http://localhost:{self.port}"))
        print(Colors.info(f"View logs: {Colors.bold('python3 docker-cli.py logs -f')}"))
        print(Colors.info(f"Test API: {Colors.bold('python3 docker-cli.py test')}"))
        print(Colors.info(f"Stop service: {Colors.bold('python3 docker-cli.py stop')}\n"))

        return True


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Docker CLI for Qwen API Service (with .env support)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 docker-cli.py full-reset      # Complete reset
  python3 docker-cli.py build           # Build image
  python3 docker-cli.py run             # Run with docker run (legacy)
  python3 docker-cli.py run-compose     # Run with docker-compose (.env)
  python3 docker-cli.py build-and-run   # Build and run with docker-compose
  python3 docker-cli.py logs -f         # Follow logs
  python3 docker-cli.py test            # Test API
  python3 docker-cli.py status          # Show status
  python3 docker-cli.py shell           # Open container shell
  python3 docker-cli.py check           # Check Docker and Ollama
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Reset command
    subparsers.add_parser("full-reset", help="Full reset: stop, remove, clean")

    # Build command
    build_parser = subparsers.add_parser("build", help="Build Docker image")
    build_parser.add_argument(
        "--no-cache", action="store_true", help="Build without cache"
    )

    # Run command
    subparsers.add_parser("run", help="Run container with docker run (legacy)")

    # Run with compose
    subparsers.add_parser("run-compose", help="Run container with docker-compose (.env)")

    # Build and run
    build_run_parser = subparsers.add_parser(
        "build-and-run", help="Build image and run with docker-compose"
    )
    build_run_parser.add_argument(
        "--no-compose", action="store_true", help="Use docker run instead of compose"
    )

    # Status command
    subparsers.add_parser("status", help="Show Docker status and config")

    # Check command
    subparsers.add_parser("check", help="Check Docker and Ollama")

    # Logs command
    logs_parser = subparsers.add_parser("logs", help="Show container logs")
    logs_parser.add_argument(
        "-f", "--follow", action="store_true", help="Follow logs"
    )
    logs_parser.add_argument(
        "-n", "--tail", type=int, default=100, help="Number of lines"
    )

    # Health command
    subparsers.add_parser("health", help="Check service health")

    # Test command
    subparsers.add_parser("test", help="Test API endpoint")

    # Shell command
    subparsers.add_parser("shell", help="Open shell in container")

    # Stop command
    subparsers.add_parser("stop", help="Stop container")

    args = parser.parse_args()

    cli = DockerCLI()

    # ========== FIXED: Handle all commands correctly ==========
    if args.command == "full-reset":
        cli.full_reset()
    elif args.command == "build":
        cli.build(no_cache=args.no_cache)
    elif args.command == "run":
        cli.run()
    elif args.command == "run-compose":
        cli.run_with_compose()
    elif args.command == "build-and-run":
        # Check if --no-compose flag is present
        use_compose = not getattr(args, 'no_compose', False)
        cli.full_build_and_run(use_compose=use_compose)
    elif args.command == "status":
        cli.status()
    elif args.command == "check":
        cli.check_docker()
        cli.check_ollama()
    elif args.command == "logs":
        cli.logs(follow=args.follow, tail=args.tail)
    elif args.command == "health":
        cli.health_check()
    elif args.command == "test":
        if cli.health_check(retries=5):
            cli.test_endpoint()
    elif args.command == "shell":
        cli.shell()
    elif args.command == "stop":
        cli.clean_containers()
    else:
        parser.print_help()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Colors.warning('Operation cancelled by user')}")
        sys.exit(1)
    except Exception as e:
        print(f"\n{Colors.error(f'Unexpected error: {e}')}")
        import traceback
        traceback.print_exc()  # Show full traceback for debugging
        sys.exit(1)