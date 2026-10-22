import csv
import io
from datetime import date, timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Animal, Lote, Pesagem
from app.services import rebanho as svc
from app.services.indicadores import gmd, gmd_medio_rebanho
from app.utils.acesso import obter_da_propriedade, propriedade_requerida
from app.utils.navegacao import pagina
from app.utils.registro import auditar
from app.utils.validacao import Form

bp = Blueprint("pesagens", __name__)

PESO_MAXIMO = 5000


def _periodo(args):
    try:
        inicio = date.fromisoformat(args.get("inicio")) if args.get("inicio") else date.today() - timedelta(days=365)
        fim = date.fromisoformat(args.get("fim")) if args.get("fim") else date.today()
    except ValueError:
        inicio, fim = date.today() - timedelta(days=365), date.today()
    return inicio, fim


def _consulta(pid, args):
    inicio, fim = _periodo(args)
    q = Pesagem.query.filter(Pesagem.propriedade_id == pid, Pesagem.data >= inicio, Pesagem.data <= fim)
    if args.get("brinco"):
        q = q.join(Animal, Pesagem.animal_id == Animal.id).filter(Animal.brinco.ilike(f"%{args['brinco'].strip()}%"))
    if args.get("lote", type=int):
        lid = args.get("lote", type=int)
        q = q.outerjoin(Animal, Pesagem.animal_id == Animal.id) if not args.get("brinco") else q
        q = q.filter(db.or_(Pesagem.lote_id == lid, Animal.lote_id == lid))
    if args.get("tipo") == "lote":
        q = q.filter(Pesagem.lote_id.isnot(None))
    elif args.get("tipo") == "individual":
        q = q.filter(Pesagem.animal_id.isnot(None))
    return q, inicio, fim


@bp.route("/")
@propriedade_requerida("ver")
def index():
    pid = g.propriedade.id
    q, inicio, fim = _consulta(pid, request.args)
    pag = q.order_by(Pesagem.data.desc(), Pesagem.id.desc()).paginate(page=pagina(), per_page=40, error_out=False)
    gmd_medio, n_animais = gmd_medio_rebanho(pid, inicio, fim)
    lotes = svc.lotes_da_propriedade(pid, False)
    # Ranking de desempenho no período (apenas animais com dados suficientes).
    por_animal = {}
    for p in (Pesagem.query.filter(Pesagem.propriedade_id == pid, Pesagem.animal_id.isnot(None), Pesagem.data >= inicio,
                                   Pesagem.data <= fim).order_by(Pesagem.data).all()):
        por_animal.setdefault(p.animal_id, []).append(p)
    ranking = []
    for aid, ps in por_animal.items():
        r = gmd(ps)
        if r:
            ranking.append((ps[0].animal, r))
    ranking.sort(key=lambda x: x[1]["gmd"], reverse=True)
    return render_template("pesagens/index.html", pag=pag, inicio=inicio, fim=fim, gmd_medio=gmd_medio, n_animais=n_animais,
                           lotes=lotes, ranking=ranking, args=request.args)


@bp.route("/exportar.csv")
@propriedade_requerida("ver")
def exportar():
    q, inicio, fim = _consulta(g.propriedade.id, request.args)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data", "Tipo", "Animal", "Lote", "Peso (kg)", "Qtd. animais", "Responsável", "Observações"])
    for p in q.order_by(Pesagem.data).all():
        w.writerow([p.data.strftime("%d/%m/%Y"), "Individual" if p.animal_id else "Lote (média)",
                    p.animal.identificacao if p.animal else "", (p.lote.nome if p.lote else (p.animal.lote.nome if p.animal and p.animal.lote else "")),
                    str(p.peso_kg).replace(".", ","), p.qtd_animais or "", p.responsavel or "", p.observacoes or ""])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=pesagens-{inicio}-{fim}.csv"})


