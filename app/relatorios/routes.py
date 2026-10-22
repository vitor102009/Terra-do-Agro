import csv
import io
from collections import OrderedDict, defaultdict
from datetime import date
from decimal import Decimal

from flask import Blueprint, Response, abort, g, render_template, request
from sqlalchemy import func

from app.extensions import db
from app.models import (
    Animal, AuditLog, EventoReprodutivo, EventoSanitario, Insumo, Lancamento, Lote,
    MovimentacaoAnimal, Pagamento, Pesagem,
)
from app.models.financeiro import CATEGORIAS_INSUMO
from app.models.rebanho import (
    ESPECIES, SEXOS, SITUACOES_REPRODUTIVAS, TIPOS_MOVIMENTACAO, TIPOS_REPRODUTIVOS, TIPOS_SANITARIOS,
)
from app.services import custos as svc_custos
from app.services import indicadores as ind
from app.utils.acesso import pode, propriedade_requerida
from app.utils.formatos import data_br, datahora_br, moeda, numero

bp = Blueprint("relatorios", __name__)

RELATORIOS = OrderedDict([
    ("inventario", {"titulo": "Inventário do rebanho", "grupo": "Rebanho", "fin": False}),
    ("categorias", {"titulo": "Animais por categoria", "grupo": "Rebanho", "fin": False}),
    ("pesagens", {"titulo": "Histórico de pesagens", "grupo": "Rebanho", "fin": False}),
    ("vacinacoes", {"titulo": "Vacinações realizadas e pendentes", "grupo": "Rebanho", "fin": False}),
    ("reproducao", {"titulo": "Reprodução", "grupo": "Rebanho", "fin": False}),
    ("movimentacoes", {"titulo": "Entradas e saídas de animais", "grupo": "Rebanho", "fin": False}),
    ("produtividade", {"titulo": "Produtividade", "grupo": "Rebanho", "fin": False}),
    ("receitas", {"titulo": "Receitas", "grupo": "Financeiro", "fin": True}),
    ("despesas", {"titulo": "Despesas", "grupo": "Financeiro", "fin": True}),
    ("fluxo", {"titulo": "Fluxo de caixa", "grupo": "Financeiro", "fin": True}),
    ("resultado", {"titulo": "Resultado por período", "grupo": "Financeiro", "fin": True}),
    ("custos_lote", {"titulo": "Custos por lote", "grupo": "Financeiro", "fin": True}),
    ("custos_animal", {"titulo": "Custos por animal", "grupo": "Financeiro", "fin": True}),
    ("estoque", {"titulo": "Estoque de insumos", "grupo": "Operacional", "fin": False}),
    ("atividades", {"titulo": "Atividades registradas", "grupo": "Operacional", "fin": False}),
])


def _periodo():
    try:
        inicio = date.fromisoformat(request.args["inicio"]) if request.args.get("inicio") else None
        fim = date.fromisoformat(request.args["fim"]) if request.args.get("fim") else None
    except ValueError:
        inicio = fim = None
    padrao_i, padrao_f = ind.periodo("ano")
    inicio, fim = inicio or padrao_i, fim or padrao_f
    if inicio > fim:
        inicio, fim = fim, inicio
    return inicio, fim


def _r(colunas, linhas, totais=None, notas=None, num_cols=()):
    return {"colunas": colunas, "linhas": linhas, "totais": totais, "notas": notas or [], "num_cols": set(num_cols)}


def _filtro_lote():
    return request.args.get("lote", type=int)


