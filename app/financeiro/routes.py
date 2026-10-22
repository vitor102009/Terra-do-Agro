import csv
import io
from datetime import date
from decimal import Decimal

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from app.extensions import db
from app.models import CategoriaFinanceira, Lancamento, Pagamento
from app.models.financeiro import ATIVIDADES_CUSTO, FORMAS_PAGAMENTO
from app.services import indicadores as ind
from app.services import rebanho as svc
from app.utils.acesso import obter_da_propriedade, propriedade_requerida
from app.utils.navegacao import pagina
from app.utils.registro import auditar
from app.utils.uploads import UploadError, caminho_absoluto, salvar_upload, tem_arquivo
from app.utils.validacao import Form, valores_de

bp = Blueprint("financeiro", __name__)

TIPOS = {"receita": "Receita", "despesa": "Despesa"}
SITUACOES = {"pendente": "Pendente", "parcial": "Parcial", "quitado": "Pago / recebido", "cancelado": "Cancelado"}


def categorias(pid, tipo=None):
    q = CategoriaFinanceira.query.filter(db.or_(CategoriaFinanceira.propriedade_id.is_(None), CategoriaFinanceira.propriedade_id == pid))
    if tipo:
        q = q.filter(CategoriaFinanceira.tipo == tipo)
    return q.order_by(CategoriaFinanceira.tipo, CategoriaFinanceira.nome).all()


def _datas(args):
    try:
        inicio = date.fromisoformat(args["inicio"]) if args.get("inicio") else None
        fim = date.fromisoformat(args["fim"]) if args.get("fim") else None
    except ValueError:
        inicio = fim = None
    if not inicio and not fim:
        inicio, fim = ind.periodo("ano")
    return inicio, fim


def _consulta(pid, args):
    inicio, fim = _datas(args)
    q = Lancamento.query.filter(Lancamento.propriedade_id == pid)
    if inicio:
        q = q.filter(Lancamento.data >= inicio)
    if fim:
        q = q.filter(Lancamento.data <= fim)
    if args.get("tipo") in TIPOS:
        q = q.filter(Lancamento.tipo == args["tipo"])
    if args.get("categoria", type=int):
        q = q.filter(Lancamento.categoria_id == args.get("categoria", type=int))
    if args.get("q"):
        termo = f"%{args['q'].strip()}%"
        q = q.filter(db.or_(Lancamento.descricao.ilike(termo), Lancamento.contraparte.ilike(termo)))
    if args.get("cancelados") != "1":
        q = q.filter(Lancamento.cancelado.is_(False))
    return q, inicio, fim


@bp.route("/")
@propriedade_requerida("financeiro")
def index():
    pid = g.propriedade.id
    q, inicio, fim = _consulta(pid, request.args)
    todos = q.order_by(Lancamento.data.desc(), Lancamento.id.desc()).all()
    situacao = request.args.get("situacao")
    if situacao in SITUACOES:
        todos = [l for l in todos if l.situacao == situacao]
    ativos = [l for l in todos if not l.cancelado]
    tot_rec = sum((l.valor for l in ativos if l.tipo == "receita"), Decimal("0"))
    tot_desp = sum((l.valor for l in ativos if l.tipo == "despesa"), Decimal("0"))
    por_pagina = 40
    pg = pagina()
    paginas = max(1, (len(todos) + por_pagina - 1) // por_pagina)
    itens = todos[(pg - 1) * por_pagina: pg * por_pagina]
    a_pagar, total_pagar = ind.em_aberto(pid, "despesa")
    a_receber, total_receber = ind.em_aberto(pid, "receita")
    return render_template("financeiro/index.html", itens=itens, total=len(todos), pg=pg, paginas=paginas,
                           tot_rec=tot_rec, tot_desp=tot_desp, inicio=inicio, fim=fim, args=request.args,
                           categorias=categorias(pid), tipos=TIPOS, situacoes=SITUACOES,
                           total_pagar=total_pagar, total_receber=total_receber, n_pagar=len(a_pagar), n_receber=len(a_receber))


@bp.route("/contas/<tipo>")
@propriedade_requerida("financeiro")
def contas(tipo):
    if tipo not in ("pagar", "receber"):
        abort(404)
    itens, total = ind.em_aberto(g.propriedade.id, "despesa" if tipo == "pagar" else "receita")
    return render_template("financeiro/contas.html", tipo=tipo, itens=itens, total=total, hoje=date.today())


@bp.route("/exportar.csv")
@propriedade_requerida("financeiro")
def exportar():
    q, inicio, fim = _consulta(g.propriedade.id, request.args)
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Data (competência)", "Tipo", "Categoria", "Descrição", "Valor", "Pago/recebido", "Saldo", "Situação",
                "Vencimento", "Cliente/fornecedor", "Lote", "Animal", "Atividade", "Origem"])
    for l in q.order_by(Lancamento.data).all():
        w.writerow([l.data.strftime("%d/%m/%Y"), TIPOS[l.tipo], l.categoria.nome, l.descricao, str(l.valor).replace(".", ","),
                    str(l.valor_pago).replace(".", ","), str(l.saldo).replace(".", ","), l.situacao_label,
                    l.vencimento.strftime("%d/%m/%Y") if l.vencimento else "", l.contraparte or "",
                    l.lote.nome if l.lote else "", l.animal.identificacao if l.animal else "",
                    ATIVIDADES_CUSTO.get(l.atividade or "", ""), l.origem])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=financeiro-{inicio or ''}-{fim or ''}.csv"})


