"""Auditoria de ações e notificações internas."""
from flask import g, has_request_context, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import AuditLog, Notificacao


def auditar(acao, descricao, link=None, propriedade_id="atual", administrativa=False, user_id=None):
    """Adiciona um registro de auditoria à sessão atual (o commit fica com quem chamou)."""
    if propriedade_id == "atual":
        prop = g.get("propriedade") if has_request_context() else None
        propriedade_id = prop.id if prop else None
    if user_id is None and has_request_context() and current_user.is_authenticated:
        user_id = current_user.id
    ip = request.remote_addr if has_request_context() else None
    db.session.add(AuditLog(user_id=user_id, propriedade_id=propriedade_id, acao=acao, descricao=descricao[:300],
                            link=link, administrativa=administrativa, ip=ip))


def notificar(user_id, tipo, titulo, mensagem=None, link=None, chave=None):
    """Cria uma notificação interna. Com 'chave', a mesma notificação não é criada duas vezes."""
    if chave and Notificacao.query.filter_by(user_id=user_id, chave=chave).first():
        return None
    n = Notificacao(user_id=user_id, tipo=tipo, titulo=titulo[:150], mensagem=(mensagem or "")[:500],
                    link=link, chave=chave)
    if chave:
        # Savepoint: em corrida com outra requisição, a restrição única evita a duplicação.
        try:
            with db.session.begin_nested():
                db.session.add(n)
        except IntegrityError:
            return None
    else:
        db.session.add(n)
    return n