def rel_inventario(pid, inicio, fim):
    q = Animal.query.filter_by(propriedade_id=pid, status="ativo")
    if _filtro_lote():
        q = q.filter_by(lote_id=_filtro_lote())
    linhas = [[a.brinco or f"#{a.id}", ESPECIES.get(a.especie), a.raca or "", SEXOS.get(a.sexo), data_br(a.data_nascimento),
               a.categoria or "", a.lote.nome if a.lote else "", SITUACOES_REPRODUTIVAS.get(a.situacao_reprodutiva),
               numero(a.peso_atual, 1) if a.peso_atual is not None else "—", data_br(a.data_entrada)]
              for a in q.order_by(Animal.brinco).all()]
    return _r(["Brinco", "Espécie", "Raça", "Sexo", "Nascimento", "Categoria", "Lote", "Situação reprodutiva", "Peso atual (kg)", "Entrada"],
              linhas, totais=["Total", f"{len(linhas)} animais"] + [""] * 8, num_cols=(8,),
              notas=["Posição atual do rebanho ativo (independe do período)."])


def rel_categorias(pid, inicio, fim):
    linhas = (db.session.query(Animal.especie, Animal.categoria, Animal.sexo, func.count(Animal.id), func.avg(Animal.peso_atual))
              .filter(Animal.propriedade_id == pid, Animal.status == "ativo")
              .group_by(Animal.especie, Animal.categoria, Animal.sexo).order_by(Animal.especie, Animal.categoria).all())
    total = sum(l[3] for l in linhas)
    return _r(["Espécie", "Categoria", "Sexo", "Quantidade", "% do rebanho", "Peso médio (kg)"],
              [[ESPECIES.get(e), c or "Sem categoria", SEXOS.get(s), n, numero(n / total * 100, 1) + "%" if total else "—",
                numero(p, 1) if p else "—"] for e, c, s, n, p in linhas],
              totais=["Total", "", "", total, "100%" if total else "—", ""], num_cols=(3, 4, 5))


def rel_pesagens(pid, inicio, fim):
    q = Pesagem.query.filter(Pesagem.propriedade_id == pid, Pesagem.data >= inicio, Pesagem.data <= fim)
    if _filtro_lote():
        q = q.outerjoin(Animal, Pesagem.animal_id == Animal.id).filter(db.or_(Pesagem.lote_id == _filtro_lote(), Animal.lote_id == _filtro_lote()))
    linhas = [[data_br(p.data), p.animal.identificacao if p.animal else f"Lote {p.lote.nome} ({p.qtd_animais})",
               "Individual" if p.animal_id else "Média do lote", numero(p.peso_kg, 1), p.responsavel or ""]
              for p in q.order_by(Pesagem.data).all()]
    gmd, n = ind.gmd_medio_rebanho(pid, inicio, fim)
    return _r(["Data", "Animal / lote", "Tipo", "Peso (kg)", "Responsável"], linhas, num_cols=(3,),
              notas=[f"{len(linhas)} pesagem(ns) no período.",
                     f"GMD médio no período: {numero(gmd, 3)} kg/dia ({n} animais)." if gmd is not None else "GMD: dados insuficientes no período."])


def rel_vacinacoes(pid, inicio, fim):
    hoje = date.today()
    realizados = (EventoSanitario.query.filter(EventoSanitario.propriedade_id == pid, EventoSanitario.data_aplicacao >= inicio,
                                               EventoSanitario.data_aplicacao <= fim).order_by(EventoSanitario.data_aplicacao).all())
    pendentes = (EventoSanitario.query.filter(EventoSanitario.propriedade_id == pid, EventoSanitario.proxima_data.isnot(None),
                                              EventoSanitario.proxima_resolvida.is_(False)).order_by(EventoSanitario.proxima_data).all())
    linhas = [["Realizada", data_br(e.data_aplicacao), TIPOS_SANITARIOS.get(e.tipo), e.produto, e.alvo, e.lote_produto or "", e.responsavel or ""]
              for e in realizados]
    linhas += [["Atrasada" if e.proxima_data < hoje else "Pendente", data_br(e.proxima_data), TIPOS_SANITARIOS.get(e.tipo), e.produto,
                e.alvo, "", ""] for e in pendentes]
    return _r(["Situação", "Data", "Tipo", "Produto", "Aplicação", "Partida", "Responsável"], linhas,
              notas=[f"{len(realizados)} aplicação(ões) no período; {len(pendentes)} pendente(s) na agenda (independe do período)."])


