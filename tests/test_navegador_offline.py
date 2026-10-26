"""Teste ponta a ponta da PWA em navegador real (Microsoft Edge via Playwright).

Cobre: instalação do Service Worker, abertura offline, consulta de dados sincronizados, registro offline,
queda de conexão durante a sincronização (sem duplicar), reconexão e limpeza ao sair da conta.
É ignorado automaticamente se o Playwright ou o Edge não estiverem disponíveis.
"""
import threading
from datetime import date
from decimal import Decimal

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from app.cli import seed_base  # noqa: E402
from app.utils.esquema import pasta_migracoes  # noqa: E402
from flask_migrate import upgrade  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Animal, Pesagem, Propriedade, PropriedadeMembro, User  # noqa: E402
from config import Config  # noqa: E402

SENHA = "SenhaForte123"


@pytest.fixture()
def servidor(tmp_path):
    class Cfg(Config):
        SECRET_KEY = "chave-teste-navegador"
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "e2e.db").replace("\\", "/")
        UPLOAD_ROOT = str(tmp_path / "uploads")
        WEATHER_ENABLED = False
        QUOTES_BCB_ENABLED = False  # sem acesso à internet nos testes
        LOG_DIR = str(tmp_path / "logs")

    app = create_app(Cfg)
    with app.app_context():
        upgrade(directory=pasta_migracoes(app))  # mesmo caminho da produção (flask setup)
        seed_base()
        u = User(nome="Vitor Campo", email="campo@teste.com", email_verificado=True)
        u.set_password(SENHA)
        u.add_role("produtor")
        db.session.add(u)
        db.session.flush()
        p = Propriedade(owner_id=u.id, nome="Fazenda Offline", municipio="Uberaba", uf="MG", atividade="pecuaria_corte")
        db.session.add(p)
        db.session.flush()
        db.session.add(PropriedadeMembro(propriedade_id=p.id, user_id=u.id, papel="proprietario"))
        u.propriedade_ativa_id = p.id
        db.session.add(Animal(propriedade_id=p.id, brinco="OFF-77", especie="bovino", sexo="F", data_entrada=date(2024, 1, 1)))
        db.session.commit()
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield app, f"http://127.0.0.1:{srv.port}"
    srv.shutdown()


@pytest.fixture()
def navegador():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="msedge")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Microsoft Edge indisponível para o teste: {exc}")
        yield b
        b.close()


def _pesagens(app):
    with app.app_context():
        return Pesagem.query.all()


def test_pwa_offline_ponta_a_ponta(servidor, navegador):
    app, base = servidor
    ctx = navegador.new_context()
    page = ctx.new_page()
    erros = []
    page.on("pageerror", lambda e: erros.append(str(e)))
    page.on("console", lambda m: erros.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)

    # Login real (com CSRF ativo)
    page.goto(base + "/entrar")
    page.fill("#f-email", "campo@teste.com")
    page.fill("#f-senha", SENHA)
    page.click("button[type=submit]")
    page.wait_for_url("**/painel")
    assert "Olá, Vitor!" in page.content()

    # Páginas principais sem erros de JavaScript nem violações de CSP
    for url in ("/rebanho/", "/financeiro/", "/marketplace/", "/mensagens/", "/feed/", "/relatorios/", "/painel"):
        page.goto(base + url)
    assert not erros, erros

    # Service Worker instalado e ativo
    assert page.evaluate("navigator.serviceWorker.ready.then(r => !!r.active)")
    page.goto(base + "/app-offline")
    page.wait_for_selector("text=OFF-77", timeout=15000)

    # ---- Sem conexão: a casca abre do cache e os dados vêm do IndexedDB
    ctx.set_offline(True)
    page.reload()
    page.wait_for_selector("text=OFF-77", timeout=15000)
    assert page.inner_text("#off-conexao") == "Sem conexão"

    # Registro offline vai para a fila local
    page.click("[data-aba=registrar]")
    page.fill("#p-animal", "OFF-77")
    page.fill("#p-peso", "421,5")
    page.click("#f-pesagem button[type=submit]")
    page.wait_for_selector("text=Guardado neste aparelho")
    assert page.inner_text("#off-qtd-fila") == "1"
    assert _pesagens(app) == []

    # Página privada sem conexão mostra a página offline genérica
    page.goto(base + "/painel")
    assert "Você está sem conexão" in page.content()
    page.goto(base + "/app-offline")
    page.wait_for_selector("text=OFF-77")

    # ---- Conexão volta, mas cai durante o envio: o servidor grava, a resposta se perde
    estado = {"cortes": 0}

    def cortar_uma_vez(route):
        if estado["cortes"] == 0:
            estado["cortes"] += 1
            route.fetch()   # a requisição chega ao servidor e é gravada
            route.abort()   # mas a resposta não chega ao aparelho
        else:
            route.continue_()

    page.route("**/api/sync", cortar_uma_vez)
    ctx.set_offline(False)
    page.evaluate("window.dispatchEvent(new Event('online'))")
    page.wait_for_function("document.querySelector('#off-qtd-fila').textContent === '1'")
    page.click("[data-aba=fila]")
    page.wait_for_selector("text=Erro no envio", timeout=15000)
    assert len(_pesagens(app)) == 1  # gravada uma vez, item continua na fila até confirmação

    # Nova tentativa usa o mesmo UUID: servidor responde "duplicada" e a fila esvazia sem duplicar
    page.click("#off-sincronizar")
    page.wait_for_selector("text=Nenhum registro pendente", timeout=15000)
    pesagens = _pesagens(app)
    assert len(pesagens) == 1 and pesagens[0].peso_kg == Decimal("421.50") and pesagens[0].offline_uuid

    # ---- Ao sair da conta, os dados privados do aparelho são apagados
    page.goto(base + "/painel")
    page.click("button[aria-label='Menu da conta']")
    page.click("text=Sair")
    page.wait_for_url("**/entrar**")
    page.goto(base + "/app-offline")
    page.wait_for_selector("text=Modo offline não preparado")
    restantes = page.evaluate("indexedDB.databases ? indexedDB.databases().then(l => l.map(d => d.name)) : []")
    assert not [n for n in restantes if n.startswith("terra-offline-")]
    assert not erros, erros
    ctx.close()


