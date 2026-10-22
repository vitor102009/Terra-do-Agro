from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Notificacao, PropriedadeMembro, Tarefa, User, utcnow
from app.models.sistema import TIPOS_TAREFA
from app.services.alertas import gerar_alertas
from app.utils.acesso import membro_de
from app.utils.navegacao import destino_seguro, pagina
from app.utils.registro import auditar, notificar
from app.utils.validacao import Form, valores_de

bp = Blueprint("notificacoes", __name__)

ICONES = {"vacina": "bi-shield-plus", "estoque": "bi-box-seam", "vencimento": "bi-calendar-x", "conta_pagar": "bi-cash",
          "conta_receber": "bi-cash-coin", "tarefa": "bi-check2-square", "pesagem": "bi-speedometer", "servico": "bi-clipboard-check",
          "mensagem": "bi-chat-dots", "candidatura": "bi-briefcase", "acesso": "bi-key", "avaliacao": "bi-star",
          "comentario": "bi-chat", "reacao": "bi-hand-thumbs-up", "moderacao": "bi-shield-exclamation", "anuncio": "bi-megaphone",
          "suporte": "bi-life-preserver", "verificacao": "bi-patch-check", "admin": "bi-megaphone"}


@bp.route("/notificacoes")
@login_required
def index():
    gerar_alertas(current_user, forcar=request.args.get("atualizar") == "1")
    q = Notificacao.query.filter_by(user_id=current_user.id)
    if request.args.get("filtro") == "nao_lidas":
        q = q.filter_by(lida=False)
    pag = q.order_by(Notificacao.lida, Notificacao.created_at.desc()).paginate(page=pagina(), per_page=30, error_out=False)
    return render_template("notificacoes/index.html", pag=pag, icones=ICONES, filtro=request.args.get("filtro"))


@bp.route("/notificacoes/<int:nid>/abrir")
@login_required
def abrir(nid):
    n = Notificacao.query.filter_by(id=nid, user_id=current_user.id).first_or_404()
    n.lida = True
    db.session.commit()
    return redirect(destino_seguro(n.link, "notificacoes.index"))


@bp.route("/notificacoes/marcar-lidas", methods=["POST"])
@login_required
def marcar_lidas():
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    q = Notificacao.query.filter_by(user_id=current_user.id, lida=False)
    if ids:
        q = q.filter(Notificacao.id.in_(ids))
    q.update({"lida": True}, synchronize_session=False)
    db.session.commit()
    flash("Notificações marcadas como lidas.", "info")
    return redirect(url_for("notificacoes.index"))


# ------------------------------------------------------------------ Agenda

def _visiveis():
    cond = [Tarefa.user_id == current_user.id, Tarefa.responsavel_id == current_user.id]
    props = [mb.propriedade_id for mb in g.get("minhas_propriedades", [])]
    if props:
        cond.append(Tarefa.propriedade_id.in_(props))
    return Tarefa.query.filter(db.or_(*cond))


def _pode_editar(t):
    if t.user_id == current_user.id or t.responsavel_id == current_user.id:
        return True
    mb = membro_de(current_user.id, t.propriedade_id) if t.propriedade_id else None
    return bool(mb and "editar" in mb.perms)


def _responsaveis():
    """Pessoas com acesso à propriedade ativa (para atribuir responsáveis)."""
    if not g.get("propriedade"):
        return {current_user.id: current_user.nome}
    membros = PropriedadeMembro.query.filter_by(propriedade_id=g.propriedade.id).all()
    return {mb.user_id: mb.user.nome for mb in membros}


@bp.route("/agenda")
@login_required
def agenda():
    hoje = date.today()
    q = _visiveis()
    if request.args.get("tipo") in TIPOS_TAREFA:
        q = q.filter(Tarefa.tipo == request.args["tipo"])
    pendentes = q.filter(Tarefa.status == "pendente").order_by(Tarefa.data, Tarefa.hora).all()
    concluidas = q.filter(Tarefa.status != "pendente").order_by(Tarefa.data.desc()).limit(30).all()
    grupos = {"Atrasadas": [t for t in pendentes if t.data < hoje], "Hoje": [t for t in pendentes if t.data == hoje],
              "Próximos 7 dias": [t for t in pendentes if hoje < t.data <= hoje + timedelta(days=7)],
              "Mais adiante": [t for t in pendentes if t.data > hoje + timedelta(days=7)]}
    return render_template("notificacoes/agenda.html", grupos=grupos, concluidas=concluidas, tipos=TIPOS_TAREFA, args=request.args,
                           pode_editar=_pode_editar)


