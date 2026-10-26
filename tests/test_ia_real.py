"""Chamada REAL ao provedor (Anthropic). Ignorado sem credencial: não roda por padrão.

Para executar (gera custo):
    set ANTHROPIC_API_KEY=...        (ou no .env)
    set RUN_REAL_AI=1
    python -m pytest -q tests/test_ia_real.py
"""
import os
from datetime import date
from decimal import Decimal

import pytest

from app.extensions import db
from app.models import Cotacao
from app.services import ia_agro

pytestmark = pytest.mark.skipif(not (os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("RUN_REAL_AI") == "1"),
                                reason="Defina ANTHROPIC_API_KEY e RUN_REAL_AI=1 para o teste real da IA.")


def test_chamada_real_usa_a_cotacao_do_sistema(app, client, produtor):
    app.config["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
    ia_agro._cliente = None
    st = ia_agro.status(forcar=True)
    assert st["estado"] == "ok", st["motivo"]  # chamada autenticada real (sem custo de tokens)
    db.session.add(Cotacao(produto="Boi gordo", praca="Praça de teste", uf="SP", valor=Decimal("123.45"), unidade="R$/@",
                           fonte="Fonte fictícia de teste", data_referencia=date.today()))
    db.session.commit()
    r = client.post("/ia/perguntar", json={"pergunta": "Qual a cotação mais recente do boi gordo no sistema? Cite a fonte."})
    corpo = r.get_json()
    assert r.status_code == 200, corpo
    assert "cotacoes" in corpo["mensagem"]["ferramentas"]
    assert "123,45" in corpo["mensagem"]["texto"] or "123.45" in corpo["mensagem"]["texto"]
    pagina = client.get(corpo["url"]).get_data(as_text=True)  # a resposta real aparece na interface
    assert "indisponível" not in pagina and ("123,45" in pagina or "123.45" in pagina)