def rel_reproducao(pid, inicio, fim):
    evs = (EventoReprodutivo.query.filter(EventoReprodutivo.propriedade_id == pid, EventoReprodutivo.data >= inicio,
                                          EventoReprodutivo.data <= fim).order_by(EventoReprodutivo.data).all())
    linhas = [[data_br(e.data), e.animal.identificacao, TIPOS_REPRODUTIVOS.get(e.tipo),
               {"positivo": "Positivo", "negativo": "Negativo", "inconclusivo": "Inconclusivo"}.get(e.diagnostico_resultado, ""),
               (e.reprodutor.identificacao if e.reprodutor else (e.reprodutor_descricao or "")), data_br(e.previsao_parto) if e.previsao_parto else "",
               e.responsavel or ""] for e in evs]
    contagem = defaultdict(int)
    for e in evs:
        contagem[e.tipo] += 1
    r = ind.reprodutivo(pid)
    return _r(["Data", "Fêmea", "Evento", "Diagnóstico", "Reprodutor / sêmen", "Previsão de parto (estimada)", "Responsável"], linhas,
              notas=[", ".join(f"{TIPOS_REPRODUTIVOS[k]}: {v}" for k, v in contagem.items()) or "Nenhum evento no período.",
                     f"Situação atual: {r['gestantes']} gestantes, {r['vazias']} vazias, {r['paridas']} paridas, {r['servidas']} aguardando diagnóstico."
                     + (f" Taxa de prenhez: {numero(r['taxa_prenhez'], 1)}%." if r["taxa_prenhez"] is not None else "")])


def rel_movimentacoes(pid, inicio, fim):
    movs = (MovimentacaoAnimal.query.join(Animal).filter(Animal.propriedade_id == pid, MovimentacaoAnimal.data >= inicio,
                                                         MovimentacaoAnimal.data <= fim).order_by(MovimentacaoAnimal.data).all())
    ver_fin = pode("financeiro")
    linhas = [[data_br(mv.data), mv.animal.identificacao, TIPOS_MOVIMENTACAO.get(mv.tipo),
               (mv.lote_origem.nome if mv.lote_origem else "") + (" → " + mv.lote_destino.nome if mv.lote_destino else ""),
               mv.destino or "", moeda(mv.valor) if (mv.valor and ver_fin) else "", mv.observacao or ""] for mv in movs]
    contagem = defaultdict(int)
    for mv in movs:
        contagem[mv.tipo] += 1
    return _r(["Data", "Animal", "Movimentação", "Lotes", "Destino / comprador", "Valor", "Observação"], linhas, num_cols=(5,),
              notas=[", ".join(f"{TIPOS_MOVIMENTACAO[k]}: {v}" for k, v in contagem.items()) or "Nenhuma movimentação no período."])


def rel_produtividade(pid, inicio, fim):
    linhas = []
    pesagens = (Pesagem.query.join(Animal, Pesagem.animal_id == Animal.id)
                .filter(Pesagem.propriedade_id == pid, Animal.status == "ativo", Pesagem.data >= inicio, Pesagem.data <= fim)
                .order_by(Pesagem.data).all())
    por_animal = defaultdict(list)
    for p in pesagens:
        por_animal[p.animal_id].append(p)
    por_lote = defaultdict(list)
    for ps in por_animal.values():
        r = ind.gmd(ps)
        if r:
            por_lote[ps[0].animal.lote.nome if ps[0].animal.lote else "Sem lote"].append(r)
    for lote, rs in sorted(por_lote.items()):
        linhas.append([lote, len(rs), numero(sum(x["gmd"] for x in rs) / len(rs), 3), numero(sum(x["ganho"] for x in rs), 1),
                       numero(min(x["gmd"] for x in rs), 3), numero(max(x["gmd"] for x in rs), 3)])
    nascimentos = EventoReprodutivo.query.filter(EventoReprodutivo.propriedade_id == pid, EventoReprodutivo.tipo == "parto",
                                                 EventoReprodutivo.data >= inicio, EventoReprodutivo.data <= fim).count()
    obitos = (MovimentacaoAnimal.query.join(Animal).filter(Animal.propriedade_id == pid, MovimentacaoAnimal.tipo == "obito",
                                                           MovimentacaoAnimal.data >= inicio, MovimentacaoAnimal.data <= fim).count())
    r = ind.reprodutivo(pid)
    return _r(["Lote", "Animais com dados", "GMD médio (kg/dia)", "Ganho total (kg)", "GMD mínimo", "GMD máximo"], linhas,
              num_cols=(1, 2, 3, 4, 5),
              notas=["GMD por animal = (último − primeiro peso) ÷ dias, com ao menos 2 pesagens individuais e 7 dias de intervalo no período. "
                     "Animais sem dados suficientes não entram no cálculo.",
                     f"Partos no período: {nascimentos}. Óbitos no período: {obitos}.",
                     f"Taxa de prenhez atual: {numero(r['taxa_prenhez'], 1)}%." if r["taxa_prenhez"] is not None else "Taxa de prenhez: dados insuficientes."])


