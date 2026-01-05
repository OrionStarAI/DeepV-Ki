import os
import json
import logging
import re
from pathlib import Path
from typing import List, Union, Dict, Any

logger = logging.getLogger(__name__)

from api.openai_client import OpenAIClient
from api.openrouter_client import OpenRouterClient
from api.bedrock_client import BedrockClient
from api.azureai_client import AzureAIClient
from api.dashscope_client import DashscopeClient
from adalflow import GoogleGenAIClient, OllamaClient

# Get API keys from environment variables
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
DASHSCOPE_API_KEY = os.environ.get('DASHSCOPE_API_KEY')
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
AWS_REGION = os.environ.get('AWS_REGION')
AWS_ROLE_ARN = os.environ.get('AWS_ROLE_ARN')

# Wiki authentication settings
raw_auth_mode = os.environ.get('DEEPWIKI_AUTH_MODE', 'False')
WIKI_AUTH_MODE = raw_auth_mode.lower() in ['true', '1', 't']
WIKI_AUTH_CODE = os.environ.get('DEEPWIKI_AUTH_CODE', '')

# SSO Configuration (OIDC)
SSO_CLIENT_ID = os.environ.get('SSO_CLIENT_ID')
SSO_CLIENT_SECRET = os.environ.get('SSO_CLIENT_SECRET')
SSO_SERVER_METADATA_URL = os.environ.get('SSO_SERVER_METADATA_URL', 'https://sso.example.com/realms/oa-sso/.well-known/openid-configuration')

# GitLab OAuth Configuration
GITLAB_CLIENT_ID = os.environ.get('GITLAB_CLIENT_ID')
GITLAB_CLIENT_SECRET = os.environ.get('GITLAB_CLIENT_SECRET')
GITLAB_REDIRECT_URI = os.environ.get('GITLAB_REDIRECT_URI')
GITLAB_URL = os.environ.get('GITLAB_URL', 'https://gitlab.com')

# Session Secret Key
SESSION_SECRET_KEY = os.environ.get('SESSION_SECRET_KEY')
if not SESSION_SECRET_KEY:
    # In production, this should be a hard error or at least a warning
    if os.environ.get('NODE_ENV') == 'production':
        logger.warning("SESSION_SECRET_KEY not set in production! Using insecure default.")
    SESSION_SECRET_KEY = "deepwiki-super-secret-key"

# Frontend URL for SSO redirects
FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:3000')

# Set keys in environment (in case they're needed elsewhere in the code)
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
if GOOGLE_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
if OPENROUTER_API_KEY:
    os.environ["OPENROUTER_API_KEY"] = OPENROUTER_API_KEY
if DASHSCOPE_API_KEY:
    os.environ["DASHSCOPE_API_KEY"] = DASHSCOPE_API_KEY
if AWS_ACCESS_KEY_ID:
    os.environ["AWS_ACCESS_KEY_ID"] = AWS_ACCESS_KEY_ID
if AWS_SECRET_ACCESS_KEY:
    os.environ["AWS_SECRET_ACCESS_KEY"] = AWS_SECRET_ACCESS_KEY
if AWS_REGION:
    os.environ["AWS_REGION"] = AWS_REGION
if AWS_ROLE_ARN:
    os.environ["AWS_ROLE_ARN"] = AWS_ROLE_ARN

# Wiki authentication settings
raw_auth_mode = os.environ.get('DEEPWIKI_AUTH_MODE', 'False')
WIKI_AUTH_MODE = raw_auth_mode.lower() in ['true', '1', 't']
WIKI_AUTH_CODE = os.environ.get('DEEPWIKI_AUTH_CODE', '')

# Get configuration directory from environment variable, or use default if not set
CONFIG_DIR = os.environ.get('DEEPWIKI_CONFIG_DIR', None)

# Client class mapping
CLIENT_CLASSES = {
    "GoogleGenAIClient": GoogleGenAIClient,
    "OpenAIClient": OpenAIClient,
    "OpenRouterClient": OpenRouterClient,
    "OllamaClient": OllamaClient,
    "BedrockClient": BedrockClient,
    "AzureAIClient": AzureAIClient,
    "DashscopeClient": DashscopeClient
}

