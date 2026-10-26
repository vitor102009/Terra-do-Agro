"""Cotações: coleta com controle de frequência, normalização, estado, variação, endpoints e falhas das fontes.

As fontes externas são simuladas: nenhum teste acessa a internet.
"""
import json
import urllib.error
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import Cotacao, FonteCotacao, utcnow
from app.services import cotacoes as svc
from tests.conftest import criar_usuario, login


def _ptax(linhas_por_moeda):
    chamadas = []

    def buscar(url):
        chamadas.append(url)
        moeda = "USD" if "%27USD%27" in url else "EUR"
        return json.dumps({"value": linhas_por_moeda.get(moeda, [])})
    return buscar, chamadas


ONTEM = (date.today() - timedelta(days=1)).isoformat()
HOJE = date.today().isoformat()
LINHAS = {"USD": [{"cotacaoVenda": 5.0218, "dataHoraCotacao": f"{ONTEM} 13:09:36.250006", "tipoBoletim": "Fechamento"},
                  {"cotacaoVenda": 5.1004, "dataHoraCotacao": f"{HOJE} 10:05:10.000801", "tipoBoletim": "Abertura"}],
          "EUR": [{"cotacaoVenda": 6.1427, "dataHoraCotacao": f"{HOJE} 11:09:12.593517", "tipoBoletim": "Intermediário"}]}


def test_coleta_bcb_normaliza_e_nao_duplica(app):
    buscar, chamadas = _ptax(LINHAS)
    assert svc.coletar("bcb", buscar=buscar) == 3
    usd = Cotacao.query.filter_by(serie="bcb:USD").order_by(Cotacao.publicado_em).all()
    assert [u.valor for u in usd] == [Decimal("5.0218"), Decimal("5.1004")]  # 4 casas preservadas
    assert usd[-1].unidade == "R$ por 1 USD" and usd[-1].origem == "bcb" and usd[-1].coletado_em
    assert usd[-1].publicado_em == datetime.fromisoformat(f"{HOJE} 10:05:10.000801")
    assert svc.coletar("bcb", forcar=True, buscar=buscar) == 0  # mesmos boletins: nada novo
    assert Cotacao.query.count() == 3 and len(chamadas) == 4


def test_frequencia_minima_impede_nova_consulta(app):
    buscar, chamadas = _ptax(LINHAS)
    svc.coletar("bcb", buscar=buscar)
    assert svc.coletar("bcb", buscar=buscar) is None  # dentro do intervalo de 15 min
    assert len(chamadas) == 2  # só a primeira coleta (USD + EUR)
    f = db.session.get(FonteCotacao, "bcb")
    f.proxima_em = datetime(2000, 1, 1)
    db.session.commit()
    assert svc.coletar("bcb", buscar=buscar) == 0
    assert len(chamadas) == 4


def test_fonte_indisponivel_mantem_ultimo_dado_e_recua(app):
    buscar, _ = _ptax(LINHAS)
    svc.coletar("bcb", buscar=buscar)

    def falha(url):
        raise urllib.error.URLError("sem rede")
    assert svc.coletar("bcb", forcar=True, buscar=falha) is None
    f = db.session.get(FonteCotacao, "bcb")
    assert f.falhas_seguidas == 1 and "indisponível" in f.ultimo_erro
    assert f.proxima_em > utcnow() + timedelta(minutes=10)
    itens = svc.recentes({"fonte": "bcb"})
    assert {i["chave"] for i in itens} == {"bcb:USD", "bcb:EUR"}  # o último valor continua disponível
    assert svc.coletar("bcb", forcar=True, buscar=lambda u: "não é json") is None
    assert "inválida" in db.session.get(FonteCotacao, "bcb").ultimo_erro