CAMPOS = ["tipo", "titulo", "descricao", "data", "hora", "responsavel_id"]


def _ler(form, resp):
    return {
        "tipo": form.choice("tipo", TIPOS_TAREFA),
        "titulo": form.text("titulo", required=True, max_len=150),
        "descricao": form.text("descricao", max_len=3000),
        "data": form.date("data", required=True),
        "hora": form.time("hora"),
        "responsavel_id": form.id_in("responsavel_id", set(resp)),
    }


@bp.route("/agenda/nova", methods=["GET", "POST"])
@login_required
def nova_tarefa():
    resp = _responsaveis()
    v = {"tipo": request.args.get("tipo", "tarefa"), "data": date.today().isoformat(), "responsavel_id": current_user.id}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, resp)
        vincular = form.boolean("vincular_propriedade") and g.get("propriedade")
        if form.ok:
            t = Tarefa(user_id=current_user.id, propriedade_id=g.propriedade.id if vincular else None, **dados)
            db.session.add(t)
            db.session.flush()
            if t.responsavel_id and t.responsavel_id != current_user.id:
                notificar(t.responsavel_id, "tarefa", f"Nova tarefa atribuída: {t.titulo}", f"Para {t.data.strftime('%d/%m/%Y')}",
                          url_for("notificacoes.agenda"))
            if t.propriedade_id:
                auditar("tarefa.criada", f"{TIPOS_TAREFA[t.tipo]} agendada: {t.titulo}", url_for("notificacoes.agenda"))
            db.session.commit()
            flash("Item adicionado à agenda.", "success")
            return redirect(url_for("notificacoes.agenda"))
        v, e = form.values, form.errors
    v.setdefault("vincular_propriedade", True)
    return render_template("notificacoes/tarefa.html", v=v, e=e, t=None, tipos=TIPOS_TAREFA, resp=resp)


@bp.route("/agenda/<int:tid>/editar", methods=["GET", "POST"])
@login_required
def editar_tarefa(tid):
    t = _visiveis().filter(Tarefa.id == tid).first_or_404()
    if not _pode_editar(t):
        abort(403)
    resp = _responsaveis()
    if t.responsavel_id:
        resp.setdefault(t.responsavel_id, db.session.get(User, t.responsavel_id).nome)
    v, e = valores_de(t, CAMPOS), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, resp)
        if form.ok:
            for k, val in dados.items():
                setattr(t, k, val)
            db.session.commit()
            flash("Agenda atualizada.", "success")
            return redirect(url_for("notificacoes.agenda"))
        v, e = form.values, form.errors
    return render_template("notificacoes/tarefa.html", v=v, e=e, t=t, tipos=TIPOS_TAREFA, resp=resp)


@bp.route("/agenda/<int:tid>/status", methods=["POST"])
@login_required
def status_tarefa(tid):
    t = _visiveis().filter(Tarefa.id == tid).first_or_404()
    if not _pode_editar(t):
        abort(403)
    novo = request.form.get("status")
    if novo not in ("pendente", "concluida", "cancelada"):
        abort(400)
    t.status = novo
    t.concluida_em = utcnow() if novo == "concluida" else None
    if t.propriedade_id and novo == "concluida":
        auditar("tarefa.concluida", f"Tarefa concluída: {t.titulo}", url_for("notificacoes.agenda"), propriedade_id=t.propriedade_id)
    db.session.commit()
    flash({"concluida": "Tarefa concluída.", "cancelada": "Tarefa cancelada.", "pendente": "Tarefa reaberta."}[novo], "success")
    return redirect(url_for("notificacoes.agenda"))
