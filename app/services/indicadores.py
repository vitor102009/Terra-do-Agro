"""Cálculo de indicadores a partir dos dados reais do banco.

Critérios (também descritos no README):
- Receitas e despesas do período: soma dos lançamentos não cancelados pela DATA DE COMPETÊNCIA
  (campo `data`) dentro do período. É o resultado econômico do período.
- Fluxo de caixa: soma dos pagamentos/recebimentos efetivos (tabela `pagamentos`) pela data em que ocorreram.
- Contas a pagar/receber: saldo em aberto (valor - pagamentos) de lançamentos não cancelados.
- Ganho médio diário (GMD) de um animal: (último peso - primeiro peso) / dias entre as duas pesagens,
  usando pesagens individuais. Só é calculado com 2 ou mais pesagens e intervalo de pelo menos 7 dias.
- Taxa de prenhez: fêmeas gestantes / (vazias + cobertas aguardando diagnóstico + gestantes), considerando
  apenas fêmeas ativas com situação reprodutiva informada. Não calculada sem fêmeas nessas situações.
"""
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models import (
    Animal, AuditLog, CategoriaFinanceira, EventoSanitario, Insumo, Lancamento, Lote, Pagamento, Pesagem, Tarefa,
)

ZERO = Decimal("0")
MIN_DIAS_GMD = 7

PERIODOS = OrderedDict([
    ("mes", "Mês atual"),
    ("30", "Últimos 30 dias"),
    ("90", "Últimos 90 dias"),
    ("ano", "Ano atual"),
    ("365", "Últimos 12 meses"),
])


def dec(valor):
    if valor is None:
        return ZERO
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def periodo(chave, hoje=None):
    hoje = hoje or date.today()
    if chave == "30":
        return hoje - timedelta(days=29), hoje
    if chave == "90":
        return hoje - timedelta(days=89), hoje
    if chave == "ano":
        return date(hoje.year, 1, 1), hoje
    if chave == "365":
        return hoje - timedelta(days=364), hoje
    return date(hoje.year, hoje.month, 1), hoje


def _base_lanc(pid):
    return Lancamento.query.filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False))


def total_lancamentos(pid, tipo, inicio, fim):
    q = db.session.query(func.coalesce(func.sum(Lancamento.valor), 0)).filter(
        Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.tipo == tipo,
        Lancamento.data >= inicio, Lancamento.data <= fim)
    return dec(q.scalar())


def total_caixa(pid, tipo, inicio, fim):
    q = (db.session.query(func.coalesce(func.sum(Pagamento.valor), 0)).join(Lancamento)
         .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.tipo == tipo,
                 Pagamento.data >= inicio, Pagamento.data <= fim))
    return dec(q.scalar())


def saldo_caixa_acumulado(pid, ate):
    rec = (db.session.query(func.coalesce(func.sum(Pagamento.valor), 0)).join(Lancamento)
           .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.tipo == "receita",
                   Pagamento.data <= ate)).scalar()
    desp = (db.session.query(func.coalesce(func.sum(Pagamento.valor), 0)).join(Lancamento)
            .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.tipo == "despesa",
                    Pagamento.data <= ate)).scalar()
    return dec(rec) - dec(desp)


def em_aberto(pid, tipo):
    """Lista de lançamentos com saldo em aberto e o total."""
    itens = [l for l in _base_lanc(pid).filter(Lancamento.tipo == tipo).order_by(Lancamento.vencimento, Lancamento.data).all()
             if l.saldo > 0]
    return itens, sum((l.saldo for l in itens), ZERO)


def _meses(qtd, hoje=None):
    hoje = hoje or date.today()
    y, mth = hoje.year, hoje.month
    out = []
    for _ in range(qtd):
        out.append(f"{y:04d}-{mth:02d}")
        mth -= 1
        if mth == 0:
            mth, y = 12, y - 1
    return list(reversed(out))


def rotulo_mes(chave):
    nomes = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
    y, mth = chave.split("-")
    return f"{nomes[int(mth) - 1]}/{y[2:]}"