def test_variacao_estado_e_unidades(app):
    buscar, _ = _ptax(LINHAS)
    svc.coletar("bcb", buscar=buscar)
    usd = next(i for i in svc.recentes() if i["chave"] == "bcb:USD")
    assert usd["valor"] == 5.1004 and usd["comparado_com"]["valor"] == 5.0218
    assert round(usd["variacao"], 4) == 0.0786 and round(usd["variacao_pct"], 2) == 1.57
    assert usd["estado"] == "atualizada" and usd["categoria"] == "moedas" and usd["casas"] == 4
    eur = next(i for i in svc.recentes() if i["chave"] == "bcb:EUR")
    assert eur["variacao"] is None  # sem valor de dia anterior: não inventa variação

    antigo = date.today() - timedelta(days=30)
    db.session.add_all([
        Cotacao(produto="Milho", praca="Rio Verde", uf="GO", valor=Decimal("60"), unidade="R$/sc 60 kg", fonte="Cooperativa X",
                data_referencia=antigo - timedelta(days=1)),
        Cotacao(produto="Milho", praca="Rio Verde", uf="GO", valor=Decimal("62"), unidade="R$/t", fonte="Cooperativa X",
                data_referencia=antigo),
    ])
    db.session.commit()
    milho = next(i for i in svc.recentes() if i["produto"] == "Milho")
    assert milho["estado"] == "desatualizada"
    assert milho["variacao"] is None  # unidades diferentes não são comparáveis
    assert milho["categoria"] == "graos" and milho["origem"] == "manual"


def test_cepea_parser_le_praca_e_unidade_da_fonte(app):
    widget = ("document.write(`<table><tr><td>Data</td><td>Produto</td><td>Valor</td></tr>"
              "<tr><td>23/10/2026</td><td>Boi Gordo</td><td>@</td><td>R$</td><td>381,40</td></tr>"
              "<tr><td>23/10/2026</td><td>Bezerro - MS</td><td>cabeça</td><td>R$</td><td>3.518,84</td></tr></table>`)")
    itens = svc._cepea_parse(widget)
    assert [(i["produto"], i["valor"], i["unidade"], i["uf"]) for i in itens] == [
        ("Boi Gordo (indicador CEPEA)", Decimal("381.40"), "R$/@", None),
        ("Bezerro - MS (indicador CEPEA)", Decimal("3518.84"), "R$/cabeça", "MS")]
    assert "Brasil" not in itens[0]["praca"]  # indicador regional não é apresentado como nacional
    assert itens[0]["publicado_em"] is None  # a fonte só informa a data


def test_endpoints_exigem_login_e_respondem(app, client):
    assert client.get("/cotacoes/dados.json").status_code in (302, 401)
    criar_usuario()
    login(client)
    buscar, _ = _ptax(LINHAS)
    svc.coletar("bcb", buscar=buscar)
    r1 = client.get("/cotacoes/dados.json")
    assert r1.status_code == 200 and r1.headers["Cache-Control"] == "no-store"
    d1 = r1.get_json()
    assert {i["chave"] for i in d1["itens"]} == {"bcb:USD", "bcb:EUR"}
    assert {f["slug"]: f["estado"] for f in d1["fontes"]} == {"bcb": "desativada", "cepea": "desativada"}
    d2 = client.get("/cotacoes/dados.json").get_json()
    assert d1["versao"] == d2["versao"]  # sem dado novo, a tela não re-renderiza
    assert client.get("/cotacoes/dados.json?categoria=graos").get_json()["itens"] == []
    pagina = client.get("/cotacoes").get_data(as_text=True)
    assert 'data-intervalo="3"' in pagina and "js/cotacoes.js" in pagina and "Dólar comercial" in pagina
    h = client.get("/cotacoes/historico.json?chave=bcb:USD&dias=7").get_json()
    assert len(h["pontos"]) == 2 and h["variacao_periodo"]["valor_final"] == 5.1004
    assert client.get("/cotacoes/historico.json?chave=bcb:USD&dias=3").status_code == 400


def test_cadastro_manual_do_admin_continua_funcionando(app, client):
    criar_usuario(email="admin@teste.com", admin=True)
    login(client, "admin@teste.com")
    r = client.post("/admin/cotacoes", data={"produto": "Leite", "praca": "Sul de Minas", "uf": "MG", "valor": "2,45",
                                             "unidade": "R$/litro", "fonte": "Laticínio Y", "data_referencia": date.today().isoformat()})
    assert r.status_code == 302
    leite = next(i for i in svc.recentes() if i["produto"] == "Leite")
    assert leite["valor"] == 2.45 and leite["origem"] == "manual" and leite["categoria"] == "leite"
    assert client.get("/painel").status_code == 200