def replace_env_placeholders(config: Union[Dict[str, Any], List[Any], str, Any]) -> Union[Dict[str, Any], List[Any], str, Any]:
    """
    Recursively replace placeholders like "${ENV_VAR}" in string values
    within a nested configuration structure (dicts, lists, strings)
    with environment variable values. Logs a warning if a placeholder is not found.
    """
    pattern = re.compile(r"\$\{([A-Z0-9_]+)\}")

    def replacer(match: re.Match[str]) -> str:
        env_var_name = match.group(1)
        original_placeholder = match.group(0)
        env_var_value = os.environ.get(env_var_name)
        if env_var_value is None:
            logger.warning(
                f"Environment variable placeholder '{original_placeholder}' was not found in the environment. "
                f"The placeholder string will be used as is."
            )
            return original_placeholder
        return env_var_value

    if isinstance(config, dict):
        return {k: replace_env_placeholders(v) for k, v in config.items()}
    elif isinstance(config, list):
        return [replace_env_placeholders(item) for item in config]
    elif isinstance(config, str):
        return pattern.sub(replacer, config)
    else:
        # Handles numbers, booleans, None, etc.
        return config

# Load JSON configuration file
def load_json_config(filename):
    try:
        # If environment variable is set, use the directory specified by it
        if CONFIG_DIR:
            config_path = Path(CONFIG_DIR) / filename
        else:
            # Otherwise use default directory
            config_path = Path(__file__).parent / "config" / filename

        logger.info(f"Loading configuration from {config_path}")

        if not config_path.exists():
            logger.warning(f"Configuration file {config_path} does not exist")
            return {}

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            config = replace_env_placeholders(config)
            return config
    except Exception as e:
        logger.error(f"Error loading configuration file {filename}: {str(e)}")
        return {}

# Load generator model configuration
def load_generator_config():
    generator_config = load_json_config("generator.json")

    # Add client classes to each provider
    if "providers" in generator_config:
        for provider_id, provider_config in generator_config["providers"].items():
            # Try to set client class from client_class
            if provider_config.get("client_class") in CLIENT_CLASSES:
                provider_config["model_client"] = CLIENT_CLASSES[provider_config["client_class"]]
            # Fall back to default mapping based on provider_id
            elif provider_id in ["google", "openai", "openrouter", "ollama", "bedrock", "azure", "dashscope"]:
                default_map = {
                    "google": GoogleGenAIClient,
                    "openai": OpenAIClient,
                    "openrouter": OpenRouterClient,
                    "ollama": OllamaClient,
                    "bedrock": BedrockClient,
                    "azure": AzureAIClient,
                    "dashscope": DashscopeClient
                }
                provider_config["model_client"] = default_map[provider_id]
            else:
                logger.warning(f"Unknown provider or client class: {provider_id}")

    return generator_config

# Load embedder configuration
def load_embedder_config():
    embedder_config = load_json_config("embedder.json")

    # Process client classes
    for key in ["embedder", "embedder_ollama"]:
        if key in embedder_config and "client_class" in embedder_config[key]:
            class_name = embedder_config[key]["client_class"]
            if class_name in CLIENT_CLASSES:
                embedder_config[key]["model_client"] = CLIENT_CLASSES[class_name]

    return embedder_config

def get_embedder_config():
    """
    Get the current embedder configuration.

    Returns:
        dict: The embedder configuration with model_client resolved
    """
    return configs.get("embedder", {})

def is_ollama_embedder():
    """
    Check if the current embedder configuration uses OllamaClient.

    Returns:
        bool: True if using OllamaClient, False otherwise
    """
    embedder_config = get_embedder_config()
    if not embedder_config:
        return False

    # Check if model_client is OllamaClient
    model_client = embedder_config.get("model_client")
    if model_client:
        return model_client.__name__ == "OllamaClient"

    # Fallback: check client_class string
    client_class = embedder_config.get("client_class", "")
    return client_class == "OllamaClient"

# Load repository and file filters configuration
def load_repo_config():
    return load_json_config("repo.json")

# Load language configuration
def load_lang_config():
    default_config = {
        "supported_languages": {
            "en": "English",
            "ja": "Japanese (日本語)",
            "zh": "Mandarin Chinese (中文)",
            "zh-tw": "Traditional Chinese (繁體中文)",
            "es": "Spanish (Español)",
            "kr": "Korean (한국어)",
            "vi": "Vietnamese (Tiếng Việt)",
            "pt-br": "Brazilian Portuguese (Português Brasileiro)",
            "fr": "Français (French)",
            "ru": "Русский (Russian)"
        },
        "default": "en"
    }

    loaded_config = load_json_config("lang.json") # Let load_json_config handle path and loading

    if not loaded_config:
        return default_config

    if "supported_languages" not in loaded_config or "default" not in loaded_config:
        logger.warning("Language configuration file 'lang.json' is malformed. Using default language configuration.")
        return default_config

    return loaded_config

