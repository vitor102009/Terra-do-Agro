"""Gera notificações internas a partir dos dados (vacinas, estoque, contas, tarefas).

Cada alerta usa uma chave única por usuário e evento, então a rotina pode rodar várias vezes
sem criar notificações duplicadas. Nenhum e-mail ou push é disparado aqui.
"""
import time
from datetime import date, timedelta

from flask import session, url_for

from app.extensions import db
from app.models import Lancamento, PropriedadeMembro, Propriedade, Tarefa
from app.services.indicadores import insumos_alerta, vacinas_alerta
from app.utils.formatos import data_br, moeda
from app.utils.registro import notificar

INTERVALO_SEGUNDOS = 600


def gerar_alertas(user, forcar=False):
    agora = time.time()
    if not forcar and agora - session.get("_alertas_em", 0) < INTERVALO_SEGUNDOS:
        return
    session["_alertas_em"] = agora
    hoje = date.today()
    membros = (PropriedadeMembro.query.join(Propriedade)
               .filter(PropriedadeMembro.user_id == user.id, Propriedade.arquivada.is_(False)).all())
    for mb in membros:
        prop = mb.propriedade
        proximas, vencidas = vacinas_alerta(prop.id, dias=7)
        for ev in proximas + vencidas:
            situacao = "vencida" if ev.proxima_data < hoje else "próxima"
            notificar(user.id, "vacina", f"Aplicação {situacao}: {ev.produto}",
                      f"{ev.alvo} · prevista para {data_br(ev.proxima_data)} · {prop.nome}",
                      url_for("saude.index", aba="agenda"), chave=f"vac:{ev.id}:{ev.proxima_data}:{situacao}")
        baixo, vencendo = insumos_alerta(prop.id)
        for ins in baixo:
            notificar(user.id, "estoque", f"Estoque baixo: {ins.nome}",
                      f"Disponível: {ins.quantidade.normalize()} {ins.unidade} · mínimo {ins.quantidade_minima.normalize()} · {prop.nome}",
                      url_for("insumos.detalhe", iid=ins.id), chave=f"est:{ins.id}:{ins.quantidade}")
        for ins in vencendo:
            notificar(user.id, "vencimento", f"Validade próxima: {ins.nome}",
                      f"Vence em {data_br(ins.validade)} · {prop.nome}",
                      url_for("insumos.detalhe", iid=ins.id), chave=f"val:{ins.id}:{ins.validade}")
        if "financeiro" in mb.perms:
            limite = hoje + timedelta(days=3)
            contas = (Lancamento.query.filter(Lancamento.propriedade_id == prop.id, Lancamento.cancelado.is_(False),
                                              Lancamento.vencimento.isnot(None), Lancamento.vencimento <= limite).all())
            for l in contas:
                if l.saldo <= 0:
                    continue
                tipo = "conta_pagar" if l.tipo == "despesa" else "conta_receber"
                rotulo = "Conta a pagar" if l.tipo == "despesa" else "Conta a receber"
                quando = "vencida" if l.vencimento < hoje else f"vence {data_br(l.vencimento)}"
                notificar(user.id, tipo, f"{rotulo}: {l.descricao}", f"{moeda(l.saldo)} · {quando} · {prop.nome}",
                          url_for("financeiro.detalhe", lid=l.id), chave=f"fin:{l.id}:{l.vencimento}:{l.vencimento < hoje}")
    tarefas = (Tarefa.query.filter(Tarefa.status == "pendente", Tarefa.data <= hoje + timedelta(days=1),
                                   db.or_(Tarefa.responsavel_id == user.id,
                                          db.and_(Tarefa.user_id == user.id, Tarefa.responsavel_id.is_(None)))).all())
    for t in tarefas:
        tipo = "pesagem" if t.tipo == "pesagem" else "tarefa"
        notificar(user.id, tipo, f"{'Pesagem programada' if tipo == 'pesagem' else 'Tarefa'}: {t.titulo}",
                  f"Data: {data_br(t.data)}", url_for("notificacoes.agenda"), chave=f"tar:{t.id}:{t.data}")
    db.session.commit()