CAMPOS = ["descricao", "categoria_id", "valor", "data", "vencimento", "forma", "contraparte", "observacoes", "animal_id",
          "lote_id", "atividade"]


def _ler(form, pid, tipo, lanc=None):
    cats = {c.id for c in categorias(pid, tipo)}
    dados = {
        "descricao": form.text("descricao", required=True, max_len=200),
        "categoria_id": form.id_in("categoria_id", cats, required=True),
        "valor": form.positive("valor", max_value=Decimal("999999999999")),
        "data": form.date("data", required=True),
        "vencimento": form.date("vencimento"),
        "forma": form.choice("forma", FORMAS_PAGAMENTO, required=False),
        "contraparte": form.text("contraparte", max_len=150),
        "observacoes": form.text("observacoes", max_len=4000),
        "animal_id": form.id_in("animal_id", {a.id for a in svc.animais_opcoes(pid)} | ({lanc.animal_id} if lanc and lanc.animal_id else set())),
        "lote_id": form.id_in("lote_id", {l.id for l in svc.lotes_da_propriedade(pid, False)}),
        "atividade": form.choice("atividade", ATIVIDADES_CUSTO, required=False) or None,
    }
    if lanc and dados["valor"] is not None and dados["valor"] < lanc.valor_pago:
        form.error("valor", "O valor não pode ser menor que o total já pago/recebido.")
    return dados


def _anexo(form, lanc):
    arq = request.files.get("anexo")
    if tem_arquivo(arq):
        try:
            lanc.anexo = salvar_upload(arq, "documento", "financeiro", privado=True)
        except UploadError as exc:
            form.error("anexo", str(exc))


def _ctx_form(pid, tipo, lanc=None):
    return dict(tipo=tipo, lanc=lanc, cats={c.id: c.nome for c in categorias(pid, tipo)}, formas=FORMAS_PAGAMENTO,
                atividades=ATIVIDADES_CUSTO, lotes={l.id: l.nome for l in svc.lotes_da_propriedade(pid, False)},
                animais={a.id: a.identificacao for a in svc.animais_opcoes(pid)})


@bp.route("/novo/<tipo>", methods=["GET", "POST"])
@propriedade_requerida("financeiro")
def novo(tipo):
    if tipo not in TIPOS:
        abort(404)
    pid = g.propriedade.id
    v, e = {"data": date.today().isoformat(), "situacao_inicial": "pendente", "lote_id": request.args.get("lote", ""),
            "animal_id": request.args.get("animal", ""), "descricao": request.args.get("descricao", ""),
            "valor": request.args.get("valor", "")}, {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, pid, tipo)
        situacao = form.choice("situacao_inicial", {"pendente": "", "quitado": "", "parcial": ""})
        valor_parcial = form.positive("valor_parcial") if situacao == "parcial" else None
        data_pag = form.date("data_pagamento", not_future=True) if situacao in ("quitado", "parcial") else None
        if situacao == "parcial" and valor_parcial and dados["valor"] and valor_parcial >= dados["valor"]:
            form.error("valor_parcial", "Para pagamento parcial, informe um valor menor que o total.")
        lanc = Lancamento(propriedade_id=pid, tipo=tipo, registrado_por=current_user.id, origem="manual")
        if form.ok:
            _anexo(form, lanc)
        if form.ok:
            for k, val in dados.items():
                setattr(lanc, k, val)
            if situacao == "quitado":
                lanc.pagamentos.append(Pagamento(data=data_pag or dados["data"], valor=dados["valor"], forma=dados["forma"],
                                                 registrado_por=current_user.id))
            elif situacao == "parcial":
                lanc.pagamentos.append(Pagamento(data=data_pag or dados["data"], valor=valor_parcial, forma=dados["forma"],
                                                 registrado_por=current_user.id))
            db.session.add(lanc)
            db.session.flush()
            auditar(f"financeiro.{tipo}", f"{TIPOS[tipo]} lançada: {lanc.descricao}", url_for("financeiro.detalhe", lid=lanc.id))
            db.session.commit()
            flash(f"{TIPOS[tipo]} registrada.", "success")
            if request.form.get("continuar"):
                return redirect(url_for("financeiro.novo", tipo=tipo))
            return redirect(url_for("financeiro.detalhe", lid=lanc.id))
        v, e = form.values, form.errors
    return render_template("financeiro/form.html", v=v, e=e, **_ctx_form(pid, tipo))


