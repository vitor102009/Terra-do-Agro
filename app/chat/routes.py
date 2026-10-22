import re

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Anuncio, Bloqueio, Conversa, Mensagem, SolicitacaoServico, User, Vaga, utcnow
from app.utils.formatos import datahora_br, hora_br
from app.utils.registro import notificar

bp = Blueprint("chat", __name__)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def bloqueado_entre(a, b):
    return Bloqueio.query.filter(db.or_(db.and_(Bloqueio.user_id == a, Bloqueio.bloqueado_id == b),
                                        db.and_(Bloqueio.user_id == b, Bloqueio.bloqueado_id == a))).first() is not None


def conversa_do_usuario(cid):
    conv = db.session.get(Conversa, cid)
    if not conv or not conv.participa(current_user.id):
        abort(404)
    return conv


def serializar(msg):
    d = {"id": msg.id, "texto": msg.texto, "minha": msg.remetente_id == current_user.id, "remetente": msg.remetente.primeiro_nome,
         "hora": hora_br(msg.created_at), "data": datahora_br(msg.created_at), "uuid": msg.client_uuid, "lida": bool(msg.lida_em)}
    if msg.anuncio:
        d["compartilhado"] = {"tipo": "Anúncio", "titulo": msg.anuncio.titulo, "url": url_for("marketplace.detalhe", aid=msg.anuncio.id)}
    elif msg.solicitacao:
        d["compartilhado"] = {"tipo": "Contratação", "titulo": msg.solicitacao.titulo, "url": url_for("servicos.detalhe", sid=msg.solicitacao.id)}
    elif msg.vaga:
        d["compartilhado"] = {"tipo": "Vaga", "titulo": msg.vaga.cargo, "url": url_for("vagas.detalhe", vid=msg.vaga.id)}
    return d


def _conversas():
    convs = (Conversa.query.filter(db.or_(Conversa.user_a_id == current_user.id, Conversa.user_b_id == current_user.id))
             .order_by(Conversa.ultima_mensagem_em.desc()).all())
    out = []
    for c in convs:
        ultima = c.mensagens.order_by(Mensagem.id.desc()).first()
        nao_lidas = c.mensagens.filter(Mensagem.remetente_id != current_user.id, Mensagem.lida_em.is_(None)).count()
        out.append({"c": c, "outro": c.outro(current_user.id), "ultima": ultima, "nao_lidas": nao_lidas})
    return out


@bp.route("/")
@login_required
def index():
    ativa = None
    compartilhar = None
    if request.args.get("c", type=int):
        ativa = conversa_do_usuario(request.args.get("c", type=int))
        agora = utcnow()
        ativa.mensagens.filter(Mensagem.remetente_id != current_user.id, Mensagem.lida_em.is_(None)).update(
            {"lida_em": agora}, synchronize_session=False)
        db.session.commit()
        compartilhar = _referencia(request.args, ativa)
    mensagens = ativa.mensagens.order_by(Mensagem.id.desc()).limit(100).all()[::-1] if ativa else []
    outro = ativa.outro(current_user.id) if ativa else None
    bloqueio = None
    if ativa:
        bloqueio = Bloqueio.query.filter_by(user_id=current_user.id, bloqueado_id=outro.id).first()
    return render_template("chat/index.html", conversas=_conversas(), ativa=ativa, outro=outro, mensagens=[serializar(m) for m in mensagens],
                           compartilhar=compartilhar, bloqueio=bloqueio,
                           bloqueado_por_outro=ativa and Bloqueio.query.filter_by(user_id=outro.id, bloqueado_id=current_user.id).first())


def _referencia(args, conv):
    """Valida o item a compartilhar (anúncio ativo, contratação entre os dois participantes ou vaga)."""
    if args.get("anuncio", type=int):
        an = db.session.get(Anuncio, args.get("anuncio", type=int))
        if an and (an.status == "ativo" or an.user_id == current_user.id):
            return {"campo": "anuncio_id", "id": an.id, "tipo": "Anúncio", "titulo": an.titulo}
    if args.get("solicitacao", type=int):
        s = db.session.get(SolicitacaoServico, args.get("solicitacao", type=int))
        if s and {s.cliente_id, s.perfil.user_id} == {conv.user_a_id, conv.user_b_id}:
            return {"campo": "solicitacao_id", "id": s.id, "tipo": "Contratação", "titulo": s.titulo}
    if args.get("vaga", type=int):
        v = db.session.get(Vaga, args.get("vaga", type=int))
        if v and v.status != "removida":
            return {"campo": "vaga_id", "id": v.id, "tipo": "Vaga", "titulo": v.cargo}
    return None


