"""Fluxos que antes só eram cobertos pela varredura de páginas: lotes e pastagens, alertas/notificações e clima."""
import json
import urllib.error
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import Animal, ClimaCache, EventoSanitario, Insumo, Lote, ManejoPastagem, MovimentacaoAnimal, Notificacao, Pastagem
from app.services import clima
from tests.conftest import criar_propriedade, criar_usuario, login, logout


def test_lotes_pastagens_transferencia_e_isolamento(app, client, produtor):
    _, prop = produtor
    r = client.post("/lotes/pastagens/nova", data={"nome": "Piquete 1", "area_ha": "12,5", "condicao": "boa"})
    assert r.status_code == 302
    past = Pastagem.query.one()
    assert past.area_ha == Decimal("12.50") and past.propriedade_id == prop.id
    assert client.post("/lotes/novo", data={"nome": "Recria", "pastagem_id": past.id}).status_code == 302
    assert client.post("/lotes/novo", data={"nome": "Engorda"}).status_code == 302
    recria, engorda = Lote.query.order_by(Lote.id).all()
    assert recria.pastagem_id == past.id
    assert client.post(f"/lotes/{recria.id}/editar", data={"nome": "Recria A", "pastagem_id": past.id}).status_code == 302
    assert db.session.get(Lote, recria.id).nome == "Recria A"

    a = Animal(propriedade_id=prop.id, brinco="L1", sexo="M", data_entrada=date.today(), lote_id=recria.id)
    db.session.add(a)
    db.session.commit()
    r = client.post(f"/lotes/{recria.id}/transferir", data={"animais": [a.id], "destino": engorda.id})
    assert r.status_code == 302
    assert db.session.get(Animal, a.id).lote_id == engorda.id
    mov = MovimentacaoAnimal.query.filter_by(animal_id=a.id, tipo="troca_lote").one()
    assert (mov.lote_origem_id, mov.lote_destino_id) == (recria.id, engorda.id)

    hoje = date.today().isoformat()
    r = client.post(f"/lotes/pastagens/{past.id}", data={"data": hoje, "tipo": "avaliacao", "altura_cm": "25"})
    assert r.status_code == 302 and ManejoPastagem.query.filter_by(pastagem_id=past.id).count() == 1

    logout(client)
    outro = criar_usuario(email="vizinho@teste.com")
    criar_propriedade(outro, nome="Vizinho")
    login(client, "vizinho@teste.com")
    assert client.get(f"/lotes/{recria.id}").status_code == 404
    assert client.post(f"/lotes/{recria.id}/editar", data={"nome": "Invadido"}).status_code == 404
    assert client.post(f"/lotes/{engorda.id}/transferir", data={"animais": [a.id], "acao": "adicionar"}).status_code == 404
    assert db.session.get(Lote, recria.id).nome == "Recria A"


def test_alertas_geram_notificacoes_unicas_e_marcar_lidas(app, client, produtor):
    u, prop = produtor
    db.session.add(EventoSanitario(propriedade_id=prop.id, tipo="vacina", produto="Aftosa",
                                   data_aplicacao=date.today() - timedelta(days=180),
                                   proxima_data=date.today() - timedelta(days=2)))
    db.session.add(Insumo(propriedade_id=prop.id, nome="Sal mineral", unidade="sc", quantidade=Decimal("1"),
                          quantidade_minima=Decimal("5")))
    db.session.commit()
    from app.services.alertas import gerar_alertas
    with app.test_request_context():
        gerar_alertas(u, forcar=True)
        gerar_alertas(u, forcar=True)  # idempotente
    db.session.commit()
    tipos = sorted(n.tipo for n in Notificacao.query.filter_by(user_id=u.id))
    assert tipos == ["estoque", "vacina"]
    pagina = client.get("/notificacoes").get_data(as_text=True)
    assert "Aftosa" in pagina and "Sal mineral" in pagina
    assert client.post("/notificacoes/marcar-lidas").status_code == 302
    assert Notificacao.query.filter_by(user_id=u.id, lida=False).count() == 0


def test_clima_usa_cache_e_mostra_ultimo_dado_quando_a_fonte_falha(app, monkeypatch):
    app.config["WEATHER_ENABLED"] = True
    hoje = date.today().isoformat()
    respostas = {
        "geocoding": {"results": [{"name": "Uberaba", "admin1": "Minas Gerais", "latitude": -19.7, "longitude": -47.9}]},
        "forecast": {"current": {"temperature_2m": 28.4, "relative_humidity_2m": 55, "precipitation": 0, "weather_code": 1,
                                 "wind_speed_10m": 9},
                     "daily": {"time": [hoje], "weather_code": [2], "temperature_2m_max": [31], "temperature_2m_min": [18],
                               "precipitation_sum": [0], "precipitation_probability_max": [10]}},
    }
    chamadas = []

    def fake(url):
        chamadas.append(url)
        return respostas["geocoding" if "geocoding" in url else "forecast"]
    monkeypatch.setattr(clima, "_get_json", fake)
    p = clima.previsao(municipio="Uberaba", uf="MG")
    assert p["ok"] and p["atual"]["temperatura"] == 28.4 and not p["cache_antigo"] and p["fonte"].startswith("Open-Meteo")
    clima.previsao(municipio="Uberaba", uf="MG")
    assert len(chamadas) == 2  # segunda consulta veio do cache

    cache = ClimaCache.query.filter(ClimaCache.chave.like("prev:%")).one()
    cache.obtido_em = cache.obtido_em - timedelta(hours=3)
    db.session.commit()

    def falha(url):
        raise urllib.error.URLError("sem rede")
    monkeypatch.setattr(clima, "_get_json", falha)
    p = clima.previsao(municipio="Uberaba", uf="MG")
    assert p["ok"] and p["cache_antigo"] is True  # mostra o último dado, identificado como antigo
    assert json.loads(cache.payload)["local"] == "Uberaba/MG"
