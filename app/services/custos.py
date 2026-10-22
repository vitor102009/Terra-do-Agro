"""Custos de produção por lote e por animal.

Critérios (documentados também no README e na própria página):
1. Base: despesas não canceladas, pela data de competência dentro do período, cuja categoria está marcada
   como 'custo de produção'.
2. Compras de estoque (lançamentos com origem 'estoque') NÃO entram diretamente: entra o CONSUMO do estoque
   (saídas valorizadas pelo custo médio) no período. Assim a mesma compra nunca é contada duas vezes.
3. Custo direto do animal: despesas vinculadas ao animal.
4. Custo direto do lote: despesas vinculadas ao lote (sem animal) + saídas de estoque destinadas ao lote.
5. Custo compartilhado: despesas e saídas de estoque sem lote nem animal. É rateado entre os lotes
   proporcionalmente ao número de animais ativos em cada lote na data final do período.
6. Custo por animal = custo direto do animal + (custo direto do lote ÷ animais do lote)
   + (custo compartilhado ÷ total de animais ativos).
Os valores são indicadores gerenciais e não substituem a contabilidade da propriedade.
"""
from collections import defaultdict
from decimal import Decimal

from app.models import Animal, CategoriaFinanceira, Lancamento, Lote, MovimentacaoInsumo, Insumo

ZERO = Decimal("0")


def calcular(pid, inicio, fim):
    despesas = (Lancamento.query.join(CategoriaFinanceira, Lancamento.categoria_id == CategoriaFinanceira.id)
                .filter(Lancamento.propriedade_id == pid, Lancamento.tipo == "despesa", Lancamento.cancelado.is_(False),
                        Lancamento.origem != "estoque", CategoriaFinanceira.custo_producao.is_(True),
                        Lancamento.data >= inicio, Lancamento.data <= fim).all())
    consumos = (MovimentacaoInsumo.query.join(Insumo)
                .filter(Insumo.propriedade_id == pid, MovimentacaoInsumo.tipo == "saida",
                        MovimentacaoInsumo.data >= inicio, MovimentacaoInsumo.data <= fim).all())

    animais = Animal.query.filter_by(propriedade_id=pid, status="ativo").all()
    por_lote_animais = defaultdict(list)
    for a in animais:
        por_lote_animais[a.lote_id].append(a)
    total_animais = len(animais)

    direto_animal = defaultdict(lambda: ZERO)
    direto_lote = defaultdict(lambda: ZERO)
    consumo_lote = defaultdict(lambda: ZERO)
    compartilhado = ZERO
    sem_destino_valor = ZERO  # custos de animais que não estão mais ativos
    for d in despesas:
        if d.animal_id:
            direto_animal[d.animal_id] += d.valor
        elif d.lote_id:
            direto_lote[d.lote_id] += d.valor
        else:
            compartilhado += d.valor
    for c in consumos:
        valor = c.custo_total or ZERO
        if c.animal_id:
            direto_animal[c.animal_id] += valor
        elif c.lote_id:
            consumo_lote[c.lote_id] += valor
        else:
            compartilhado += valor

    ativos_ids = {a.id for a in animais}
    for aid, val in direto_animal.items():
        if aid not in ativos_ids:
            sem_destino_valor += val

    lotes = {l.id: l for l in Lote.query.filter_by(propriedade_id=pid).all()}
    chaves_lote = set(por_lote_animais) | set(direto_lote) | set(consumo_lote)
    linhas_lote = []
    for lid in sorted(chaves_lote, key=lambda k: (k is None, lotes[k].nome if k in lotes else "")):
        qtd = len(por_lote_animais.get(lid, []))
        rateio = (compartilhado * qtd / total_animais) if total_animais else ZERO
        diretos_animais = sum((direto_animal[a.id] for a in por_lote_animais.get(lid, [])), ZERO)
        total = direto_lote[lid] + consumo_lote[lid] + diretos_animais + rateio
        linhas_lote.append({
            "lote": lotes[lid].nome if lid in lotes else "Sem lote", "lote_id": lid, "animais": qtd,
            "direto": direto_lote[lid], "consumo": consumo_lote[lid], "animais_direto": diretos_animais,
            "rateio": rateio, "total": total, "por_cabeca": (total / qtd) if qtd else None,
        })

    linhas_animal = []
    for a in sorted(animais, key=lambda x: (x.brinco or "", x.id)):
        qtd_lote = len(por_lote_animais[a.lote_id])
        parte_lote = ((direto_lote[a.lote_id] + consumo_lote[a.lote_id]) / qtd_lote) if qtd_lote else ZERO
        parte_comp = (compartilhado / total_animais) if total_animais else ZERO
        linhas_animal.append({"animal": a, "direto": direto_animal[a.id], "lote": parte_lote, "rateio": parte_comp,
                              "total": direto_animal[a.id] + parte_lote + parte_comp})

    lotes_sem_animais = sum((l["direto"] + l["consumo"] for l in linhas_lote if l["animais"] == 0), ZERO)
    total_geral = sum((d.valor for d in despesas), ZERO) + sum((c.custo_total or ZERO for c in consumos), ZERO)
    return {
        "lotes": linhas_lote, "animais": linhas_animal, "compartilhado": compartilhado, "total_geral": total_geral,
        "total_animais": total_animais, "custo_animais_inativos": sem_destino_valor,
        "custo_lotes_sem_animais": lotes_sem_animais,
        "custo_medio_cabeca": (total_geral / total_animais) if total_animais else None,
        "qtd_despesas": len(despesas), "qtd_consumos": len(consumos),
    }