# Default excluded directories and files
DEFAULT_EXCLUDED_DIRS: List[str] = [
    # Virtual environments and package managers
    "./.venv/", "./venv/", "./env/", "./virtualenv/",
    "./node_modules/", "./bower_components/", "./jspm_packages/",

    # Version control (contains sensitive history)
    "./.git/", "./.svn/", "./.hg/", "./.bzr/",

    # Cache and compiled files
    "./__pycache__/", "./.pytest_cache/", "./.mypy_cache/", "./.ruff_cache/", "./.coverage/",

    # Build and distribution
    "./dist/", "./build/", "./out/", "./out-*/", "./target/", "./bin/", "./obj/",
    "./.next/", "./.nuxt/", "./.parcel-cache/", "./.astro/",

    # Documentation
    "./docs/", "./_docs/", "./site-docs/", "./_site/",

    # IDE specific (可能包含敏感配置)
    "./.idea/", "./.vscode/", "./.vs/", "./.eclipse/", "./.settings/",

    # Logs and temporary files (可能包含敏感信息)
    "./logs/", "./log/", "./tmp/", "./temp/", "./cache/", "./.cache/",
    "./.parcel-cache/",

    # Security-sensitive directories 🔐
    "./.aws/", "./.ssh/", "./.kube/", "./secrets/", "./private/",
    "./.docker/", "./credentials/",
]

DEFAULT_EXCLUDED_FILES: List[str] = [
    # Package locks and dependencies
    "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json", "poetry.lock",
    "Pipfile.lock", "requirements.txt.lock", "Cargo.lock", "composer.lock",
    ".lock", ".DS_Store", "Thumbs.db", "desktop.ini", "*.lnk",

    # Environment and configuration (敏感文件 🔐)
    ".env", ".env.*", "*.env", ".flaskenv", "*.env.local", "*.env.example",
    "*.cfg", "*.ini", ".flaskenv", "secrets.json", "secrets.yaml", "secrets.yml",
    "credentials.json", "credentials.yaml", "credentials.yml",
    "config.secrets.json", "config.secrets.yaml", "config.secrets.yml",
    ".aws", ".aws/*", "aws_credentials", ".ssh", ".ssh/*", "private_key*",
    "id_rsa", "id_rsa.*", "*.pem", "*.key", "*.p8", "*.p12", "*.pfx",
    ".docker", ".docker/*", ".dockercfg", ".dockerconfigjson",

    # VCS and git config
    ".gitignore", ".gitattributes", ".gitmodules", ".github", ".gitlab-ci.yml",
    ".gitconfig", "git.config",

    # Build and tool configuration
    ".prettierrc", ".eslintrc", ".eslintignore", ".stylelintrc",
    ".editorconfig", ".jshintrc", ".pylintrc", ".flake8", "mypy.ini",
    "pyproject.toml", "tsconfig.json", "webpack.config.js", "babel.config.js",
    "rollup.config.js", "jest.config.js", "karma.conf.js", "vite.config.js",
    "next.config.js",

    # Minified and bundled code
    "*.min.js", "*.min.css", "*.bundle.js", "*.bundle.css",
    "*.map",

    # Archives and compressed files
    "*.gz", "*.zip", "*.tar", "*.tgz", "*.rar", "*.7z", "*.iso",
    "*.dmg", "*.img", "*.msix", "*.appx", "*.appxbundle", "*.xap", "*.ipa",

    # Binary and executable files
    "*.deb", "*.rpm", "*.msi", "*.exe", "*.dll", "*.so", "*.dylib", "*.o",
    "*.obj", "*.jar", "*.war", "*.ear", "*.jsm", "*.class", "*.pyc", "*.pyd",
    "*.pyo", "__pycache__", "*.a", "*.lib", "*.lo", "*.la", "*.slo", "*.dSYM",
    "*.egg", "*.egg-info", "*.dist-info", "*.eggs",

    # Package directories
    "node_modules", "bower_components", "jspm_packages", "lib-cov", "coverage", "htmlcov",
    ".nyc_output", ".tox", "dist", "build", "bld", "out", "bin", "target",
    "packages/*/dist", "packages/*/build", ".output"
]

