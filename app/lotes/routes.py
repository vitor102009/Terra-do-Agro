from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Animal, Lancamento, Lote, ManejoPastagem, MovimentacaoAnimal, MovimentacaoInsumo, Pastagem, Pesagem
from app.models.rebanho import CONDICOES_PASTAGEM
from app.utils.acesso import obter_da_propriedade, pode, propriedade_requerida
from app.utils.registro import auditar
from app.utils.validacao import Form, valores_de

bp = Blueprint("lotes", __name__)

# 1 UA (unidade animal) = 450 kg de peso vivo, referência usual em manejo de pastagens.
PESO_UA = Decimal("450")

TIPOS_MANEJO = {
    "avaliacao": "Avaliação de pasto",
    "entrada_lote": "Entrada de lote",
    "saida_lote": "Saída de lote / vedação",
    "adubacao": "Adubação / correção",
    "rocada": "Roçada / limpeza",
    "reforma": "Reforma",
    "outro": "Outro",
}


def lotacao(animais, area_ha):
    """Calcula a lotação (UA/ha) apenas quando todos os animais têm peso e a área é conhecida."""
    if not animais:
        return {"ok": False, "motivo": "Sem animais"}
    if not area_ha or area_ha <= 0:
        return {"ok": False, "motivo": "Área da pastagem não informada"}
    sem_peso = [a for a in animais if a.peso_atual is None]
    if sem_peso:
        return {"ok": False, "motivo": f"{len(sem_peso)} animal(is) sem pesagem"}
    peso_total = sum((a.peso_atual for a in animais), Decimal("0"))
    ua = peso_total / PESO_UA
    return {"ok": True, "ua": ua, "ua_ha": ua / Decimal(area_ha), "peso_total": peso_total}


@bp.route("/")
@propriedade_requerida("ver")
def index():
    pid = g.propriedade.id
    lotes = Lote.query.filter_by(propriedade_id=pid).order_by(Lote.ativo.desc(), Lote.nome).all()
    resumo = {}
    for l in lotes:
        ativos = l.animais.filter_by(status="ativo").all()
        pesos = [a.peso_atual for a in ativos if a.peso_atual is not None]
        resumo[l.id] = {"qtd": len(ativos), "peso_medio": (sum(pesos) / len(pesos)) if pesos else None,
                        "sem_peso": len(ativos) - len(pesos)}
    pastagens = Pastagem.query.filter_by(propriedade_id=pid).order_by(Pastagem.ativa.desc(), Pastagem.nome).all()
    lot_past = {}
    for p in pastagens:
        animais = [a for l in p.lotes if l.ativo for a in l.animais.filter_by(status="ativo").all()]
        lot_past[p.id] = lotacao(animais, p.area_ha)
    sem_lote = Animal.query.filter_by(propriedade_id=pid, status="ativo", lote_id=None).count()
    return render_template("lotes/index.html", lotes=lotes, resumo=resumo, pastagens=pastagens, lot_past=lot_past,
                           sem_lote=sem_lote, condicoes=CONDICOES_PASTAGEM, aba=request.args.get("aba", "lotes"))


def _ler_lote(form, pid, lote=None):
    pastagens = {p.id for p in Pastagem.query.filter_by(propriedade_id=pid).all()}
    dados = {
        "nome": form.text("nome", required=True, max_len=100),
        "finalidade": form.text("finalidade", max_len=60),
        "descricao": form.text("descricao", max_len=2000),
        "pastagem_id": form.id_in("pastagem_id", pastagens),
        "ativo": form.boolean("ativo") if lote else True,
    }
    q = Lote.query.filter(Lote.propriedade_id == pid, db.func.lower(Lote.nome) == (dados["nome"] or "").lower())
    if lote:
        q = q.filter(Lote.id != lote.id)
    if dados["nome"] and q.first():
        form.error("nome", "Já existe um lote com este nome.")
    if lote and not dados["ativo"] and lote.animais.filter_by(status="ativo").count():
        form.error("ativo", "Transfira os animais ativos antes de desativar o lote.")
    return dados


