import glob
import os
import re

from app.extensions import db
from app.models import User

from conftest import SENHA, criar_usuario, login, logout


def test_cadastro_login_logout(client):
    r = client.post("/cadastro", data={"nome": "Maria Souza", "email": "Maria@Teste.com", "senha": SENHA, "confirmar": SENHA, "aceite": "1"})
    assert r.status_code == 302 and "/perfil-inicial" in r.headers["Location"]
    u = User.query.filter_by(email="maria@teste.com").first()
    assert u and u.senha_hash != SENHA and u.check_password(SENHA)
    client.post("/perfil-inicial", data={"papeis": ["produtor", "comprador"]})
    assert set(u.lista_papeis) == {"produtor", "comprador"}
    logout(client)
    assert client.get("/painel").status_code == 302
    assert login(client, "maria@teste.com").status_code == 302
    assert client.get("/painel").status_code == 200


def test_cadastro_valida_campos_e_email_duplicado(client):
    criar_usuario()
    r = client.post("/cadastro", data={"nome": "X", "email": "invalido", "senha": "123", "confirmar": "456"})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "E-mail inválido" in html and "8 caracteres" in html
    r = client.post("/cadastro", data={"nome": "Outro", "email": "vitor@teste.com", "senha": SENHA, "confirmar": SENHA, "aceite": "1"})
    assert "Não foi possível usar este e-mail" in r.get_data(as_text=True)


def test_nao_vira_admin_pelo_formulario(client):
    client.post("/cadastro", data={"nome": "Hacker Silva", "email": "h@teste.com", "senha": SENHA, "confirmar": SENHA,
                                   "aceite": "1", "is_admin": "1"})
    client.post("/conta/", data={"acao": "dados", "nome": "Hacker Silva", "is_admin": "1"})
    assert User.query.filter_by(email="h@teste.com").first().is_admin is False
    assert client.get("/admin/").status_code == 403


def test_login_mensagem_generica_e_limite_de_tentativas(client):
    criar_usuario()
    r1 = client.post("/entrar", data={"email": "vitor@teste.com", "senha": "errada"})
    r2 = client.post("/entrar", data={"email": "naoexiste@teste.com", "senha": "errada"})
    assert "E-mail ou senha incorretos" in r1.get_data(as_text=True)
    assert "E-mail ou senha incorretos" in r2.get_data(as_text=True)
    for _ in range(4):
        client.post("/entrar", data={"email": "vitor@teste.com", "senha": "errada"})
    r = client.post("/entrar", data={"email": "vitor@teste.com", "senha": SENHA})
    assert "Muitas tentativas" in r.get_data(as_text=True)


def test_recuperacao_de_senha_uso_unico(app, client):
    criar_usuario()
    r = client.post("/recuperar-senha", data={"email": "vitor@teste.com"})
    assert "Se houver uma conta" in r.get_data(as_text=True)
    arquivos = glob.glob(os.path.join(app.instance_path, "mail_outbox", "*.eml"))
    assert arquivos, "e-mail de recuperação não gravado"
    corpo = open(sorted(arquivos)[-1], encoding="utf-8").read().replace("=\n", "")
    token = re.search(r"/redefinir-senha/([A-Za-z0-9_\-.]+)", corpo).group(1)
    nova = "NovaSenha456"
    r = client.post(f"/redefinir-senha/{token}", data={"senha": nova, "confirmar": nova})
    assert r.status_code == 302
    assert login(client, "vitor@teste.com", nova).status_code == 302
    logout(client)
    r = client.get(f"/redefinir-senha/{token}")  # segundo uso do mesmo link
    assert r.status_code == 302 and "recuperar" in r.headers["Location"]


def test_troca_de_senha_encerra_outras_sessoes(app):
    criar_usuario()
    c1, c2 = app.test_client(), app.test_client()
    login(c1)
    login(c2)
    nova = "OutraSenha789"
    c1.post("/conta/senha", data={"atual": SENHA, "senha": nova, "confirmar": nova})
    assert c1.get("/painel").status_code == 200
    assert c2.get("/painel").status_code == 302  # sessão antiga invalidada


def test_conta_suspensa_nao_entra(client):
    u = criar_usuario()
    u.ativo = False
    db.session.commit()
    r = client.post("/entrar", data={"email": "vitor@teste.com", "senha": SENHA})
    assert "suspensa" in r.get_data(as_text=True)


def test_rotas_privadas_exigem_login(client):
    for url in ["/painel", "/rebanho/", "/financeiro/", "/mensagens/", "/admin/", "/marketplace/", "/api/offline/snapshot"]:
        assert client.get(url).status_code in (302, 401), url


def test_redirecionamento_aberto_bloqueado(client):
    criar_usuario()
    r = client.post("/entrar?next=//site-malicioso.com", data={"email": "vitor@teste.com", "senha": SENHA})
    assert "site-malicioso" not in r.headers["Location"]


def test_csrf_recusa_formulario_sem_token(app, client):
    criar_usuario()
    app.config["WTF_CSRF_ENABLED"] = True
    r = client.post("/entrar", data={"email": "vitor@teste.com", "senha": SENHA})
    assert r.status_code == 400 and "Formulário expirado" in r.get_data(as_text=True)
    r = client.post("/api/sync", json={})
    assert r.status_code == 400


def test_pagina_404_status_real(client):
    r = client.get("/pagina-que-nao-existe")
    html = r.get_data(as_text=True)
    assert r.status_code == 404
    assert "Página não encontrada" in html and "A página que você procura não existe ou foi movida." in html
    assert "Voltar ao início" in html and "Voltar à página anterior" in html
    assert client.get("/api/nao-existe").get_json()["erro"]


def test_pagina_500_sem_detalhes_tecnicos(app, client):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    r = client.get("/_teste/erro")
    html = r.get_data(as_text=True)
    assert r.status_code == 500
    assert "detalhe-interno-secreto" not in html and "Traceback" not in html
    assert "Algo deu errado" in html


def test_cabecalhos_de_seguranca(client, produtor):
    r = client.get("/painel")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "no-store" in r.headers["Cache-Control"]