@bp.route("/<int:lid>/editar", methods=["GET", "POST"])
@propriedade_requerida("financeiro")
def editar(lid):
    pid = g.propriedade.id
    lanc = obter_da_propriedade(Lancamento, lid)
    if lanc.cancelado:
        flash("Lançamentos cancelados não podem ser editados.", "warning")
        return redirect(url_for("financeiro.detalhe", lid=lid))
    v, e = valores_de(lanc, CAMPOS), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, pid, lanc.tipo, lanc)
        if form.ok:
            _anexo(form, lanc)
        if form.ok:
            for k, val in dados.items():
                setattr(lanc, k, val)
            auditar("financeiro.editado", f"Lançamento editado: {lanc.descricao}", url_for("financeiro.detalhe", lid=lid))
            db.session.commit()
            flash("Lançamento atualizado.", "success")
            return redirect(url_for("financeiro.detalhe", lid=lid))
        v, e = form.values, form.errors
    return render_template("financeiro/form.html", v=v, e=e, **_ctx_form(pid, lanc.tipo, lanc))


@bp.route("/<int:lid>", methods=["GET", "POST"])
@propriedade_requerida("financeiro")
def detalhe(lid):
    lanc = obter_da_propriedade(Lancamento, lid)
    v, e = {"data": date.today().isoformat(), "valor": str(lanc.saldo) if lanc.saldo > 0 else "", "forma": lanc.forma or ""}, {}
    if request.method == "POST":
        if lanc.cancelado or lanc.saldo <= 0:
            abort(400)
        form = Form(request.form)
        valor = form.positive("valor")
        data_pag = form.date("data", required=True, not_future=True)
        forma = form.choice("forma", FORMAS_PAGAMENTO, required=False)
        if valor and valor > lanc.saldo:
            form.error("valor", f"O valor excede o saldo em aberto ({lanc.saldo}).")
        if form.ok:
            db.session.add(Pagamento(lancamento=lanc, data=data_pag, valor=valor, forma=forma, registrado_por=current_user.id))
            acao = "Recebimento" if lanc.tipo == "receita" else "Pagamento"
            auditar("financeiro.pagamento", f"{acao} de R$ {valor} em {lanc.descricao}", url_for("financeiro.detalhe", lid=lid))
            db.session.commit()
            flash(f"{acao} registrado.", "success")
            return redirect(url_for("financeiro.detalhe", lid=lid))
        v, e = form.values, form.errors
    return render_template("financeiro/detalhe.html", l=lanc, v=v, e=e, formas=FORMAS_PAGAMENTO, atividades=ATIVIDADES_CUSTO, tipos=TIPOS)


@bp.route("/<int:lid>/pagamentos/<int:pgid>/estornar", methods=["POST"])
@propriedade_requerida("financeiro")
def estornar(lid, pgid):
    lanc = obter_da_propriedade(Lancamento, lid)
    pag = Pagamento.query.filter_by(id=pgid, lancamento_id=lanc.id).first_or_404()
    auditar("financeiro.estorno", f"Estorno de R$ {pag.valor} ({pag.data.strftime('%d/%m/%Y')}) em {lanc.descricao}",
            url_for("financeiro.detalhe", lid=lid))
    db.session.delete(pag)
    db.session.commit()
    flash("Pagamento estornado.", "info")
    return redirect(url_for("financeiro.detalhe", lid=lid))


@bp.route("/<int:lid>/cancelar", methods=["POST"])
@propriedade_requerida("financeiro")
def cancelar(lid):
    lanc = obter_da_propriedade(Lancamento, lid)
    if lanc.pagamentos:
        flash("Estorne os pagamentos antes de cancelar o lançamento.", "warning")
        return redirect(url_for("financeiro.detalhe", lid=lid))
    lanc.cancelado = True
    auditar("financeiro.cancelado", f"Lançamento cancelado: {lanc.descricao}", url_for("financeiro.detalhe", lid=lid))
    db.session.commit()
    flash("Lançamento cancelado. Ele não entra mais nos totais, mas permanece no histórico.", "info")
    return redirect(url_for("financeiro.detalhe", lid=lid))


