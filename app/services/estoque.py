"""Movimentações de estoque de insumos.

Regra para não duplicar custos: a DESPESA financeira acontece apenas na compra (entrada com
'gerar despesa'). Saídas e consumos apenas apropriam o custo médio ao lote/animal/atividade para
fins de custo de produção, sem criar novo lançamento financeiro.
"""
from decimal import Decimal

from app.extensions import db
from app.models import Lancamento, MovimentacaoInsumo, Pagamento

Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")


class EstoqueError(ValueError):
    pass


def registrar_entrada(insumo, quantidade, data, user_id, custo_unitario=None, fornecedor=None, validade=None,
                      gerar_despesa=False, categoria_id=None, pago=False, vencimento=None, justificativa=None):
    if quantidade is None or quantidade <= 0:
        raise EstoqueError("A quantidade deve ser maior que zero.")
    custo_total = (custo_unitario * quantidade).quantize(Q2) if custo_unitario is not None else None
    if custo_unitario is not None:
        # Custo médio ponderado: (saldo atual × custo médio + entrada × custo) ÷ novo saldo
        saldo = insumo.quantidade if insumo.quantidade > 0 else Decimal("0")
        novo_total = saldo + quantidade
        insumo.custo_medio = ((saldo * Decimal(insumo.custo_medio or 0) + quantidade * custo_unitario) / novo_total).quantize(Q4)
    insumo.quantidade = (insumo.quantidade or 0) + quantidade
    if fornecedor:
        insumo.fornecedor = fornecedor
    if validade:
        insumo.validade = validade
    mov = MovimentacaoInsumo(insumo=insumo, tipo="entrada", quantidade=quantidade, data=data, custo_unitario=custo_unitario,
                             custo_total=custo_total, fornecedor=fornecedor, validade=validade, justificativa=justificativa,
                             registrado_por=user_id)
    db.session.add(mov)
    if gerar_despesa:
        if not custo_total or not categoria_id:
            raise EstoqueError("Para lançar a despesa, informe o custo unitário e a categoria.")
        lanc = Lancamento(propriedade_id=insumo.propriedade_id, tipo="despesa",
                          descricao=f"Compra de {insumo.nome} ({quantidade.normalize()} {insumo.unidade})",
                          categoria_id=categoria_id, valor=custo_total, data=data, vencimento=vencimento,
                          contraparte=fornecedor, origem="estoque", registrado_por=user_id)
        if pago:
            lanc.pagamentos.append(Pagamento(data=data, valor=custo_total, registrado_por=user_id))
        db.session.add(lanc)
        db.session.flush()
        mov.lancamento_id = lanc.id
    return mov


def registrar_saida(insumo, quantidade, data, user_id, lote_id=None, animal_id=None, atividade=None, justificativa=None):
    if quantidade is None or quantidade <= 0:
        raise EstoqueError("A quantidade deve ser maior que zero.")
    if quantidade > insumo.quantidade:
        raise EstoqueError(f"Estoque insuficiente: disponível {insumo.quantidade.normalize()} {insumo.unidade}.")
    custo_unit = Decimal(insumo.custo_medio or 0)
    insumo.quantidade = insumo.quantidade - quantidade
    mov = MovimentacaoInsumo(insumo=insumo, tipo="saida", quantidade=quantidade, data=data, custo_unitario=custo_unit or None,
                             custo_total=(custo_unit * quantidade).quantize(Q2) if custo_unit else None, lote_id=lote_id,
                             animal_id=animal_id, atividade=atividade, justificativa=justificativa, registrado_por=user_id)
    db.session.add(mov)
    return mov


def registrar_ajuste(insumo, nova_quantidade, data, user_id, justificativa):
    if not justificativa:
        raise EstoqueError("Informe a justificativa do ajuste.")
    if nova_quantidade is None or nova_quantidade < 0:
        raise EstoqueError("A quantidade contada não pode ser negativa.")
    diferenca = nova_quantidade - insumo.quantidade
    if diferenca == 0:
        raise EstoqueError("A quantidade informada é igual ao saldo atual.")
    insumo.quantidade = nova_quantidade
    mov = MovimentacaoInsumo(insumo=insumo, tipo="ajuste", quantidade=diferenca, data=data, justificativa=justificativa,
                             registrado_por=user_id)
    db.session.add(mov)
    return mov