@bp.route("/novo", methods=["GET", "POST"])
@propriedade_requerida("editar")
def novo():
    pid = g.propriedade.id
    v, e = {}, {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler_lote(form, pid)
        if form.ok:
            lote = Lote(propriedade_id=pid, **dados)
            db.session.add(lote)
            db.session.flush()
            auditar("lote.criado", f"Lote {lote.nome} criado", url_for("lotes.detalhe", lid=lote.id))
            db.session.commit()
            flash("Lote criado.", "success")
            return redirect(url_for("lotes.detalhe", lid=lote.id))
        v, e = form.values, form.errors
    pastagens = {p.id: p.nome for p in Pastagem.query.filter_by(propriedade_id=pid, ativa=True).order_by(Pastagem.nome)}
    return render_template("lotes/form_lote.html", v=v, e=e, lote=None, pastagens=pastagens)


@bp.route("/<int:lid>/editar", methods=["GET", "POST"])
@propriedade_requerida("editar")
def editar(lid):
    pid = g.propriedade.id
    lote = obter_da_propriedade(Lote, lid)
    v, e = valores_de(lote, ["nome", "finalidade", "descricao", "pastagem_id", "ativo"]), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler_lote(form, pid, lote)
        if form.ok:
            for k, val in dados.items():
                setattr(lote, k, val)
            auditar("lote.editado", f"Lote {lote.nome} atualizado", url_for("lotes.detalhe", lid=lid))
            db.session.commit()
            flash("Lote atualizado.", "success")
            return redirect(url_for("lotes.detalhe", lid=lid))
        v, e = form.values, form.errors
    pastagens = {p.id: p.nome for p in Pastagem.query.filter_by(propriedade_id=pid).order_by(Pastagem.nome)}
    return render_template("lotes/form_lote.html", v=v, e=e, lote=lote, pastagens=pastagens)


@bp.route("/<int:lid>")
@propriedade_requerida("ver")
def detalhe(lid):
    pid = g.propriedade.id
    lote = obter_da_propriedade(Lote, lid)
    animais = lote.animais.filter_by(status="ativo").order_by(Animal.brinco).all()
    outros_lotes = Lote.query.filter(Lote.propriedade_id == pid, Lote.id != lid, Lote.ativo.is_(True)).order_by(Lote.nome).all()
    sem_lote = Animal.query.filter_by(propriedade_id=pid, status="ativo", lote_id=None).order_by(Animal.brinco).all()
    pesagens_lote = Pesagem.query.filter_by(lote_id=lid).order_by(Pesagem.data.desc()).limit(20).all()
    custos, consumo = [], []
    if pode("financeiro"):
        custos = Lancamento.query.filter_by(lote_id=lid, tipo="despesa", cancelado=False).order_by(Lancamento.data.desc()).limit(30).all()
    consumo = MovimentacaoInsumo.query.filter_by(lote_id=lid, tipo="saida").order_by(MovimentacaoInsumo.data.desc()).limit(30).all()
    lot = lotacao(animais, lote.pastagem.area_ha) if lote.pastagem else None
    pesos = [a.peso_atual for a in animais if a.peso_atual is not None]
    return render_template("lotes/detalhe.html", lote=lote, animais=animais, outros_lotes=outros_lotes, sem_lote=sem_lote,
                           pesagens_lote=pesagens_lote, custos=custos, consumo=consumo, lot=lot,
                           peso_medio=(sum(pesos) / len(pesos)) if pesos else None)


@bp.route("/<int:lid>/transferir", methods=["POST"])
@propriedade_requerida("editar")
def transferir(lid):
    """Transfere animais selecionados para outro lote (ou adiciona animais sem lote a este lote)."""
    pid = g.propriedade.id
    lote = obter_da_propriedade(Lote, lid)
    ids = [int(i) for i in request.form.getlist("animais") if i.isdigit()]
    destino_id = request.form.get("destino", type=int)
    if request.form.get("acao") == "adicionar":
        destino = lote
    else:
        destino = Lote.query.filter_by(id=destino_id, propriedade_id=pid, ativo=True).first() if destino_id else None
        if destino_id and not destino:
            flash("Lote de destino inválido.", "danger")
            return redirect(url_for("lotes.detalhe", lid=lid))
    if not ids:
        flash("Selecione ao menos um animal.", "warning")
        return redirect(url_for("lotes.detalhe", lid=lid))
    animais = Animal.query.filter(Animal.id.in_(ids), Animal.propriedade_id == pid, Animal.status == "ativo").all()
    hoje = date.today()
    for a in animais:
        if a.lote_id == (destino.id if destino else None):
            continue
        db.session.add(MovimentacaoAnimal(animal=a, tipo="troca_lote", data=hoje, lote_origem_id=a.lote_id,
                                          lote_destino_id=destino.id if destino else None, registrado_por=current_user.id))
        a.lote_id = destino.id if destino else None
    nome_destino = destino.nome if destino else "sem lote"
    auditar("lote.transferencia", f"{len(animais)} animal(is) movidos para {nome_destino}", url_for("lotes.detalhe", lid=lid))
    db.session.commit()
    flash(f"{len(animais)} animal(is) movidos para {nome_destino}.", "success")
    return redirect(url_for("lotes.detalhe", lid=lid))


# ------------------------------------------------------------------ Pastagens

def _ler_pastagem(form, pid, past=None):
    dados = {
        "nome": form.text("nome", required=True, max_len=100),
        "area_ha": form.decimal("area_ha", min_value=0, max_value=1_000_000),
        "forrageira": form.text("forrageira", max_len=100),
        "condicao": form.choice("condicao", CONDICOES_PASTAGEM, required=False),
        "disponibilidade": form.text("disponibilidade", max_len=2000),
        "observacoes": form.text("observacoes", max_len=4000),
        "ativa": form.boolean("ativa") if past else True,
    }
    q = Pastagem.query.filter(Pastagem.propriedade_id == pid, db.func.lower(Pastagem.nome) == (dados["nome"] or "").lower())
    if past:
        q = q.filter(Pastagem.id != past.id)
    if dados["nome"] and q.first():
        form.error("nome", "Já existe uma pastagem com este nome.")
    return dados


@bp.route("/pastagens/nova", methods=["GET", "POST"])
@propriedade_requerida("editar")
def nova_pastagem():
    pid = g.propriedade.id
    v, e = {}, {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler_pastagem(form, pid)
        if form.ok:
            past = Pastagem(propriedade_id=pid, **dados)
            db.session.add(past)
            db.session.flush()
            auditar("lote.pastagem_criada", f"Pastagem {past.nome} cadastrada", url_for("lotes.pastagem", pid_=past.id))
            db.session.commit()
            flash("Pastagem cadastrada.", "success")
            return redirect(url_for("lotes.pastagem", pid_=past.id))
        v, e = form.values, form.errors
    return render_template("lotes/form_pastagem.html", v=v, e=e, past=None, condicoes=CONDICOES_PASTAGEM)


@bp.route("/pastagens/<int:pid_>/editar", methods=["GET", "POST"])
@propriedade_requerida("editar")
def editar_pastagem(pid_):
    past = obter_da_propriedade(Pastagem, pid_)
    v, e = valores_de(past, ["nome", "area_ha", "forrageira", "condicao", "disponibilidade", "observacoes", "ativa"]), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler_pastagem(form, g.propriedade.id, past)
        if form.ok:
            for k, val in dados.items():
                setattr(past, k, val)
            db.session.commit()
            flash("Pastagem atualizada.", "success")
            return redirect(url_for("lotes.pastagem", pid_=pid_))
        v, e = form.values, form.errors
    return render_template("lotes/form_pastagem.html", v=v, e=e, past=past, condicoes=CONDICOES_PASTAGEM)


@bp.route("/pastagens/<int:pid_>", methods=["GET", "POST"])
@propriedade_requerida("ver")
def pastagem(pid_):
    past = obter_da_propriedade(Pastagem, pid_)
    v, e = {"data": date.today().isoformat(), "tipo": "avaliacao"}, {}
    if request.method == "POST":
        if not pode("editar"):
            from flask import abort

            abort(403)
        form = Form(request.form)
        dados = {
            "data": form.date("data", required=True, not_future=True),
            "tipo": form.choice("tipo", TIPOS_MANEJO),
            "condicao": form.choice("condicao", CONDICOES_PASTAGEM, required=False),
            "altura_cm": form.decimal("altura_cm", min_value=0, max_value=500),
            "observacao": form.text("observacao", max_len=2000),
        }
        if form.ok:
            db.session.add(ManejoPastagem(pastagem_id=past.id, registrado_por=current_user.id, **dados))
            if dados["condicao"]:
                past.condicao = dados["condicao"]
            auditar("lote.manejo_pastagem", f"Manejo registrado na pastagem {past.nome}", url_for("lotes.pastagem", pid_=past.id))
            db.session.commit()
            flash("Manejo registrado.", "success")
            return redirect(url_for("lotes.pastagem", pid_=past.id))
        v, e = form.values, form.errors
    animais = [a for l in past.lotes if l.ativo for a in l.animais.filter_by(status="ativo").all()]
    return render_template("lotes/pastagem.html", past=past, v=v, e=e, tipos=TIPOS_MANEJO, condicoes=CONDICOES_PASTAGEM,
                           lot=lotacao(animais, past.area_ha), qtd_animais=len(animais))
