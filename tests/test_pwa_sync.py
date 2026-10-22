import json
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import Animal, EventoSanitario, Lancamento, OperacaoOffline, Pesagem, Tarefa
from app.utils.offline import chave_offline

from conftest import adicionar_membro, criar_usuario, hoje, login


def _animal(prop, brinco="OF1", status="ativo"):
    a = Animal(propriedade_id=prop.id, brinco=brinco, especie="bovino", sexo="F", data_entrada=date(2024, 1, 1), status=status)
    db.session.add(a)
    db.session.commit()
    return a


def _sync(client, prop, tipo, dados, uid=None, forcar=False):
    corpo = {"uuid": uid or str(uuid.uuid4()), "tipo": tipo, "propriedade_id": prop.id, "dados": dados, "forcar": forcar}
    r = client.post("/api/sync", json=corpo)
    return r, corpo["uuid"]


def test_manifest_e_service_worker(client):
    r = client.get("/manifest.webmanifest")
    m = json.loads(r.data)
    assert r.headers["Content-Type"].startswith("application/manifest+json")
    assert m["name"] == "Terra do Agro" and m["display"] == "standalone" and m["theme_color"] == "#073817"
    assert {"192x192", "512x512"} <= {i["sizes"] for i in m["icons"]}
    assert any(i["purpose"] == "maskable" for i in m["icons"])
    raiz = os.path.join(os.path.dirname(__file__), "..", "app")
    for i in m["icons"]:
        assert os.path.isfile(os.path.join(raiz, i["src"].lstrip("/"))), i["src"]
    sw = client.get("/service-worker.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["Content-Type"]
    assert sw.headers["Service-Worker-Allowed"] == "/" and "no-cache" in sw.headers["Cache-Control"]
    corpo = sw.get_data(as_text=True)
    assert "/offline" in corpo and "/static/css/terra.css" in corpo
    from app.main.routes import ARQUIVOS_OFFLINE
    for url in ARQUIVOS_OFFLINE:  # todo arquivo pré-armazenado precisa existir, senão a instalação falha
        assert client.get(url).status_code == 200, url


def test_paginas_offline_nao_contem_dados_do_usuario(client, produtor):
    for url in ("/offline", "/app-offline"):
        html = client.get(url).get_data(as_text=True)
        assert "Vitor" not in html and "csrf" not in html.lower() and "Fazenda Boa Vista" not in html


def test_snapshot_sem_dados_financeiros(client, produtor):
    u, prop = produtor
    _animal(prop)
    from app.models import CategoriaFinanceira
    db.session.add(Lancamento(propriedade_id=prop.id, tipo="despesa", descricao="Financeiro sigiloso", valor=Decimal("10"),
                              data=date.today(), categoria_id=CategoriaFinanceira.query.filter_by(slug="agua").one().id))
    db.session.commit()
    r = client.get("/api/offline/snapshot")
    assert "no-store" in r.headers["Cache-Control"]
    d = r.get_json()
    texto = json.dumps(d)
    assert d["propriedades"][0]["animais"][0]["brinco"] == "OF1"
    assert "sigiloso" not in texto and "valor" not in texto
    assert client.get("/api/csrf").get_json()["chave"] == chave_offline(u.id)


def test_sync_pesagem_idempotente(client, produtor):
    u, prop = produtor
    a = _animal(prop)
    dados = {"animal_id": a.id, "data": hoje(), "peso_kg": 412.5}
    r, uid = _sync(client, prop, "pesagem", dados)
    assert r.get_json()["status"] == "aplicada"
    for _ in range(3):  # reenvio após queda de conexão
        r, _ = _sync(client, prop, "pesagem", dados, uid=uid)
        assert r.get_json()["status"] == "duplicada"
    assert Pesagem.query.filter_by(animal_id=a.id).count() == 1
    assert a.peso_atual == Decimal("412.50")
    assert client.get(f"/api/sync/status?uuids={uid}").get_json() == {uid: "aplicada"}


def test_sync_conflito_e_forcar(client, produtor):
    u, prop = produtor
    a = _animal(prop)
    _sync(client, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": 400})
    r, uid = _sync(client, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": 405})
    d = r.get_json()
    assert d["status"] == "conflito" and d["servidor"]["peso_kg"] == 400.0
    assert Pesagem.query.count() == 1
    r, _ = _sync(client, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": 405}, uid=uid, forcar=True)
    assert r.get_json()["status"] == "aplicada" and Pesagem.query.count() == 2
    # Mesmo peso na mesma data = sem duplicar
    r, _ = _sync(client, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": 400})
    assert r.get_json()["status"] == "aplicada" and Pesagem.query.count() == 2
    # Animal vendido enquanto o aparelho estava offline
    vendido = _animal(prop, "OF2", status="vendido")
    r, _ = _sync(client, prop, "pesagem", {"animal_id": vendido.id, "data": hoje(), "peso_kg": 300})
    assert r.get_json()["status"] == "conflito" and r.get_json()["motivo"] == "animal_inativo"


def test_sync_validacoes_e_permissoes(app, client, produtor):
    u, prop = produtor
    a = _animal(prop)
    amanha = (date.today() + timedelta(days=5)).isoformat()
    r, uid = _sync(client, prop, "pesagem", {"animal_id": a.id, "data": amanha, "peso_kg": 300})
    assert r.get_json()["status"] == "rejeitada"
    r, _ = _sync(client, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": -1})
    assert r.get_json()["status"] == "rejeitada"
    assert client.post("/api/sync", json={"uuid": "nao-e-uuid", "tipo": "pesagem"}).status_code == 400
    # Usuário só de consulta não grava
    cons = criar_usuario(nome="Consulta", email="cons@teste.com")
    adicionar_membro(prop, cons, "consulta")
    c2 = app.test_client()
    login(c2, "cons@teste.com")
    r, _ = _sync(c2, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": 300})
    assert r.get_json()["status"] == "rejeitada"
    # UUID de outro usuário não é reaproveitado
    r = c2.post("/api/sync", json={"uuid": uid, "tipo": "pesagem", "propriedade_id": prop.id, "dados": {}})
    assert r.status_code == 409
    # Estranho à propriedade
    estranho = criar_usuario(nome="Estranho", email="x@teste.com")
    c3 = app.test_client()
    login(c3, "x@teste.com")
    r, _ = _sync(c3, prop, "pesagem", {"animal_id": a.id, "data": hoje(), "peso_kg": 300})
    assert r.get_json()["status"] == "rejeitada" and Pesagem.query.count() == 0
    assert estranho.id


def test_sync_sanitario_e_tarefa(client, produtor):
    u, prop = produtor
    a = _animal(prop)
    dados = {"tipo": "vacina", "produto": "Raiva", "alvo": "animal", "animal_id": a.id, "data_aplicacao": hoje(),
             "proxima_data": (date.today() + timedelta(days=365)).isoformat()}
    r, _ = _sync(client, prop, "sanitario", dados)
    assert r.get_json()["status"] == "aplicada"
    r, _ = _sync(client, prop, "sanitario", dados)
    assert r.get_json()["status"] == "conflito"
    assert EventoSanitario.query.count() == 1 and EventoSanitario.query.one().offline_uuid
    r, _ = _sync(client, prop, "tarefa", {"titulo": "Consertar cerca", "data": hoje()})
    assert r.get_json()["status"] == "aplicada" and Tarefa.query.count() == 1
    assert OperacaoOffline.query.filter_by(status="aplicada").count() == 2


def test_sync_exige_login(client):
    r = client.post("/api/sync", json={})
    assert r.status_code in (302, 401)
    assert client.get("/api/sessao").get_json() == {"autenticado": False}