@bp.route("/<int:lid>/anexo")
@propriedade_requerida("financeiro")
def anexo(lid):
    lanc = obter_da_propriedade(Lancamento, lid)
    if not lanc.anexo:
        abort(404)
    resp = send_file(caminho_absoluto(lanc.anexo), as_attachment=True,
                     download_name=f"anexo-lancamento-{lanc.id}.{lanc.anexo.rsplit('.', 1)[-1]}")
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/fluxo")
@propriedade_requerida("financeiro")
def fluxo():
    """Fluxo de caixa mensal pelos pagamentos e recebimentos efetivos, com saldo acumulado."""
    pid = g.propriedade.id
    meses = int(request.args.get("meses", 12)) if request.args.get("meses", "12").isdigit() else 12
    meses = min(max(meses, 3), 36)
    chaves = ind._meses(meses)
    inicio = date(int(chaves[0][:4]), int(chaves[0][5:]), 1)
    saldo_inicial = ind.saldo_caixa_acumulado(pid, date.fromordinal(inicio.toordinal() - 1))
    mes_col = db.func.strftime("%Y-%m", Pagamento.data)
    linhas = (db.session.query(mes_col, Lancamento.tipo, db.func.sum(Pagamento.valor)).join(Lancamento)
              .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Pagamento.data >= inicio)
              .group_by(mes_col, Lancamento.tipo).all())
    ent = {c: Decimal("0") for c in chaves}
    sai = {c: Decimal("0") for c in chaves}
    for mes, tipo, total in linhas:
        if mes in ent:
            (ent if tipo == "receita" else sai)[mes] = ind.dec(total)
    tabela, saldo = [], saldo_inicial
    for c in chaves:
        saldo += ent[c] - sai[c]
        tabela.append({"mes": ind.rotulo_mes(c), "entradas": ent[c], "saidas": sai[c], "liquido": ent[c] - sai[c], "saldo": saldo})
    a_pagar, total_pagar = ind.em_aberto(pid, "despesa")
    a_receber, total_receber = ind.em_aberto(pid, "receita")
    grafico = {"type": "bar", "money": True, "labels": [t["mes"] for t in tabela], "datasets": [
        {"label": "Entradas", "data": [float(t["entradas"]) for t in tabela], "color": "#137536"},
        {"label": "Saídas", "data": [float(t["saidas"]) for t in tabela], "color": "#B42318"},
        {"label": "Saldo acumulado", "data": [float(t["saldo"]) for t in tabela], "color": "#D1A33A", "type": "line"}]}
    return render_template("financeiro/fluxo.html", tabela=tabela, saldo_inicial=saldo_inicial, grafico=grafico, meses=meses,
                           total_pagar=total_pagar, total_receber=total_receber, a_pagar=a_pagar[:8], a_receber=a_receber[:8],
                           tem_dados=any(ent[c] or sai[c] for c in chaves), hoje=date.today())


@bp.route("/categorias", methods=["GET", "POST"])
@propriedade_requerida("financeiro")
def categorias_view():
    pid = g.propriedade.id
    v, e = {"tipo": "despesa"}, {}
    if request.method == "POST":
        if request.form.get("remover"):
            cat = CategoriaFinanceira.query.filter_by(id=request.form.get("remover", type=int), propriedade_id=pid).first_or_404()
            if Lancamento.query.filter_by(categoria_id=cat.id).count():
                flash("Categoria em uso por lançamentos: não pode ser removida.", "warning")
            else:
                db.session.delete(cat)
                db.session.commit()
                flash("Categoria removida.", "info")
            return redirect(url_for("financeiro.categorias_view"))
        form = Form(request.form)
        tipo = form.choice("tipo", TIPOS)
        nome = form.text("nome", required=True, max_len=80)
        if nome and tipo and any(c.nome.lower() == nome.lower() for c in categorias(pid, tipo)):
            form.error("nome", "Já existe uma categoria com este nome.")
        if form.ok:
            db.session.add(CategoriaFinanceira(propriedade_id=pid, tipo=tipo, nome=nome))
            db.session.commit()
            flash("Categoria criada.", "success")
            return redirect(url_for("financeiro.categorias_view"))
        v, e = form.values, form.errors
    return render_template("financeiro/categorias.html", cats=categorias(pid), tipos=TIPOS, v=v, e=e)
