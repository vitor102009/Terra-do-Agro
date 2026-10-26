from flask import Blueprint, abort, current_app, g, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ConversaIA, MensagemIA, utcnow
from app.services import ia_agro

bp = Blueprint("ia", __name__)

MAX_PERGUNTA = 2000


def _conversa(cid):
    conv = db.session.get(ConversaIA, cid)
    if conv is None or conv.user_id != current_user.id:
        abort(404)  # conversa de outro usuário: não revela que existe
    return conv


def _contexto():
    membro = g.get("membro")
    return ia_agro.Contexto(user_id=current_user.id, propriedade=g.get("propriedade"),
                            perms=set(membro.perms) if membro else set())


def _msg(m):
    return {"id": m.id, "papel": m.papel, "texto": m.conteudo, "erro": m.erro,
            "ferramentas": [f for f in (m.ferramentas or "").split(",") if f]}


@bp.route("/")
@bp.route("/conversa/<int:cid>")
@login_required
def index(cid=None):
    conversas = (ConversaIA.query.filter_by(user_id=current_user.id)
                 .order_by(ConversaIA.updated_at.desc()).limit(50).all())
    atual = _conversa(cid) if cid else None
    return render_template("ia/index.html", conversas=conversas, atual=atual,
                           mensagens=[_msg(m) for m in atual.mensagens] if atual else [],
                           configurada=ia_agro.configurada(), limite_dia=current_app.config["AI_DAILY_LIMIT"],
                           outra_propriedade=bool(atual and atual.propriedade_id and g.get("propriedade")
                                                  and atual.propriedade_id != g.propriedade.id))


@bp.route("/perguntar", methods=["POST"])
@login_required
def perguntar():
    dados = request.get_json(silent=True) or {}
    pergunta = (dados.get("pergunta") or "").strip()
    if not pergunta:
        return jsonify(erro="Escreva uma pergunta."), 400
    if len(pergunta) > MAX_PERGUNTA:
        return jsonify(erro=f"A pergunta deve ter no máximo {MAX_PERGUNTA} caracteres."), 400
    if not ia_agro.configurada():
        return jsonify(erro="A IA Agro ainda não foi configurada neste servidor."), 503
    prop = g.get("propriedade")
    cid = dados.get("conversa_id")
    if cid:
        try:
            conv = _conversa(int(cid))
        except (TypeError, ValueError):
            abort(404)
        # Não mistura propriedades: a conversa continua apenas na propriedade em que começou.
        if conv.propriedade_id and (not prop or conv.propriedade_id != prop.id):
            return jsonify(erro="Esta conversa é de outra propriedade. Inicie uma nova conversa."), 409
    else:
        conv = None
    try:
        ia_agro.verificar_limites(current_user.id)
    except ia_agro.LimiteExcedido as exc:
        return jsonify(erro=str(exc)), 429
    if conv is None:
        conv = ConversaIA(user_id=current_user.id, propriedade_id=prop.id if prop else None,
                          titulo=pergunta[:80] + ("…" if len(pergunta) > 80 else ""))
        db.session.add(conv)
        db.session.flush()
    nova = MensagemIA(conversa_id=conv.id, papel="user", conteudo=pergunta)
    db.session.add(nova)
    db.session.commit()  # a pergunta conta no limite mesmo que o provedor falhe

    ctx = _contexto()
    try:
        texto, usadas, uso = ia_agro.responder(conv, pergunta, ctx, pergunta_id=nova.id)
        resposta = MensagemIA(conversa_id=conv.id, papel="assistant", conteudo=texto,
                              ferramentas=",".join(dict.fromkeys(usadas))[:500],
                              tokens_entrada=uso["entrada"], tokens_saida=uso["saida"])
        status = 200
    except ia_agro.IAIndisponivel as exc:
        resposta = MensagemIA(conversa_id=conv.id, papel="assistant", conteudo=str(exc), erro=True)
        status = 502
    except Exception:  # noqa: BLE001 - registra no log, responde com mensagem genérica
        current_app.logger.exception("Falha inesperada na IA Agro")
        db.session.rollback()
        resposta = MensagemIA(conversa_id=conv.id, papel="assistant", erro=True,
                              conteudo="Ocorreu um erro inesperado. Tente novamente em instantes.")
        status = 500
    conv.updated_at = utcnow()
    db.session.add(resposta)
    db.session.commit()
    return jsonify(conversa_id=conv.id, url=url_for("ia.index", cid=conv.id), mensagem=_msg(resposta)), status


@bp.route("/conversa/<int:cid>/excluir", methods=["POST"])
@login_required
def excluir(cid):
    db.session.delete(_conversa(cid))
    db.session.commit()
    return redirect(url_for("ia.index"))
