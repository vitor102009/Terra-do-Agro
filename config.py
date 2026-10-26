import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
load_dotenv(os.path.join(BASE_DIR, ".env"))  # vale para python app.py, flask e servidores WSGI


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


def _load_secret_key():
    """Lê SECRET_KEY do ambiente. Em desenvolvimento, gera e persiste uma chave local."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    path = os.path.join(INSTANCE_DIR, "secret_key")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


class Config:
    ENV_MODE = os.environ.get("FLASK_ENV_MODE", "development")
    SECRET_KEY = None  # definido em create_app

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(INSTANCE_DIR, "terradoagro.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    WTF_CSRF_TIME_LIMIT = None  # o token vale enquanto a sessão existir

    UPLOAD_ROOT = os.path.join(INSTANCE_DIR, "uploads")
    MAX_CONTENT_LENGTH = 55 * 1024 * 1024  # limite global da requisição
    MAX_IMAGE_BYTES = 5 * 1024 * 1024
    MAX_VIDEO_BYTES = 50 * 1024 * 1024
    MAX_DOC_BYTES = 10 * 1024 * 1024

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587") or 587)
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", True)
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "Terra do Agro <nao-responda@localhost>")
    APP_BASE_URL = os.environ.get("APP_BASE_URL", "http://127.0.0.1:5000")
    SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")

    WEATHER_ENABLED = _bool("WEATHER_ENABLED", True)
    WEATHER_CACHE_MINUTES = 60

    # Cotações automáticas. BCB (PTAX): dados abertos, sem chave.
    QUOTES_BCB_ENABLED = _bool("QUOTES_BCB_ENABLED", True)
    # CEPEA: confirme os termos de uso com o CEPEA antes de ativar. IDs confirmados: 2 = Boi gordo, 8 = Bezerro MS.
    CEPEA_WIDGET_ENABLED = _bool("CEPEA_WIDGET_ENABLED", False)
    CEPEA_INDICADORES = os.environ.get("CEPEA_INDICADORES", "2,8")

    # IA Agro (Anthropic). Sem ANTHROPIC_API_KEY, a IA fica desativada com aviso na tela.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
    AI_EFFORT = os.environ.get("AI_EFFORT", "medium")
    AI_TIMEOUT_SECONDS = float(os.environ.get("AI_TIMEOUT_SECONDS", "60") or 60)
    AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "40") or 40)  # perguntas por usuário por dia
    AI_MINUTE_LIMIT = int(os.environ.get("AI_MINUTE_LIMIT", "5") or 5)  # perguntas por usuário por minuto
    AI_MAX_TOOL_ROUNDS = 6

    # Ative somente atrás de um proxy reverso confiável (PythonAnywhere: true).
    TRUST_PROXY_HEADERS = _bool("TRUST_PROXY_HEADERS", False)
    # Bloqueia o uso do sistema (503) enquanto houver migrações pendentes no banco.
    SCHEMA_CHECK = True

    LOGIN_MAX_ATTEMPTS = 5
    LOGIN_WINDOW_MINUTES = 15

    LOG_DIR = os.path.join(INSTANCE_DIR, "logs")
    BACKUP_DIR = os.path.join(BASE_DIR, "backups")


class TestConfig(Config):
    TESTING = True
    ENV_MODE = "testing"
    SECRET_KEY = "chave-apenas-para-testes"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    WEATHER_ENABLED = False
    QUOTES_BCB_ENABLED = False  # testes nunca acessam fontes externas
    CEPEA_WIDGET_ENABLED = False
    ANTHROPIC_API_KEY = ""  # testes reais da IA ficam em tests/test_ia_real.py
    SCHEMA_CHECK = False  # os testes criam as tabelas com create_all
    MAIL_SERVER = ""


def resolve_secret_key(configured):
    if configured:
        return configured
    if os.environ.get("FLASK_ENV_MODE") == "production" and not os.environ.get("SECRET_KEY"):
        raise RuntimeError("SECRET_KEY é obrigatória em produção. Configure-a no arquivo .env.")
    return _load_secret_key()