def serie_financeira(pid, qtd=12):
    meses = _meses(qtd)
    inicio = date(int(meses[0][:4]), int(meses[0][5:]), 1)
    mes_col = func.strftime("%Y-%m", Lancamento.data)
    linhas = (db.session.query(mes_col, Lancamento.tipo, func.sum(Lancamento.valor))
              .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.data >= inicio)
              .group_by(mes_col, Lancamento.tipo).all())
    rec = {m: ZERO for m in meses}
    desp = {m: ZERO for m in meses}
    for mes, tipo, total in linhas:
        if mes in rec:
            (rec if tipo == "receita" else desp)[mes] = dec(total)
    resultado = [rec[m] - desp[m] for m in meses]
    acumulado, soma = [], ZERO
    for r in resultado:
        soma += r
        acumulado.append(soma)
    return {
        "labels": [rotulo_mes(m) for m in meses],
        "receitas": [float(rec[m]) for m in meses],
        "despesas": [float(desp[m]) for m in meses],
        "resultado": [float(r) for r in resultado],
        "acumulado": [float(a) for a in acumulado],
        "tem_dados": any(rec[m] or desp[m] for m in meses),
    }


def despesas_por_categoria(pid, inicio, fim):
    linhas = (db.session.query(CategoriaFinanceira.nome, func.sum(Lancamento.valor))
              .join(CategoriaFinanceira, Lancamento.categoria_id == CategoriaFinanceira.id)
              .filter(Lancamento.propriedade_id == pid, Lancamento.cancelado.is_(False), Lancamento.tipo == "despesa",
                      Lancamento.data >= inicio, Lancamento.data <= fim)
              .group_by(CategoriaFinanceira.nome).order_by(func.sum(Lancamento.valor).desc()).all())
    return [(nome, dec(total)) for nome, total in linhas]


def animais_ativos(pid):
    return Animal.query.filter_by(propriedade_id=pid, status="ativo")


def contagem_por(pid, coluna, rotulo_vazio="Não informado"):
    linhas = (db.session.query(coluna, func.count(Animal.id))
              .filter(Animal.propriedade_id == pid, Animal.status == "ativo")
              .group_by(coluna).order_by(func.count(Animal.id).desc()).all())
    return [(valor or rotulo_vazio, qtd) for valor, qtd in linhas]


def animais_por_lote(pid):
    linhas = (db.session.query(Lote.nome, func.count(Animal.id))
              .outerjoin(Lote, Animal.lote_id == Lote.id)
              .filter(Animal.propriedade_id == pid, Animal.status == "ativo")
              .group_by(Lote.nome).all())
    return [(nome or "Sem lote", qtd) for nome, qtd in linhas]


def reprodutivo(pid):
    linhas = dict(db.session.query(Animal.situacao_reprodutiva, func.count(Animal.id))
                  .filter(Animal.propriedade_id == pid, Animal.status == "ativo", Animal.sexo == "F")
                  .group_by(Animal.situacao_reprodutiva).all())
    gestantes = linhas.get("gestante", 0)
    base = gestantes + linhas.get("vazia", 0) + linhas.get("servida", 0)
    return {
        "paridas": linhas.get("parida", 0),
        "gestantes": gestantes,
        "vazias": linhas.get("vazia", 0),
        "servidas": linhas.get("servida", 0),
        "taxa_prenhez": (gestantes / base * 100) if base else None,
    }


def gmd(pesagens):
    """Recebe pesagens ordenadas por data e retorna dados de ganho médio diário, ou None."""
    validas = [p for p in pesagens if p.peso_kg]
    if len(validas) < 2:
        return None
    primeira, ultima = validas[0], validas[-1]
    dias = (ultima.data - primeira.data).days
    if dias < MIN_DIAS_GMD:
        return None
    ganho = dec(ultima.peso_kg) - dec(primeira.peso_kg)
    return {"dias": dias, "ganho": ganho, "gmd": ganho / dias, "inicio": primeira, "fim": ultima}


def gmd_medio_rebanho(pid, inicio=None, fim=None):
    q = (Pesagem.query.join(Animal, Pesagem.animal_id == Animal.id)
         .filter(Pesagem.propriedade_id == pid, Pesagem.animal_id.isnot(None), Animal.status == "ativo"))
    if inicio:
        q = q.filter(Pesagem.data >= inicio)
    if fim:
        q = q.filter(Pesagem.data <= fim)
    por_animal = {}
    for p in q.order_by(Pesagem.data).all():
        por_animal.setdefault(p.animal_id, []).append(p)
    valores = [r["gmd"] for r in (gmd(ps) for ps in por_animal.values()) if r]
    if not valores:
        return None, 0
    return sum(valores) / len(valores), len(valores)


