import logging
import os
from logging.handlers import RotatingFileHandler

from flask import Flask, g, jsonify, render_template, request
from flask_login import current_user
from flask_wtf.csrf import CSRFError

from config import Config, resolve_secret_key
from app.extensions import csrf, db, login_manager, migrate

BLUEPRINTS = [
    ("app.main.routes", "bp", None),
    ("app.auth.routes", "bp", None),
    ("app.conta.routes", "bp", "/conta"),
    ("app.propriedades.routes", "bp", "/propriedades"),
    ("app.animais.routes", "bp", "/rebanho"),
    ("app.lotes.routes", "bp", "/lotes"),
    ("app.pesagens.routes", "bp", "/pesagens"),
    ("app.saude.routes", "bp", "/sanidade"),
    ("app.reproducao.routes", "bp", "/reproducao"),
    ("app.insumos.routes", "bp", "/insumos"),
    ("app.financeiro.routes", "bp", "/financeiro"),
    ("app.relatorios.routes", "bp", "/relatorios"),
    ("app.marketplace.routes", "bp", "/marketplace"),
    ("app.profissionais.routes", "bp", "/profissionais"),
    ("app.servicos.routes", "bp", "/servicos"),
    ("app.vagas.routes", "bp", "/vagas"),
    ("app.chat.routes", "bp", "/mensagens"),
    ("app.comunidade.routes", "bp", "/comunidade"),
    ("app.feed.routes", "bp", "/feed"),
    ("app.notificacoes.routes", "bp", None),
    ("app.clima.routes", "bp", None),
    ("app.ia.routes", "bp", "/ia"),
    ("app.admin.routes", "bp", "/admin"),
    ("app.api.routes", "bp", "/api"),
]


def create_app(config_object=None):
    app = Flask(__name__, instance_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), "instance"))
    app.config.from_object(config_object or Config)
    app.config["SECRET_KEY"] = resolve_secret_key(app.config.get("SECRET_KEY"))
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_ROOT"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)

    _configurar_sqlite(app)
    _configurar_logs(app)

    from app.utils.formatos import registrar_filtros

    registrar_filtros(app)

    import importlib

    for modulo, attr, prefixo in BLUEPRINTS:
        bp = getattr(importlib.import_module(modulo), attr)
        app.register_blueprint(bp, url_prefix=prefixo)

    _registrar_erros(app)
    _registrar_hooks(app)

    from app.cli import registrar_cli

    registrar_cli(app)

    if app.config.get("TRUST_PROXY_HEADERS"):
        # Atrás de proxy (PythonAnywhere, Nginx): IP real do cliente e HTTPS. Sem isso, todos os acessos
        # teriam o IP do proxy e 5 senhas erradas de qualquer pessoa bloqueariam o login de todos.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    # Conferido na primeira requisição (os comandos `flask setup`/`db` não passam por aqui).
    app.config["BANCO_PRONTO"] = not app.config.get("SCHEMA_CHECK", True)
    return app


def _pragmas_sqlite(dbapi_conn, _record):
    if "sqlite" in type(dbapi_conn).__module__:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def _configurar_sqlite(app):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    if not event.contains(Engine, "connect", _pragmas_sqlite):
        event.listen(Engine, "connect", _pragmas_sqlite)


