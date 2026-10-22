from datetime import date, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.models import CategoriaFinanceira, Insumo
from app.models.financeiro import ATIVIDADES_CUSTO, CATEGORIAS_INSUMO, TIPOS_MOV_INSUMO, UNIDADES
from app.services import rebanho as svc
from app.services.estoque import EstoqueError, registrar_ajuste, registrar_entrada, registrar_saida
from app.utils.acesso import obter_da_propriedade, pode, propriedade_requerida
from app.utils.registro import auditar
from app.utils.validacao import Form, valores_de

bp = Blueprint("insumos", __name__)

# Categoria financeira sugerida para a despesa de compra, conforme a categoria do insumo.
CATEGORIA_DESPESA = {
    "racao": "racao", "suplemento": "racao", "medicamento": "medicamentos", "vacina": "medicamentos",
    "semente": "insumos_agricolas", "fertilizante": "insumos_agricolas", "defensivo": "insumos_agricolas",
    "combustivel": "combustivel", "material": "manutencao", "outro": "outras_despesas",
}


def categorias_despesa(pid):
    return (CategoriaFinanceira.query.filter(CategoriaFinanceira.tipo == "despesa",
                                             db.or_(CategoriaFinanceira.propriedade_id.is_(None), CategoriaFinanceira.propriedade_id == pid))
            .order_by(CategoriaFinanceira.nome).all())


@bp.route("/")
@propriedade_requerida("ver")
def index():
    pid = g.propriedade.id
    q = Insumo.query.filter_by(propriedade_id=pid, ativo=request.args.get("inativos") != "1")
    if request.args.get("categoria") in CATEGORIAS_INSUMO:
        q = q.filter_by(categoria=request.args["categoria"])
    if request.args.get("q"):
        q = q.filter(Insumo.nome.ilike(f"%{request.args['q'].strip()}%"))
    insumos = q.order_by(Insumo.nome).all()
    if request.args.get("alerta") == "baixo":
        insumos = [i for i in insumos if i.estoque_baixo]
    limite_validade = date.today() + timedelta(days=30)
    valor_estoque = sum(((i.quantidade or 0) * (i.custo_medio or 0) for i in insumos if i.quantidade > 0), 0)
    return render_template("insumos/index.html", insumos=insumos, categorias=CATEGORIAS_INSUMO, unidades=UNIDADES,
                           limite_validade=limite_validade, valor_estoque=valor_estoque, args=request.args, hoje=date.today())


def _ler(form, pid, insumo=None):
    dados = {
        "nome": form.text("nome", required=True, max_len=150),
        "categoria": form.choice("categoria", CATEGORIAS_INSUMO),
        "unidade": form.choice("unidade", UNIDADES),
        "quantidade_minima": form.decimal("quantidade_minima", min_value=0) or 0,
        "fornecedor": form.text("fornecedor", max_len=150),
        "validade": form.date("validade"),
        "local_armazenamento": form.text("local_armazenamento", max_len=120),
    }
    if insumo:
        dados["ativo"] = form.boolean("ativo")
    q = Insumo.query.filter(Insumo.propriedade_id == pid, db.func.lower(Insumo.nome) == (dados["nome"] or "").lower())
    if insumo:
        q = q.filter(Insumo.id != insumo.id)
    if dados["nome"] and q.first():
        form.error("nome", "Já existe um insumo com este nome.")
    return dados


@bp.route("/novo", methods=["GET", "POST"])
@propriedade_requerida("editar")
def novo():
    pid = g.propriedade.id
    cats = categorias_despesa(pid)
    v, e = {"unidade": "kg", "categoria": "racao", "data_entrada": date.today().isoformat()}, {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, pid)
        qtd = form.decimal("quantidade_inicial", min_value=0)
        custo = form.decimal("custo_unitario", min_value=0)
        data_ent = form.date("data_entrada", not_future=True) or date.today()
        gerar = pode("financeiro") and form.boolean("gerar_despesa")
        cat_id = form.id_in("categoria_financeira_id", {c.id for c in cats}, required=gerar) if gerar else None
        pago = form.boolean("pago")
        if gerar and (not qtd or not custo):
            form.error("custo_unitario", "Para lançar a despesa, informe a quantidade e o custo unitário.")
        if form.ok:
            ins = Insumo(propriedade_id=pid, quantidade=0, custo_medio=0, **dados)
            db.session.add(ins)
            db.session.flush()
            if qtd:
                registrar_entrada(ins, qtd, data_ent, current_user.id, custo_unitario=custo, fornecedor=dados["fornecedor"],
                                  validade=dados["validade"], gerar_despesa=gerar, categoria_id=cat_id, pago=pago,
                                  justificativa="Saldo inicial")
            auditar("insumo.cadastrado", f"Insumo {ins.nome} cadastrado", url_for("insumos.detalhe", iid=ins.id))
            db.session.commit()
            flash("Insumo cadastrado.", "success")
            return redirect(url_for("insumos.detalhe", iid=ins.id))
        v, e = form.values, form.errors
    return render_template("insumos/form.html", v=v, e=e, ins=None, categorias=CATEGORIAS_INSUMO, unidades=UNIDADES,
                           cats={c.id: c.nome for c in cats}, sugestao=CATEGORIA_DESPESA)


