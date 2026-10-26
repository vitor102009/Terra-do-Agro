"""Consulta REAL às fontes de cotação (sem custo, mas depende da internet). Não roda por padrão.

    set RUN_REAL_QUOTES=1
    python -m pytest -q tests/test_cotacoes_real.py
"""
import os
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.models import Cotacao
from app.services import cotacoes as svc

pytestmark = pytest.mark.skipif(os.environ.get("RUN_REAL_QUOTES") != "1",
                                reason="Defina RUN_REAL_QUOTES=1 para consultar as fontes reais.")


def test_bcb_ptax_real(app):
    novos = svc.coletar("bcb", forcar=True)
    assert novos and novos > 0, "O Banco Central não retornou boletins"
    for serie in ("bcb:USD", "bcb:USD:compra", "bcb:EUR", "bcb:EUR:compra"):
        c = Cotacao.query.filter_by(serie=serie).order_by(Cotacao.publicado_em.desc()).first()
        assert c is not None, serie
        assert Decimal("1") < c.valor < Decimal("20") and c.unidade.startswith("R$ por 1 ")
        assert c.publicado_em and date.today() - c.data_referencia <= timedelta(days=5)
        assert c.fonte == "Banco Central do Brasil — PTAX"
    itens = {i["chave"]: i for i in svc.recentes()}
    assert itens["bcb:USD"]["estado"] == "atualizada" and itens["bcb:USD"]["publicado_br"]