def _configurar_logs(app):
    if app.testing:
        return
    os.makedirs(app.config["LOG_DIR"], exist_ok=True)
    handler = RotatingFileHandler(os.path.join(app.config["LOG_DIR"], "terradoagro.log"),
                                  maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    handler.setLevel(logging.INFO)
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def _quer_json():
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def _registrar_erros(app):
    @app.errorhandler(404)
    def nao_encontrado(_e):
        if _quer_json():
            return jsonify(erro="Recurso não encontrado."), 404
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def proibido(_e):
        if _quer_json():
            return jsonify(erro="Acesso não permitido."), 403
        return render_template("errors/403.html"), 403

    @app.errorhandler(413)
    def grande_demais(_e):
        if _quer_json():
            return jsonify(erro="Arquivo muito grande."), 413
        return render_template("errors/erro.html", codigo=413, titulo="Arquivo muito grande",
                               mensagem="O arquivo enviado ultrapassa o limite permitido."), 413

    @app.errorhandler(CSRFError)
    def csrf_invalido(_e):
        if _quer_json():
            return jsonify(erro="Sessão expirada. Atualize a página e tente novamente."), 400
        return render_template("errors/erro.html", codigo=400, titulo="Formulário expirado",
                               mensagem="Por segurança, o formulário expirou. Volte, atualize a página e envie novamente."), 400

    @app.errorhandler(500)
    def erro_interno(e):
        # Detalhes vão apenas para o log; o usuário vê uma mensagem genérica.
        app.logger.error("Erro interno em %s %s: %s", request.method, request.path, getattr(e, "original_exception", e),
                         exc_info=getattr(e, "original_exception", None) or True)
        try:
            db.session.rollback()
        except Exception:  # noqa: BLE001
            pass
        if _quer_json():
            return jsonify(erro="Erro interno. Tente novamente mais tarde."), 500
        return render_template("errors/500.html"), 500


def _registrar_hooks(app):
    from app.utils.acesso import carregar_propriedade_ativa, pode
    from app.utils.offline import chave_offline

    @app.before_request
    def antes():
        if request.endpoint and (request.endpoint == "static" or request.endpoint.startswith("main.pwa_")):
            return
        if not app.config.get("BANCO_PRONTO"):
            from app.utils.esquema import verificar_banco

            if not verificar_banco(app):  # revalida: o setup pode ter sido feito sem reiniciar
                if _quer_json():
                    return jsonify(erro="Sistema em manutenção. Tente novamente em alguns minutos."), 503
                return render_template("errors/erro.html", codigo=503, titulo="Sistema em manutenção",
                                       mensagem="O sistema está sendo atualizado e volta em alguns minutos. "
                                                "Seus dados estão preservados."), 503
        carregar_propriedade_ativa()

    @app.after_request
    def cabecalhos(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(self), geolocation=(self)")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "font-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'",
        )
        # Páginas autenticadas nunca devem ficar em cache compartilhado ou no disco do navegador.
        if current_user.is_authenticated and request.endpoint != "static" and "Cache-Control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store, private"
        return resp

    @app.context_processor
    def contexto():
        from app.models import Mensagem, Notificacao, Conversa
        from app.utils.validacao import UFS

        dados = {"pode": pode, "propriedade_atual": g.get("propriedade"), "membro_atual": g.get("membro"),
                 "minhas_propriedades": g.get("minhas_propriedades", []), "ufs": {u: u for u in UFS},
                 "support_email": app.config.get("SUPPORT_EMAIL"), "notif_nao_lidas": 0, "msg_nao_lidas": 0}
        try:
            if current_user.is_authenticated:
                from app.models import Configuracao

                aviso = db.session.get(Configuracao, "aviso_global")
                dados["aviso_global"] = aviso.valor if aviso and aviso.valor else None
                dados["offline_key"] = chave_offline(current_user.id)
                dados["notif_nao_lidas"] = Notificacao.query.filter_by(user_id=current_user.id, lida=False).count()
                dados["msg_nao_lidas"] = (
                    db.session.query(Mensagem.id).join(Conversa)
                    .filter(db.or_(Conversa.user_a_id == current_user.id, Conversa.user_b_id == current_user.id),
                            Mensagem.remetente_id != current_user.id, Mensagem.lida_em.is_(None))
                    .count()
                )
        except Exception:  # noqa: BLE001 - o layout não pode quebrar a renderização (ex.: página 500)
            app.logger.exception("Falha ao montar contexto do layout")
            db.session.rollback()
        return dados


@login_manager.user_loader
def carregar_usuario(user_id):
    from app.models import User

    try:
        uid, versao = user_id.split(":")
        user = db.session.get(User, int(uid))
    except (ValueError, AttributeError):
        return None
    if user is None or str(user.session_version) != versao or not user.ativo:
        return None
    return user