@bp.route("/<int:iid>/editar", methods=["GET", "POST"])
@propriedade_requerida("editar")
def editar(iid):
    ins = obter_da_propriedade(Insumo, iid)
    v, e = valores_de(ins, ["nome", "categoria", "unidade", "quantidade_minima", "fornecedor", "validade", "local_armazenamento", "ativo"]), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, g.propriedade.id, ins)
        if dados["unidade"] != ins.unidade and ins.movimentacoes:
            form.error("unidade", "A unidade não pode ser alterada depois que há movimentações.")
        if form.ok:
            for k, val in dados.items():
                setattr(ins, k, val)
            db.session.commit()
            flash("Insumo atualizado.", "success")
            return redirect(url_for("insumos.detalhe", iid=iid))
        v, e = form.values, form.errors
    return render_template("insumos/form.html", v=v, e=e, ins=ins, categorias=CATEGORIAS_INSUMO, unidades=UNIDADES, cats={}, sugestao={})


@bp.route("/<int:iid>")
@propriedade_requerida("ver")
def detalhe(iid):
    pid = g.propriedade.id
    ins = obter_da_propriedade(Insumo, iid)
    cats = categorias_despesa(pid)
    sug = next((c.id for c in cats if c.slug == CATEGORIA_DESPESA.get(ins.categoria)), "")
    v = {"data": date.today().isoformat(), "categoria_financeira_id": sug, "fornecedor": ins.fornecedor or ""}
    return render_template("insumos/detalhe.html", ins=ins, v=v, e={}, aba=request.args.get("aba", "entrada"),
                           tipos=TIPOS_MOV_INSUMO, cats={c.id: c.nome for c in cats},
                           lotes={l.id: l.nome for l in svc.lotes_da_propriedade(pid)}, atividades=ATIVIDADES_CUSTO)


@bp.route("/<int:iid>/movimentar/<tipo>", methods=["POST"])
@propriedade_requerida("editar")
def movimentar(iid, tipo):
    pid = g.propriedade.id
    if tipo not in TIPOS_MOV_INSUMO:
        abort(404)
    ins = obter_da_propriedade(Insumo, iid)
    form = Form(request.form)
    data_mov = form.date("data", required=True, not_future=True)
    cats = categorias_despesa(pid)
    try:
        if form.ok:
            if tipo == "entrada":
                qtd = form.positive("quantidade")
                custo = form.decimal("custo_unitario", min_value=0)
                gerar = pode("financeiro") and form.boolean("gerar_despesa")
                cat_id = form.id_in("categoria_financeira_id", {c.id for c in cats}, required=gerar) if gerar else None
                if form.ok:
                    registrar_entrada(ins, qtd, data_mov, current_user.id, custo_unitario=custo,
                                      fornecedor=form.text("fornecedor", max_len=150), validade=form.date("validade"),
                                      gerar_despesa=gerar, categoria_id=cat_id, pago=form.boolean("pago"),
                                      vencimento=form.date("vencimento"), justificativa=form.text("justificativa", max_len=500))
                    desc = f"Entrada de {qtd.normalize()} {ins.unidade} de {ins.nome}"
            elif tipo == "saida":
                qtd = form.positive("quantidade")
                lote_id = form.id_in("lote_id", {l.id for l in svc.lotes_da_propriedade(pid)})
                atividade = form.choice("atividade", ATIVIDADES_CUSTO, required=False)
                if form.ok:
                    registrar_saida(ins, qtd, data_mov, current_user.id, lote_id=lote_id, atividade=atividade or None,
                                    justificativa=form.text("justificativa", max_len=500))
                    desc = f"Saída de {qtd.normalize()} {ins.unidade} de {ins.nome}"
            else:
                contada = form.decimal("quantidade_contada", required=True, min_value=0)
                just = form.text("justificativa", required=True, max_len=500)
                if form.ok:
                    registrar_ajuste(ins, contada, data_mov, current_user.id, just)
                    desc = f"Ajuste de inventário de {ins.nome}: {contada.normalize()} {ins.unidade}"
    except EstoqueError as exc:
        db.session.rollback()
        form.error("quantidade" if tipo != "ajuste" else "quantidade_contada", str(exc))
    if not form.ok:
        db.session.rollback()
        ins = obter_da_propriedade(Insumo, iid)
        return render_template("insumos/detalhe.html", ins=ins, v=form.values, e=form.errors, aba=tipo, tipos=TIPOS_MOV_INSUMO,
                               cats={c.id: c.nome for c in cats}, lotes={l.id: l.nome for l in svc.lotes_da_propriedade(pid)},
                               atividades=ATIVIDADES_CUSTO), 400
    auditar("insumo.movimentado", desc, url_for("insumos.detalhe", iid=iid))
    db.session.commit()
    flash("Movimentação registrada.", "success")
    return redirect(url_for("insumos.detalhe", iid=iid, aba=tipo))