@bp.route("/iniciar/<int:user_id>")
@login_required
def iniciar(user_id):
    if user_id == current_user.id:
        flash("Você não pode enviar mensagens para si mesmo.", "warning")
        return redirect(url_for("chat.index"))
    outro = db.session.get(User, user_id)
    if not outro or not outro.ativo:
        abort(404)
    a, b = sorted((current_user.id, user_id))
    conv = Conversa.query.filter_by(user_a_id=a, user_b_id=b).first()
    if not conv:
        conv = Conversa(user_a_id=a, user_b_id=b)
        db.session.add(conv)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            conv = Conversa.query.filter_by(user_a_id=a, user_b_id=b).first()
    extras = {k: request.args[k] for k in ("anuncio", "solicitacao", "vaga") if request.args.get(k)}
    return redirect(url_for("chat.index", c=conv.id, **extras))


@bp.route("/<int:cid>/mensagens.json")
@login_required
def mensagens(cid):
    conv = conversa_do_usuario(cid)
    depois = request.args.get("depois", 0, type=int)
    novas = conv.mensagens.filter(Mensagem.id > depois).order_by(Mensagem.id).limit(200).all()
    recebidas = [m for m in novas if m.remetente_id != current_user.id and not m.lida_em]
    if recebidas:
        agora = utcnow()
        for m in recebidas:
            m.lida_em = agora
        db.session.commit()
    ultima_lida = (conv.mensagens.filter(Mensagem.remetente_id == current_user.id, Mensagem.lida_em.isnot(None))
                   .order_by(Mensagem.id.desc()).first())
    return jsonify(mensagens=[serializar(m) for m in novas], lida_ate=ultima_lida.id if ultima_lida else 0)


@bp.route("/<int:cid>/enviar", methods=["POST"])
@login_required
def enviar(cid):
    conv = conversa_do_usuario(cid)
    outro = conv.outro(current_user.id)
    dados = request.get_json(silent=True) or request.form
    texto = (dados.get("texto") or "").strip()
    uuid = (dados.get("uuid") or "").strip().lower()
    quer_json = request.is_json

    def erro(msg, codigo=400):
        if quer_json:
            return jsonify(erro=msg), codigo
        flash(msg, "danger")
        return redirect(url_for("chat.index", c=cid))

    if uuid and not UUID_RE.match(uuid):
        return erro("Identificador de mensagem inválido.")
    if uuid:
        existente = Mensagem.query.filter_by(client_uuid=uuid).first()
        if existente:
            # Reenvio da mesma mensagem (ex.: conexão caiu antes da resposta): devolve a já salva.
            if existente.conversa_id != conv.id:
                return erro("Identificador de mensagem inválido.")
            return (jsonify(mensagem=serializar(existente), duplicada=True), 200) if quer_json else redirect(url_for("chat.index", c=cid))
    if not texto:
        return erro("Escreva uma mensagem.")
    if len(texto) > 4000:
        return erro("Mensagem muito longa (máximo de 4000 caracteres).")
    if not outro.ativo:
        return erro("Esta conta não está disponível.", 403)
    if bloqueado_entre(current_user.id, outro.id):
        return erro("Não é possível enviar mensagens nesta conversa (bloqueio ativo).", 403)
    msg = Mensagem(conversa_id=conv.id, remetente_id=current_user.id, texto=texto, client_uuid=uuid or None)
    ref = _referencia(_args_ref(dados), conv)
    if ref:
        setattr(msg, ref["campo"], ref["id"])
    db.session.add(msg)
    conv.ultima_mensagem_em = utcnow()
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        existente = Mensagem.query.filter_by(client_uuid=uuid).first()
        return (jsonify(mensagem=serializar(existente), duplicada=True), 200) if quer_json and existente else erro("Falha ao salvar.")
    notificar(outro.id, "mensagem", f"Nova mensagem de {current_user.primeiro_nome}", texto[:120], url_for("chat.index", c=conv.id),
              chave=f"msg:{conv.id}:{outro.id}:{conv.ultima_mensagem_em.strftime('%Y%m%d%H')}")
    db.session.commit()
    if quer_json:
        return jsonify(mensagem=serializar(msg)), 201
    return redirect(url_for("chat.index", c=cid))


class _ArgsRef(dict):
    def get(self, k, default=None, type=None):  # noqa: A002 - mesma assinatura do MultiDict
        v = super().get(k, default)
        if type and v is not None:
            try:
                return type(v)
            except (TypeError, ValueError):
                return default
        return v


def _args_ref(form):
    return _ArgsRef({k.replace("_id", ""): form.get(k) for k in ("anuncio_id", "solicitacao_id", "vaga_id") if form.get(k)})


@bp.route("/usuario/<int:user_id>/bloquear", methods=["POST"])
@login_required
def bloquear(user_id):
    if user_id == current_user.id:
        abort(400)
    b = Bloqueio.query.filter_by(user_id=current_user.id, bloqueado_id=user_id).first()
    if b:
        db.session.delete(b)
        flash("Usuário desbloqueado.", "info")
    else:
        if not db.session.get(User, user_id):
            abort(404)
        db.session.add(Bloqueio(user_id=current_user.id, bloqueado_id=user_id))
        flash("Usuário bloqueado. Ele não poderá enviar mensagens para você.", "info")
    db.session.commit()
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for("chat.index"))