# Initialize empty configuration
configs = {}

# Load all configuration files
generator_config = load_generator_config()
embedder_config = load_embedder_config()
repo_config = load_repo_config()
lang_config = load_lang_config()

# Update configuration
if generator_config:
    # Intelligent default provider selection
    default_provider = generator_config.get("default_provider", "google")

    # Check available API keys
    has_openai = bool(OPENAI_API_KEY)
    has_google = bool(GOOGLE_API_KEY)
    has_openrouter = bool(OPENROUTER_API_KEY)
    has_dashscope = bool(DASHSCOPE_API_KEY)

    # If only DashScope is available, switch default to dashscope
    if has_dashscope and not (has_openai or has_google or has_openrouter):
        logger.info("🤖 Only DashScope API key found. Switching default provider to 'dashscope'.")
        default_provider = "dashscope"

        # Also update embedder config if needed
        if "embedder" in configs and configs["embedder"].get("client_class") != "DashscopeClient":
            logger.info("🔄 Switching embedder to DashScope")
            configs["embedder"] = {
                "client_class": "DashscopeClient",
                "batch_size": 25,
                "model_kwargs": {
                    "model": "text-embedding-v3",
                    "dimensions": 1024,
                    "encoding_format": "float"
                }
            }
            # Update configs dictionary
            embedder_config["embedder"] = configs["embedder"]
            # Ensure model_client is set correctly for DashScope
            configs["embedder"]["model_client"] = DashscopeClient

    configs["default_provider"] = default_provider
    configs["providers"] = generator_config.get("providers", {})

    # 获取默认 provider 的默认 model
    if default_provider in generator_config.get("providers", {}):
        default_model = generator_config["providers"][default_provider].get("default_model")
        if default_model:
            configs["default_model"] = default_model

# Update embedder configuration
if embedder_config:
    for key in ["embedder", "embedder_ollama", "retriever", "text_splitter"]:
        if key in embedder_config:
            configs[key] = embedder_config[key]

# Update repository configuration
if repo_config:
    for key in ["file_filters", "repository"]:
        if key in repo_config:
            configs[key] = repo_config[key]

# Update language configuration
if lang_config:
    configs["lang_config"] = lang_config


def get_model_config(provider="google", model=None):
    """
    Get configuration for the specified provider and model

    Parameters:
        provider (str): Model provider ('google', 'openai', 'openrouter', 'ollama', 'bedrock')
        model (str): Model name, or None to use default model

    Returns:
        dict: Configuration containing model_client, model and other parameters
    """
    # Get provider configuration
    if "providers" not in configs:
        raise ValueError("Provider configuration not loaded")

    provider_config = configs["providers"].get(provider)
    if not provider_config:
        raise ValueError(f"Configuration for provider '{provider}' not found")

    model_client = provider_config.get("model_client")
    if not model_client:
        raise ValueError(f"Model client not specified for provider '{provider}'")

    # If model not provided, use default model for the provider
    if not model:
        model = provider_config.get("default_model")
        if not model:
            raise ValueError(f"No default model specified for provider '{provider}'")

    # Get model parameters (if present)
    model_params = {}
    if model in provider_config.get("models", {}):
        model_params = provider_config["models"][model]
    else:
        default_model = provider_config.get("default_model")
        model_params = provider_config["models"][default_model]

    # Prepare base configuration
    result = {
        "model_client": model_client,
    }

    # Provider-specific adjustments
    if provider == "ollama":
        # Ollama uses a slightly different parameter structure
        if "options" in model_params:
            result["model_kwargs"] = {"model": model, **model_params["options"]}
        else:
            result["model_kwargs"] = {"model": model}
        # Ollama always needs default values for these parameters
        if "temperature" not in result["model_kwargs"]:
            result["model_kwargs"]["temperature"] = 0.7
        if "top_p" not in result["model_kwargs"]:
            result["model_kwargs"]["top_p"] = 0.95
        if "num_ctx" not in result["model_kwargs"]:
            result["model_kwargs"]["num_ctx"] = 32000
    else:
        # Standard structure for other providers
        result["model_kwargs"] = {"model": model, **model_params}
        # Add default values for temperature and top_p if not specified
        if "temperature" not in result["model_kwargs"]:
            result["model_kwargs"]["temperature"] = 0.7
        if "top_p" not in result["model_kwargs"]:
            result["model_kwargs"]["top_p"] = 0.95

    return result
