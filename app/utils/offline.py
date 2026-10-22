import hashlib
import hmac

from flask import current_app


def chave_offline(user_id):
    """Identificador opaco do usuário para isolar o armazenamento local (IndexedDB) no aparelho."""
    return hmac.new(current_app.config["SECRET_KEY"].encode(), f"offline:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]