def _lancamentos(pid, tipo, inicio, fim):
    return (Lancamento.query.filter(Lancamento.propriedade_id == pid, Lancamento.tipo == tipo, Lancamento.cancelado.is_(False),
                                    Lancamento.data >= inicio, Lancamento.data <= fim).order_by(Lancamento.data).all())


def _rel_lanc(pid, tipo, inicio, fim):
    itens = _lancamentos(pid, tipo, inicio, fim)
    linhas = [[data_br(l.data), l.descricao, l.categoria.nome, l.contraparte or "", l.situacao_label, moeda(l.valor_pago), moeda(l.valor)]
              for l in itens]
    total = sum((l.valor for l in itens), Decimal("0"))
    pago = sum((l.valor_pago for l in itens), Decimal("0"))
    por_cat = defaultdict(lambda: Decimal("0"))
    for l in itens:
        por_cat[l.categoria.nome] += l.valor
    return _r(["Data", "Descrição", "Categoria", "Cliente / fornecedor", "Situação", "Pago / recebido", "Valor"], linhas,
              totais=["Total", "", "", "", "", moeda(pago), moeda(total)], num_cols=(5, 6),
              notas=["Por categoria: " + "; ".join(f"{k}: {moeda(v)}" for k, v in sorted(por_cat.items(), key=lambda x: -x[1]))] if por_cat else [])


def rel_receitas(pid, inicio, fim):
    return _rel_lanc(pid, "receita", inicio, fim)


def rel_despesas(pid, inicio, fim):
    return _rel_lanc(pid, "despesa", inicio, fim)


def _meses_entre(inicio, fim):
    chaves, y, mth = [], inicio.year, inicio.month
    while (y, mth) <= (fim.year, fim.month):
        chaves.append(f"{y:04d}-{mth:02d}")
        mth += 1
        if mth == 13:
            y, mth = y + 1, 1
    return chaves


def rel_fluxo(pid, inicio, fim):
    mes_col = func.strftime("%Y-%m", Pagamento.data)
    dados = (db.session.query(mes_col, Lancamento.tipo, func.sum(Pagamento.valor)).join(Lancamento)
             .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Pagamento.data >= inicio, Pagamento.data <= fim)
             .group_by(mes_col, Lancamento.tipo).all())
    mapa = defaultdict(lambda: {"receita": Decimal("0"), "despesa": Decimal("0")})
    for mes, tipo, total in dados:
        mapa[mes][tipo] = ind.dec(total)
    saldo = ind.saldo_caixa_acumulado(pid, date.fromordinal(inicio.toordinal() - 1))
    linhas = [["Saldo anterior", "", "", "", moeda(saldo)]]
    te = ts = Decimal("0")
    for c in _meses_entre(inicio, fim):
        e, s = mapa[c]["receita"], mapa[c]["despesa"]
        te, ts = te + e, ts + s
        saldo += e - s
        linhas.append([ind.rotulo_mes(c), moeda(e), moeda(s), moeda(e - s), moeda(saldo)])
    return _r(["Mês", "Entradas", "Saídas", "Líquido", "Saldo acumulado"], linhas, totais=["Total", moeda(te), moeda(ts), moeda(te - ts), moeda(saldo)],
              num_cols=(1, 2, 3, 4), notas=["Considera apenas valores efetivamente pagos e recebidos, pela data do pagamento."])