def test_layout_responsivo_celular(servidor, navegador):
    app, base = servidor
    ctx = navegador.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(base + "/entrar")
    page.fill("#f-email", "campo@teste.com")
    page.fill("#f-senha", SENHA)
    page.click("button[type=submit]")
    page.wait_for_url("**/painel")
    # Sem rolagem horizontal, menu inferior visível e menu lateral acessível
    largura = page.evaluate("document.documentElement.scrollWidth")
    assert largura <= 390, largura
    assert page.is_visible(".bottom-nav")
    page.click(".bottom-nav button[data-sidebar-open]")
    page.wait_for_selector("#sidebar.open")
    assert page.is_visible("#sidebar >> text=Financeiro")
    for url in ("/rebanho/", "/marketplace/", "/financeiro/", "/mensagens/"):
        page.goto(base + url)
        assert page.evaluate("document.documentElement.scrollWidth") <= 392, url
    ctx.close()


def _entrar_celular(navegador, base, largura=360):
    ctx = navegador.new_context(viewport={"width": largura, "height": 780}, is_mobile=True, has_touch=True)
    page = ctx.new_page()
    page.goto(base + "/entrar")
    page.fill("#f-email", "campo@teste.com")
    page.fill("#f-senha", SENHA)
    page.click("button[type=submit]")
    page.wait_for_url("**/painel")
    return ctx, page


def test_cotacoes_atualizam_a_cada_3s_no_celular(servidor, navegador):
    from app.models import Cotacao

    app, base = servidor
    with app.app_context():
        db.session.add(Cotacao(produto="Boi gordo", praca="Estado de SP", uf="SP", valor=Decimal("300.00"), unidade="R$/@",
                               fonte="Fonte de teste", data_referencia=date.today()))
        db.session.commit()
    ctx, page = _entrar_celular(navegador, base)
    consultas = []
    page.on("request", lambda r: consultas.append(r.url) if "/cotacoes/dados.json" in r.url else None)
    page.goto(base + "/cotacoes")
    assert page.inner_text(".quote-card .quote-value") == "R$ 300,00"
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    page.wait_for_timeout(7000)
    assert 2 <= len(consultas) <= 3, consultas  # ~1 consulta a cada 3 s, sem rajadas
    with app.app_context():  # a "fonte" publica um valor novo: a tela muda sem recarregar
        c = Cotacao.query.first()
        c.valor = Decimal("305.50")
        db.session.commit()
    for _ in range(16):  # sem eval de string: a CSP do site bloqueia 'unsafe-eval'
        if page.inner_text(".quote-card .quote-value") == "R$ 305,50":
            break
        page.wait_for_timeout(500)
    assert page.inner_text(".quote-card .quote-value") == "R$ 305,50"
    assert "última verificação" in page.inner_text("[data-status-texto]")
    ctx.close()


def test_ia_agro_responsiva_sem_chave(servidor, navegador):
    _app, base = servidor
    ctx, page = _entrar_celular(navegador, base)
    page.goto(base + "/ia/")
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert page.is_visible("text=A IA Agro ainda não está disponível neste servidor.")
    assert not page.is_visible("text=ANTHROPIC_API_KEY")  # detalhe técnico só para administradores
    assert page.is_disabled("#ia-pergunta")
    ctx.close()


def test_ia_agro_interface_completa_com_provedor_simulado(servidor, navegador, monkeypatch):
    """Interface de ponta a ponta no navegador. O PROVEDOR É SIMULADO: valida a tela, não a Anthropic."""
    from types import SimpleNamespace
    from app.services import ia_agro

    app, base = servidor
    app.config["ANTHROPIC_API_KEY"] = "chave-ficticia-de-teste"
    ia_agro._status.update(estado=None, em=None)
    respostas = iter(["Você tem 1 animal ativo: OFF-77.", "Segunda conversa respondida."] * 2)

    def criar(**_kw):
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=next(respostas))],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))
    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=criar)),
                           models=SimpleNamespace(retrieve=lambda m: SimpleNamespace(id=m)))
    fake.with_options = lambda **_k: fake
    monkeypatch.setattr(ia_agro, "cliente", lambda: fake)
    try:
        for largura in (360, 1280):
            ctx, page = _entrar_celular(navegador, base, largura)
            page.goto(base + "/ia/")
            assert not page.is_visible(".alert-warning")  # integração validada: sem aviso
            restantes = int(page.inner_text("[data-restantes]"))
            page.click("[data-sugestao] >> nth=0")
            page.wait_for_selector(".ia-msg.assistant >> text=OFF-77")
            assert int(page.inner_text("[data-restantes]")) == restantes - 1
            assert "/ia/conversa/" in page.url
            url_conversa = page.url
            page.goto(base + "/ia/")  # nova conversa
            page.fill("#ia-pergunta", "Outra pergunta")
            page.click("[data-enviar]")
            page.wait_for_selector("text=Segunda conversa respondida.")
            assert page.url != url_conversa
            page.goto(url_conversa)  # histórico recuperado do banco
            assert page.is_visible(".ia-msg.user") and page.is_visible("text=OFF-77")
            assert page.evaluate("document.documentElement.scrollWidth") <= largura
            ctx.close()
    finally:
        ia_agro._status.update(estado=None, em=None)
