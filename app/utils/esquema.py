"""Confere se o banco em uso está com todas as migrações aplicadas.

Um banco sem migrações (ex.: clone novo sem `flask setup`) faria o login falhar com
"no such table". Em vez de um erro 500, o sistema registra o problema no log e responde 503.
"""
import os

from sqlalchemy import inspect, text

from app.extensions import db


def pasta_migracoes(app):
    return os.path.join(os.path.dirname(app.root_path), "migrations")


def revisoes_esperadas(app):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", pasta_migracoes(app))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def revisoes_aplicadas():
    if not inspect(db.engine).has_table("alembic_version"):
        return set()
    with db.engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version"))}


def estado_do_banco(app):
    """Retorna (pronto, aplicadas, esperadas)."""
    esperadas = revisoes_esperadas(app)
    aplicadas = revisoes_aplicadas()
    return aplicadas == esperadas, aplicadas, esperadas


def verificar_banco(app):
    """Atualiza app.config['BANCO_PRONTO'] e registra no log quando há migrações pendentes."""
    try:
        pronto, aplicadas, esperadas = estado_do_banco(app)
    except Exception:  # noqa: BLE001 - banco inacessível também é "não pronto"
        app.logger.exception("Não foi possível verificar o estado do banco de dados")
        pronto, aplicadas, esperadas = False, set(), set()
    if not pronto:
        app.logger.critical(
            "Banco de dados sem as migrações necessárias (aplicadas=%s, esperadas=%s). "
            "Faça um backup e execute: flask --app app setup", sorted(aplicadas) or "nenhuma", sorted(esperadas))
    app.config["BANCO_PRONTO"] = pronto
    return pronto