def rel_resultado(pid, inicio, fim):
    mes_col = func.strftime("%Y-%m", Lancamento.data)
    dados = (db.session.query(mes_col, Lancamento.tipo, func.sum(Lancamento.valor))
             .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.data >= inicio, Lancamento.data <= fim)
             .group_by(mes_col, Lancamento.tipo).all())
    mapa = defaultdict(lambda: {"receita": Decimal("0"), "despesa": Decimal("0")})
    for mes, tipo, total in dados:
        mapa[mes][tipo] = ind.dec(total)
    linhas, tr, td = [], Decimal("0"), Decimal("0")
    for c in _meses_entre(inicio, fim):
        r, d = mapa[c]["receita"], mapa[c]["despesa"]
        tr, td = tr + r, td + d
        linhas.append([ind.rotulo_mes(c), moeda(r), moeda(d), moeda(r - d), (numero((r - d) / r * 100, 1) + "%") if r else "—"])
    return _r(["Mês", "Receitas", "Despesas", "Resultado", "Margem"], linhas,
              totais=["Total", moeda(tr), moeda(td), moeda(tr - td), (numero((tr - td) / tr * 100, 1) + "%") if tr else "—"],
              num_cols=(1, 2, 3, 4), notas=["Regime de competência: considera a data do fato, independentemente do pagamento."])


def rel_custos_lote(pid, inicio, fim):
    c = svc_custos.calcular(pid, inicio, fim)
    linhas = [[l["lote"], l["animais"], moeda(l["direto"]), moeda(l["consumo"]), moeda(l["animais_direto"]), moeda(l["rateio"]),
               moeda(l["total"]), moeda(l["por_cabeca"]) if l["por_cabeca"] is not None else "—"] for l in c["lotes"]]
    return _r(["Lote", "Animais", "Despesas diretas", "Consumo de estoque", "Diretos dos animais", "Rateio compartilhado", "Total", "Por cabeça"],
              linhas, totais=["Total", c["total_animais"], "", "", "", moeda(c["compartilhado"]), moeda(c["total_geral"]),
                              moeda(c["custo_medio_cabeca"]) if c["custo_medio_cabeca"] is not None else "—"],
              num_cols=(1, 2, 3, 4, 5, 6, 7), notas=_notas_custos(c))


def rel_custos_animal(pid, inicio, fim):
    c = svc_custos.calcular(pid, inicio, fim)
    linhas = [[x["animal"].identificacao, x["animal"].lote.nome if x["animal"].lote else "Sem lote", moeda(x["direto"]), moeda(x["lote"]),
               moeda(x["rateio"]), moeda(x["total"])] for x in c["animais"]]
    return _r(["Animal", "Lote", "Custo direto", "Parte do lote", "Rateio compartilhado", "Total"], linhas, num_cols=(2, 3, 4, 5),
              notas=_notas_custos(c))


def _notas_custos(c):
    notas = ["Critérios: despesas de categorias de custo de produção, por competência. Compras de estoque entram pelo consumo "
             "(saídas valorizadas pelo custo médio), nunca pela compra, para não contar duas vezes. Custos sem lote nem animal são "
             "rateados pelo número de animais ativos de cada lote na data final.",
             f"Base: {c['qtd_despesas']} despesa(s) e {c['qtd_consumos']} saída(s) de estoque no período."]
    if c["custo_animais_inativos"]:
        notas.append(f"{moeda(c['custo_animais_inativos'])} referem-se a animais que já saíram do rebanho e não aparecem por animal.")
    if c["custo_lotes_sem_animais"]:
        notas.append(f"{moeda(c['custo_lotes_sem_animais'])} estão em lotes sem animais ativos (sem custo por cabeça).")
    return notas


