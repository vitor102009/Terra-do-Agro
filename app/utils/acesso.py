"""Controle de acesso: administradores, papéis e vínculo com propriedades."""
from functools import wraps

from flask import abort, flash, g, redirect, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Propriedade, PropriedadeMembro


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def carregar_propriedade_ativa():
    """Define g.propriedade e g.membro conforme a propriedade ativa e o vínculo real do usuário."""
    g.propriedade = None
    g.membro = None
    g.minhas_propriedades = []
    if not current_user.is_authenticated:
        return
    membros = (
        db.session.query(PropriedadeMembro)
        .join(Propriedade)
        .filter(PropriedadeMembro.user_id == current_user.id, Propriedade.arquivada.is_(False))
        .order_by(Propriedade.nome)
        .all()
    )
    g.minhas_propriedades = membros
    if not membros:
        return
    escolhido = next((m for m in membros if m.propriedade_id == current_user.propriedade_ativa_id), None)
    if escolhido is None:
        escolhido = membros[0]
        current_user.propriedade_ativa_id = escolhido.propriedade_id
        db.session.commit()
    g.propriedade = escolhido.propriedade
    g.membro = escolhido


def pode(perm):
    membro = g.get("membro")
    return bool(membro and perm in membro.perms)


def propriedade_requerida(perm="ver"):
    """Exige uma propriedade ativa com a permissão indicada (ver, editar, financeiro, membros)."""

    def deco(view):
        @wraps(view)
        @login_required
        def wrapper(*args, **kwargs):
            if not g.get("propriedade"):
                flash("Cadastre uma propriedade ou peça acesso a uma para usar este módulo.", "info")
                return redirect(url_for("propriedades.nova", next=request.path))
            if not pode(perm):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return deco


def obter_da_propriedade(model, obj_id):
    """Carrega um registro garantindo que pertence à propriedade ativa. Caso contrário, 404."""
    obj = db.session.get(model, obj_id)
    if obj is None or getattr(obj, "propriedade_id", None) != g.propriedade.id:
        abort(404)
    return obj


def obter_do_usuario(model, obj_id, campo="user_id"):
    obj = db.session.get(model, obj_id)
    if obj is None or getattr(obj, campo) != current_user.id:
        abort(404)
    return obj


def membro_de(user_id, propriedade_id):
    return PropriedadeMembro.query.filter_by(user_id=user_id, propriedade_id=propriedade_id).first()