def serie_pesagens(pid, qtd=12):
    meses = _meses(qtd)
    inicio = date(int(meses[0][:4]), int(meses[0][5:]), 1)
    mes_col = func.strftime("%Y-%m", Pesagem.data)
    linhas = dict((m, (float(avg), n)) for m, avg, n in
                  db.session.query(mes_col, func.avg(Pesagem.peso_kg), func.count(Pesagem.id))
                  .filter(Pesagem.propriedade_id == pid, Pesagem.animal_id.isnot(None), Pesagem.data >= inicio)
                  .group_by(mes_col).all())
    return {
        "labels": [rotulo_mes(m) for m in meses],
        "medias": [round(linhas[m][0], 1) if m in linhas else None for m in meses],
        "tem_dados": bool(linhas),
    }


def vacinas_alerta(pid, dias=15, hoje=None):
    hoje = hoje or date.today()
    base = EventoSanitario.query.filter(EventoSanitario.propriedade_id == pid, EventoSanitario.proxima_data.isnot(None),
                                        EventoSanitario.proxima_resolvida.is_(False))
    proximas = base.filter(EventoSanitario.proxima_data >= hoje,
                           EventoSanitario.proxima_data <= hoje + timedelta(days=dias)).order_by(EventoSanitario.proxima_data).all()
    vencidas = base.filter(EventoSanitario.proxima_data < hoje).order_by(EventoSanitario.proxima_data).all()
    return proximas, vencidas


def insumos_alerta(pid, dias=30, hoje=None):
    hoje = hoje or date.today()
    ativos = Insumo.query.filter_by(propriedade_id=pid, ativo=True).all()
    baixo = [i for i in ativos if i.quantidade_minima and i.quantidade <= i.quantidade_minima]
    vencendo = [i for i in ativos if i.validade and i.validade <= hoje + timedelta(days=dias) and i.quantidade > 0]
    return baixo, vencendo


def contas_vencidas(pid, hoje=None):
    hoje = hoje or date.today()
    itens = (_base_lanc(pid).filter(Lancamento.vencimento.isnot(None), Lancamento.vencimento < hoje)
             .order_by(Lancamento.vencimento).all())
    return [l for l in itens if l.saldo > 0]


def tarefas_pendentes(pid):
    return Tarefa.query.filter_by(propriedade_id=pid, status="pendente").order_by(Tarefa.data).all()


def atividades_recentes(pid, limite=10):
    return (AuditLog.query.filter_by(propriedade_id=pid, administrativa=False)
            .order_by(AuditLog.created_at.desc()).limit(limite).all())


def resumo_dashboard(prop, chave_periodo, ver_financeiro):
    pid = prop.id
    inicio, fim = periodo(chave_periodo)
    proximas, vencidas = vacinas_alerta(pid)
    baixo, vencendo = insumos_alerta(pid)
    tarefas = tarefas_pendentes(pid)
    dados = {
        "inicio": inicio, "fim": fim,
        "total_animais": animais_ativos(pid).count(),
        "repro": reprodutivo(pid),
        "vacinas_proximas": proximas, "vacinas_vencidas": vencidas,
        "estoque_baixo": baixo, "insumos_vencendo": vencendo,
        "tarefas": tarefas,
        "atividades": atividades_recentes(pid),
        "por_categoria": contagem_por(pid, Animal.categoria),
        "por_lote": animais_por_lote(pid),
        "pesagens": serie_pesagens(pid),
        "gmd_medio": gmd_medio_rebanho(pid, date.today() - timedelta(days=365)),
        "tem_lotes": Lote.query.filter_by(propriedade_id=pid).count() > 0,
    }
    alertas = len(vencidas) + len(baixo) + len(vencendo)
    if ver_financeiro:
        receitas = total_lancamentos(pid, "receita", inicio, fim)
        despesas = total_lancamentos(pid, "despesa", inicio, fim)
        vencidas_fin = contas_vencidas(pid)
        alertas += len(vencidas_fin)
        dados.update({
            "receitas": receitas, "despesas": despesas, "resultado": receitas - despesas,
            "caixa_periodo": total_caixa(pid, "receita", inicio, fim) - total_caixa(pid, "despesa", inicio, fim),
            "saldo_caixa": saldo_caixa_acumulado(pid, fim),
            "serie": serie_financeira(pid),
            "despesas_categoria": despesas_por_categoria(pid, inicio, fim),
            "contas_vencidas": vencidas_fin,
            "tem_lancamentos": _base_lanc(pid).count() > 0,
        })
    dados["alertas"] = alertas
    return dados
