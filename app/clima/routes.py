import hashlib
import json

from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user, login_required

from app.models import utcnow
from app.services import cotacoes as servico_cotacoes
from app.services.clima import previsao

bp = Blueprint("clima", __name__)


def _alvo():
    if g.get("propriedade"):
        return {"prop": g.propriedade}
    return {"municipio": current_user.municipio, "uf": current_user.uf}


@bp.route("/clima")
@login_required
def clima():
    return render_template("clima/clima.html", p=previsao(**_alvo()))


@bp.route("/clima/resumo.json")
@login_required
def resumo():
    resp = jsonify(previsao(**_alvo()))
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _filtros():
    a = request.args
    return {k: (a.get(k) or "").strip()[:60] for k in ("categoria", "produto", "uf", "fonte") if a.get(k)}


@bp.route("/cotacoes")
@login_required
def cotacoes():
    servico_cotacoes.atualizar_vencidas()  # primeira visita já coleta (respeitando o intervalo de cada fonte)
    itens = servico_cotacoes.recentes(_filtros())
    return render_template("clima/cotacoes.html", itens=itens, filtros=_filtros(),
                           mensagem_vazia=servico_cotacoes.mensagem_sem_dados(_filtros()),
                           categorias={k: v[0] for k, v in servico_cotacoes.CATEGORIAS.items()},
                           fontes=servico_cotacoes.estado_fontes(), intervalo_tela=3)


@bp.route("/cotacoes/dados.json")
@login_required
def cotacoes_dados():
    # Coleta somente as fontes cujo intervalo mínimo venceu; nas demais chamadas, só lê o banco.
    servico_cotacoes.atualizar_vencidas(forcar=request.args.get("forcar") == "1")
    itens = servico_cotacoes.recentes(_filtros())
    corpo = {"itens": itens, "fontes": servico_cotacoes.estado_fontes(),
             "mensagem_vazia": servico_cotacoes.mensagem_sem_dados(_filtros())}
    corpo["versao"] = hashlib.sha1(json.dumps(corpo, sort_keys=True, default=str).encode()).hexdigest()[:16]
    corpo["consultado_em"] = utcnow().isoformat(timespec="seconds") + "Z"
    resp = jsonify(corpo)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/cotacoes/historico.json")
@login_required
def cotacoes_historico():
    chave = (request.args.get("chave") or "").strip()[:200]
    dias = request.args.get("dias", 90, type=int)
    if not chave or dias not in (7, 30, 90, 180, 365):
        return jsonify(erro="Parâmetros inválidos."), 400
    resp = jsonify(servico_cotacoes.historico(chave, dias))
    resp.headers["Cache-Control"] = "no-store"
    return resp
