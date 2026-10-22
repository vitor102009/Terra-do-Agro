from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Animal, EventoSanitario, Insumo, PerfilProfissional, SolicitacaoServico, User
from app.models.rebanho import TIPOS_SANITARIOS
from app.services import rebanho as svc
from app.services.estoque import EstoqueError, registrar_saida
from app.utils.acesso import obter_da_propriedade, propriedade_requerida
from app.utils.navegacao import pagina
from app.utils.registro import auditar
from app.utils.validacao import Form

bp = Blueprint("saude", __name__)


def resolver_pendentes(evento):
    """Uma nova aplicação do mesmo produto no mesmo alvo encerra a 'próxima dose' pendente anterior."""
    q = EventoSanitario.query.filter(
        EventoSanitario.propriedade_id == evento.propriedade_id,
        db.func.lower(EventoSanitario.produto) == evento.produto.lower(),
        EventoSanitario.proxima_resolvida.is_(False), EventoSanitario.proxima_data.isnot(None),
        EventoSanitario.data_aplicacao <= evento.data_aplicacao,
    )
    if evento.id:
        q = q.filter(EventoSanitario.id != evento.id)
    if evento.animal_id:
        q = q.filter(EventoSanitario.animal_id == evento.animal_id)
    elif evento.lote_id:
        q = q.filter(EventoSanitario.animal_id.is_(None), EventoSanitario.lote_id == evento.lote_id)
    else:
        q = q.filter(EventoSanitario.animal_id.is_(None), EventoSanitario.lote_id.is_(None))
    for anterior in q.all():
        anterior.proxima_resolvida = True


def profissionais_vinculados():
    """Profissionais com quem o usuário tem contratação registrada (para vincular atendimentos)."""
    return (User.query.join(PerfilProfissional, PerfilProfissional.user_id == User.id)
            .join(SolicitacaoServico, SolicitacaoServico.perfil_id == PerfilProfissional.id)
            .filter(SolicitacaoServico.cliente_id == current_user.id,
                    SolicitacaoServico.status.in_(("orcamento_aceito", "agendado", "em_andamento", "concluido")))
            .distinct().all())


@bp.route("/")
@propriedade_requerida("ver")
def index():
    pid = g.propriedade.id
    aba = request.args.get("aba", "historico")
    hoje = date.today()
    base = EventoSanitario.query.filter(EventoSanitario.propriedade_id == pid)
    ctx = {"aba": aba, "tipos": TIPOS_SANITARIOS, "hoje": hoje, "args": request.args}
    if aba == "agenda":
        pend = base.filter(EventoSanitario.proxima_data.isnot(None), EventoSanitario.proxima_resolvida.is_(False))
        ctx["vencidas"] = pend.filter(EventoSanitario.proxima_data < hoje).order_by(EventoSanitario.proxima_data).all()
        ctx["proximas"] = pend.filter(EventoSanitario.proxima_data >= hoje).order_by(EventoSanitario.proxima_data).all()
    else:
        q = base
        if aba == "consultas":
            q = q.filter(EventoSanitario.tipo.in_(("consulta", "exame")))
        if request.args.get("tipo") in TIPOS_SANITARIOS:
            q = q.filter(EventoSanitario.tipo == request.args["tipo"])
        if request.args.get("produto"):
            q = q.filter(EventoSanitario.produto.ilike(f"%{request.args['produto'].strip()}%"))
        if request.args.get("brinco"):
            q = q.join(Animal, EventoSanitario.animal_id == Animal.id).filter(Animal.brinco.ilike(f"%{request.args['brinco'].strip()}%"))
        ctx["pag"] = q.order_by(EventoSanitario.data_aplicacao.desc(), EventoSanitario.id.desc()).paginate(
            page=pagina(), per_page=30, error_out=False)
    ctx["n_vencidas"] = base.filter(EventoSanitario.proxima_data < hoje, EventoSanitario.proxima_resolvida.is_(False)).count()
    ctx["n_proximas"] = base.filter(EventoSanitario.proxima_data >= hoje, EventoSanitario.proxima_data <= hoje + timedelta(days=15),
                                    EventoSanitario.proxima_resolvida.is_(False)).count()
    return render_template("saude/index.html", **ctx)


