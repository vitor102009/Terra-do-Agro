"""Varredura: todas as páginas GET sem parâmetros devem responder sem erro para um usuário com dados."""
from app.cli import criar_demo
from app.extensions import db
from app.models import (
    Animal, Anuncio, EventoSanitario, Grupo, Insumo, Lancamento, Lote, PerfilProfissional, Post, User, Vaga,
)

from conftest import login


def _demo_logado(app, client, admin=False):
    criar_demo("SenhaDemo123")
    u = User.query.filter_by(email="demo.produtor@exemplo.com.br").first()
    if admin:
        u.is_admin = True
        db.session.commit()
    login(client, u.email, "SenhaDemo123")
    return u


def test_todas_as_paginas_sem_parametros(app, client):
    _demo_logado(app, client, admin=True)
    ignorar = {"/sair", "/service-worker.js"}
    falhas = []
    for regra in app.url_map.iter_rules():
        if "GET" not in regra.methods or regra.arguments or regra.rule in ignorar or regra.rule.startswith("/static"):
            continue
        if regra.rule.startswith("/_teste"):
            continue
        resp = client.get(regra.rule)
        if resp.status_code >= 500:
            falhas.append((regra.rule, resp.status_code))
    assert not falhas, falhas


def test_paginas_de_detalhe(app, client):
    _demo_logado(app, client, admin=True)
    a = Animal.query.first()
    lote = Lote.query.first()
    an = Anuncio.query.first()
    perfil = PerfilProfissional.query.first()
    vaga = Vaga.query.first()
    ev = EventoSanitario.query.first()
    ins = Insumo.query.first()
    lanc = Lancamento.query.first()
    grupo = Grupo.query.first()
    urls = [
        f"/rebanho/{a.id}", f"/rebanho/{a.id}/editar", f"/rebanho/{a.id}/movimentar", f"/lotes/{lote.id}", f"/lotes/{lote.id}/editar",
        f"/marketplace/{an.id}", f"/marketplace/{an.id}/editar", f"/profissionais/perfil/{perfil.id}", f"/vagas/{vaga.id}",
        f"/vagas/{vaga.id}/editar", f"/sanidade/{ev.id}", f"/insumos/{ins.id}", f"/insumos/{ins.id}/editar",
        f"/financeiro/{lanc.id}", f"/financeiro/{lanc.id}/editar", f"/comunidade/{grupo.id}", f"/comunidade/{grupo.id}/membros",
        "/financeiro/novo/receita", "/financeiro/novo/despesa", "/financeiro/contas/pagar", "/financeiro/contas/receber",
        "/pesagens/nova?modo=lote", f"/pesagens/nova?modo=planilha&lote_id={lote.id}", "/sanidade/?aba=agenda",
        "/sanidade/?aba=consultas", "/reproducao/?aba=eventos", "/lotes/?aba=pastagens", f"/feed/usuario/{a.criado_por}",
        f"/servicos/nova?perfil={perfil.id}", "/painel?periodo=ano", "/busca?q=D00", "/clima/resumo.json",
        "/admin/denuncias?status=procedente", "/admin/conteudos?aba=vagas", "/admin/conteudos?aba=grupos", "/admin/conteudos?aba=avaliacoes",
    ]
    from app.relatorios.routes import RELATORIOS
    urls += [f"/relatorios/{k}" for k in RELATORIOS] + [f"/relatorios/{k}?formato=csv" for k in RELATORIOS]
    falhas = [(u, r.status_code) for u in urls for r in [client.get(u)] if r.status_code != 200]
    assert not falhas, falhas


def test_post_publicado_no_feed_renderiza(app, client):
    _demo_logado(app, client)
    r = client.post("/feed/publicar", data={"texto": "Chuva boa na região", "tipo": "geral"}, follow_redirects=True)
    assert r.status_code == 200 and "Chuva boa na região" in r.get_data(as_text=True)
    p = Post.query.first()
    assert client.get(f"/feed/{p.id}").status_code == 200
