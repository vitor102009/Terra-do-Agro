"""Login contra um banco SQLite real em arquivo, criado pelas migrações (não por create_all).

Reproduz o erro "no such table: tentativas_login": banco vazio (clone novo sem `flask setup`).
"""
import sqlite3

import pytest

from app import create_app
from app.extensions import db
from app.models import LoginAttempt, User
from config import TestConfig

SENHA = "SenhaForte123"


def _app(tmp_path, **extra):
    caminho = tmp_path / "terradoagro.db"

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{caminho.as_posix()}"
        SCHEMA_CHECK = True
        UPLOAD_ROOT = str(tmp_path / "uploads")
        BACKUP_DIR = str(tmp_path / "backups")

    for k, v in extra.items():
        setattr(Cfg, k, v)
    return create_app(Cfg), caminho


def _setup(app):
    resultado = app.test_cli_runner().invoke(args=["setup"])
    assert resultado.exit_code == 0, resultado.output
    return resultado


def _criar_usuario(app, email="produtor@teste.com"):
    with app.app_context():
        u = User(nome="Produtor Teste", email=email, email_verificado=True)
        u.set_password(SENHA)
        u.add_role("produtor")
        db.session.add(u)
        db.session.commit()


def _entrar(client, senha=SENHA, email="produtor@teste.com", **kw):
    return client.post("/entrar", data={"email": email, "senha": senha}, **kw)


def test_banco_vazio_responde_503_e_nao_500(tmp_path, caplog):
    app, caminho = _app(tmp_path)
    caminho.write_bytes(b"")  # mesmo estado encontrado em instance/terradoagro.db (0 bytes)
    client = app.test_client()
    resp = _entrar(client)
    assert resp.status_code == 503
    assert "manutenção" in resp.get_data(as_text=True)
    assert "tentativas_login" not in resp.get_data(as_text=True)  # nada de detalhe interno na página
    assert "flask --app app setup" in caplog.text  # o administrador vê a causa no log


def test_setup_cria_tabelas_e_login_volta_a_funcionar(tmp_path):
    app, caminho = _app(tmp_path)
    caminho.write_bytes(b"")
    client = app.test_client()
    assert _entrar(client).status_code == 503

    _setup(app)  # sem reiniciar o servidor
    _criar_usuario(app)
    resp = _entrar(client)
    assert resp.status_code == 302 and "/painel" in resp.headers["Location"]
    assert client.get("/painel").status_code == 200
    with app.app_context():
        assert LoginAttempt.query.filter_by(email="produtor@teste.com", sucesso=True).count() == 1


def test_senha_incorreta_registra_tentativa_e_bloqueia(tmp_path):
    app, _ = _app(tmp_path)
    _setup(app)
    _criar_usuario(app)
    client = app.test_client()
    resp = _entrar(client, senha="errada123")
    assert resp.status_code == 200 and "E-mail ou senha incorretos." in resp.get_data(as_text=True)
    for _ in range(4):
        _entrar(client, senha="errada123")
    with app.app_context():
        assert LoginAttempt.query.filter_by(sucesso=False).count() == 5
    resp = _entrar(client)  # senha certa, mas dentro do bloqueio
    assert "Muitas tentativas" in resp.get_data(as_text=True)
    assert client.get("/painel").status_code == 302


def test_setup_repetido_preserva_dados(tmp_path):
    app, caminho = _app(tmp_path)
    _setup(app)
    _criar_usuario(app)
    _setup(app)  # idempotente: nada é apagado
    con = sqlite3.connect(caminho)
    try:
        assert con.execute("select count(*) from usuarios").fetchone()[0] == 1
    finally:
        con.close()
    assert _entrar(app.test_client()).status_code == 302


def test_proxy_confiavel_usa_ip_real_do_cliente(tmp_path):
    app, _ = _app(tmp_path, TRUST_PROXY_HEADERS=True)
    _setup(app)
    _criar_usuario(app)
    client = app.test_client()
    # Cinco falhas de um cliente não podem bloquear outro cliente atrás do mesmo proxy.
    for _ in range(5):
        _entrar(client, senha="errada123", email="outra@teste.com", headers={"X-Forwarded-For": "203.0.113.7"})
    with app.app_context():
        assert {a.ip for a in LoginAttempt.query.all()} == {"203.0.113.7"}
    resp = _entrar(client, headers={"X-Forwarded-For": "198.51.100.9"})
    assert resp.status_code == 302


def test_sessao_sobrevive_a_troca_de_rede_no_celular(tmp_path):
    app, _ = _app(tmp_path, TRUST_PROXY_HEADERS=True)
    _setup(app)
    _criar_usuario(app)
    client = app.test_client()
    ua = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130 Mobile Safari/537.36"}
    assert _entrar(client, headers={**ua, "X-Forwarded-For": "203.0.113.7"}).status_code == 302
    resp = client.get("/painel", headers={**ua, "X-Forwarded-For": "198.51.100.20"})  # Wi-Fi → 4G
    assert resp.status_code == 200


@pytest.mark.parametrize("email", ["  Produtor@Teste.com ", "PRODUTOR@TESTE.COM"])
def test_teclado_do_celular_maiusculas_e_espacos(tmp_path, email):
    app, _ = _app(tmp_path)
    _setup(app)
    _criar_usuario(app)
    assert _entrar(app.test_client(), email=email).status_code == 302