@bp.route("/novo", methods=["GET", "POST"])
@propriedade_requerida("editar")
def novo():
    pid = g.propriedade.id
    animais = svc.animais_opcoes(pid)
    lotes = svc.lotes_da_propriedade(pid)
    insumos = Insumo.query.filter(Insumo.propriedade_id == pid, Insumo.ativo.is_(True),
                                  Insumo.categoria.in_(("vacina", "medicamento", "suplemento", "outro"))).order_by(Insumo.nome).all()
    profs = profissionais_vinculados()
    alvo_padrao = "animal" if request.args.get("animal") else ("lote" if request.args.get("lote") else "animal")
    v = {"data_aplicacao": date.today().isoformat(), "tipo": request.args.get("tipo", "vacina"), "alvo": alvo_padrao,
         "animal_id": request.args.get("animal", ""), "lote_id": request.args.get("lote", ""), "responsavel": current_user.nome}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        alvo = form.choice("alvo", {"animal": "", "lote": "", "rebanho": ""})
        dados = {
            "tipo": form.choice("tipo", TIPOS_SANITARIOS),
            "produto": form.text("produto", required=True, max_len=150),
            "lote_produto": form.text("lote_produto", max_len=60),
            "dose": form.text("dose", max_len=60),
            "data_aplicacao": form.date("data_aplicacao", required=True, not_future=True),
            "responsavel": form.text("responsavel", max_len=120),
            "observacoes": form.text("observacoes", max_len=4000),
            "profissional_id": form.id_in("profissional_id", {p.id for p in profs}),
            "animal_id": form.id_in("animal_id", {a.id for a in animais}, required=(alvo == "animal")) if alvo == "animal" else None,
            "lote_id": form.id_in("lote_id", {l.id for l in lotes}, required=(alvo == "lote")) if alvo == "lote" else None,
        }
        dados["proxima_data"] = form.date("proxima_data", min_date=dados["data_aplicacao"])
        insumo_id = form.id_in("insumo_id", {i.id for i in insumos})
        qtd_insumo = form.positive("quantidade_insumo", required=bool(insumo_id)) if insumo_id else None
        if form.ok:
            ev = EventoSanitario(propriedade_id=pid, registrado_por=current_user.id, insumo_id=insumo_id, **dados)
            db.session.add(ev)
            try:
                if insumo_id:
                    registrar_saida(db.session.get(Insumo, insumo_id), qtd_insumo, ev.data_aplicacao, current_user.id,
                                    lote_id=ev.lote_id, animal_id=ev.animal_id, atividade="sanidade",
                                    justificativa=f"{TIPOS_SANITARIOS[ev.tipo]}: {ev.produto}")
            except EstoqueError as exc:
                db.session.rollback()
                form.error("quantidade_insumo", str(exc))
            else:
                db.session.flush()
                resolver_pendentes(ev)
                link = url_for("animais.ficha", aid=ev.animal_id) if ev.animal_id else url_for("saude.index")
                auditar("sanitario.registrado", f"{TIPOS_SANITARIOS[ev.tipo]}: {ev.produto} · {ev.alvo}", link)
                db.session.commit()
                flash("Registro sanitário salvo.", "success")
                if request.form.get("continuar"):
                    return redirect(url_for("saude.novo", tipo=ev.tipo))
                return redirect(link)
        v, e = form.values, form.errors
    return render_template("saude/form.html", v=v, e=e, tipos=TIPOS_SANITARIOS, animais={a.id: a.identificacao for a in animais},
                           lotes={l.id: l.nome for l in lotes}, insumos={i.id: f"{i.nome} ({i.quantidade.normalize()} {i.unidade})" for i in insumos},
                           profs={p.id: p.nome for p in profs})


@bp.route("/<int:eid>/resolver", methods=["POST"])
@propriedade_requerida("editar")
def resolver(eid):
    ev = obter_da_propriedade(EventoSanitario, eid)
    ev.proxima_resolvida = True
    auditar("sanitario.agenda_encerrada", f"Próxima aplicação de {ev.produto} ({ev.alvo}) marcada como resolvida", url_for("saude.index", aba="agenda"))
    db.session.commit()
    flash("Pendência encerrada.", "info")
    return redirect(url_for("saude.index", aba="agenda"))


@bp.route("/<int:eid>")
@propriedade_requerida("ver")
def detalhe(eid):
    ev = obter_da_propriedade(EventoSanitario, eid)
    return render_template("saude/detalhe.html", ev=ev, tipos=TIPOS_SANITARIOS)


@bp.route("/<int:eid>/excluir", methods=["POST"])
@propriedade_requerida("editar")
def excluir(eid):
    ev = obter_da_propriedade(EventoSanitario, eid)
    descricao = f"Registro sanitário excluído: {ev.produto} em {ev.data_aplicacao.strftime('%d/%m/%Y')} ({ev.alvo})"
    db.session.delete(ev)
    auditar("sanitario.excluido", descricao)
    db.session.commit()
    flash("Registro excluído. A baixa de estoque, se houve, permanece registrada no módulo de insumos.", "info")
    return redirect(url_for("saude.index"))