@bp.route("/nova", methods=["GET", "POST"])
@propriedade_requerida("editar")
def nova():
    pid = g.propriedade.id
    modo = request.values.get("modo", "animal")
    if modo not in ("animal", "lote", "planilha"):
        modo = "animal"
    animais = svc.animais_opcoes(pid)
    lotes = svc.lotes_da_propriedade(pid)
    v = {"data": date.today().isoformat(), "responsavel": current_user.nome,
         "animal_id": request.args.get("animal", ""), "lote_id": request.args.get("lote", "")}
    e = {}
    lote_planilha = None
    if modo == "planilha" and request.values.get("lote_id", type=int):
        lote_planilha = Lote.query.filter_by(id=request.values.get("lote_id", type=int), propriedade_id=pid).first()

    if request.method == "POST":
        form = Form(request.form)
        data_p = form.date("data", required=True, not_future=True)
        responsavel = form.text("responsavel", max_len=120)
        obs = form.text("observacoes", max_len=2000)
        criadas = []
        if modo == "animal":
            aid = form.id_in("animal_id", {a.id for a in animais}, required=True)
            peso = form.positive("peso_kg", max_value=PESO_MAXIMO)
            animal = db.session.get(Animal, aid) if aid else None
            if animal and data_p and data_p < animal.data_entrada:
                form.error("data", "A data é anterior à entrada do animal na propriedade.")
            if form.ok:
                criadas.append(Pesagem(propriedade_id=pid, animal_id=aid, data=data_p, peso_kg=peso, responsavel=responsavel,
                                       observacoes=obs, registrado_por=current_user.id))
        elif modo == "lote":
            lid = form.id_in("lote_id", {l.id for l in lotes}, required=True)
            peso = form.positive("peso_kg", max_value=PESO_MAXIMO)
            qtd = form.integer("qtd_animais", required=True, min_value=1, max_value=100000)
            if form.ok:
                criadas.append(Pesagem(propriedade_id=pid, lote_id=lid, data=data_p, peso_kg=peso, qtd_animais=qtd,
                                       responsavel=responsavel, observacoes=obs, registrado_por=current_user.id))
        else:
            if not lote_planilha:
                abort(400)
            for a in lote_planilha.animais.filter_by(status="ativo").all():
                peso = form.positive(f"peso_{a.id}", required=False, max_value=PESO_MAXIMO)
                if peso:
                    criadas.append(Pesagem(propriedade_id=pid, animal_id=a.id, data=data_p, peso_kg=peso,
                                           responsavel=responsavel, observacoes=obs, registrado_por=current_user.id))
            if form.ok and not criadas:
                form.error("_geral", "Informe o peso de pelo menos um animal.")
        if form.ok and criadas:
            db.session.add_all(criadas)
            db.session.flush()
            for p in criadas:
                if p.animal_id:
                    svc.atualizar_peso_atual(db.session.get(Animal, p.animal_id))
            if len(criadas) == 1 and criadas[0].animal_id:
                alvo = db.session.get(Animal, criadas[0].animal_id)
                auditar("pesagem.registrada", f"Pesagem de {criadas[0].peso_kg} kg do animal {alvo.identificacao}",
                        url_for("animais.ficha", aid=alvo.id))
            elif criadas[0].lote_id:
                auditar("pesagem.registrada", f"Pesagem do lote {criadas[0].lote.nome if criadas[0].lote else ''}: média {criadas[0].peso_kg} kg",
                        url_for("lotes.detalhe", lid=criadas[0].lote_id))
            else:
                auditar("pesagem.registrada", f"{len(criadas)} pesagens registradas no lote {lote_planilha.nome}",
                        url_for("pesagens.index"))
            db.session.commit()
            flash(f"{len(criadas)} pesagem(ns) registrada(s).", "success")
            if request.form.get("continuar") and modo == "animal":
                return redirect(url_for("pesagens.nova", modo="animal"))
            if modo == "animal":
                return redirect(url_for("animais.ficha", aid=criadas[0].animal_id))
            return redirect(url_for("pesagens.index"))
        v, e = form.values, form.errors
    return render_template("pesagens/form.html", modo=modo, v=v, e=e, animais={a.id: a.identificacao for a in animais},
                           lotes={l.id: l.nome for l in lotes}, lote_planilha=lote_planilha,
                           animais_planilha=lote_planilha.animais.filter_by(status="ativo").order_by(Animal.brinco).all() if lote_planilha else [])


@bp.route("/<int:pesid>/excluir", methods=["POST"])
@propriedade_requerida("editar")
def excluir(pesid):
    p = obter_da_propriedade(Pesagem, pesid)
    animal = p.animal
    descricao = f"Pesagem de {p.peso_kg} kg em {p.data.strftime('%d/%m/%Y')} excluída" + (f" (animal {animal.identificacao})" if animal else "")
    db.session.delete(p)
    db.session.flush()
    if animal:
        svc.atualizar_peso_atual(animal)
    auditar("pesagem.excluida", descricao, url_for("animais.ficha", aid=animal.id) if animal else None)
    db.session.commit()
    flash("Pesagem excluída. A exclusão foi registrada no histórico de atividades.", "info")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for("pesagens.index"))
