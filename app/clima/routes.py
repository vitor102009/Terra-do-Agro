from flask import Blueprint, g, jsonify, render_template, request
from flask_login import current_user, login_required

from app.models import Cotacao
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


@bp.route("/cotacoes")
@login_required
def cotacoes():
    produto = request.args.get("produto")
    todas = Cotacao.query.order_by(Cotacao.data_referencia.desc(), Cotacao.id.desc()).all()
    recentes = {}
    for c in todas:
        recentes.setdefault((c.produto, c.praca), c)
    produtos = sorted({c.produto for c in todas})
    historico = [c for c in todas if c.produto == produto][:60] if produto else []
    grafico = None
    if produto and len(historico) >= 2:
        serie = sorted(historico, key=lambda c: c.data_referencia)
        pracas = sorted({c.praca for c in serie})
        datas = sorted({c.data_referencia for c in serie})
        grafico = {"type": "line", "money": True, "labels": [d.strftime("%d/%m/%y") for d in datas],
                   "datasets": [{"label": pr, "data": [next((float(c.valor) for c in serie if c.praca == pr and c.data_referencia == d), None)
                                                       for d in datas]} for pr in pracas]}
    return render_template("clima/cotacoes.html", recentes=sorted(recentes.values(), key=lambda c: (c.produto, c.praca)),
                           produtos=produtos, produto=produto, historico=historico, grafico=grafico)