def rel_estoque(pid, inicio, fim):
    insumos = Insumo.query.filter_by(propriedade_id=pid, ativo=True).order_by(Insumo.nome).all()
    ver_fin = pode("financeiro")
    linhas = []
    for i in insumos:
        movs = [mv for mv in i.movimentacoes if inicio <= mv.data <= fim]
        entradas = sum((mv.quantidade for mv in movs if mv.tipo == "entrada"), Decimal("0"))
        saidas = sum((mv.quantidade for mv in movs if mv.tipo == "saida"), Decimal("0"))
        linhas.append([i.nome, CATEGORIAS_INSUMO.get(i.categoria), i.unidade, numero(entradas, 2), numero(saidas, 2), numero(i.quantidade, 2),
                       numero(i.quantidade_minima, 2), "Baixo" if i.estoque_baixo else "", data_br(i.validade) if i.validade else "",
                       moeda(i.quantidade * i.custo_medio) if ver_fin and i.custo_medio else ""])
    return _r(["Insumo", "Categoria", "Unid.", "Entradas no período", "Saídas no período", "Saldo atual", "Mínimo", "Alerta", "Validade", "Valor em estoque"],
              linhas, num_cols=(3, 4, 5, 6, 9))


def rel_atividades(pid, inicio, fim):
    logs = (AuditLog.query.filter(AuditLog.propriedade_id == pid, AuditLog.administrativa.is_(False),
                                  func.date(AuditLog.created_at) >= inicio.isoformat(), func.date(AuditLog.created_at) <= fim.isoformat())
            .order_by(AuditLog.created_at.desc()).limit(2000).all())
    if not pode("financeiro"):
        logs = [l for l in logs if not l.acao.startswith("financeiro")]
    return _r(["Data e hora", "Usuário", "Atividade"], [[datahora_br(l.created_at), l.user.nome if l.user else "Sistema", l.descricao] for l in logs])


@bp.route("/")
@propriedade_requerida("ver")
def index():
    inicio, fim = _periodo()
    disponiveis = OrderedDict((k, r) for k, r in RELATORIOS.items() if not r["fin"] or pode("financeiro"))
    lotes = Lote.query.filter_by(propriedade_id=g.propriedade.id).order_by(Lote.nome).all()
    return render_template("relatorios/index.html", relatorios=disponiveis, inicio=inicio, fim=fim, lotes=lotes)


@bp.route("/custos")
@propriedade_requerida("financeiro")
def custos():
    inicio, fim = _periodo()
    c = svc_custos.calcular(g.propriedade.id, inicio, fim)
    return render_template("relatorios/custos.html", c=c, inicio=inicio, fim=fim, notas=_notas_custos(c))


@bp.route("/<tipo>")
@propriedade_requerida("ver")
def ver(tipo):
    meta = RELATORIOS.get(tipo)
    if not meta:
        abort(404)
    if meta["fin"] and not pode("financeiro"):
        abort(403)
    inicio, fim = _periodo()
    rel = globals()[f"rel_{tipo}"](g.propriedade.id, inicio, fim)
    if request.args.get("formato") == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([f"{meta['titulo']} · {g.propriedade.nome} · {data_br(inicio)} a {data_br(fim)}"])
        w.writerow(rel["colunas"])
        for linha in rel["linhas"]:
            w.writerow(linha)
        if rel["totais"]:
            w.writerow(rel["totais"])
        for n in rel["notas"]:
            w.writerow([n])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={tipo}-{inicio}-{fim}.csv"})
    lotes = Lote.query.filter_by(propriedade_id=g.propriedade.id).order_by(Lote.nome).all()
    return render_template("relatorios/ver.html", tipo=tipo, meta=meta, rel=rel, inicio=inicio, fim=fim, lotes=lotes,
                           gerado_em=date.today())
