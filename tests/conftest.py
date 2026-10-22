import io
import os
import struct
import sys
import zlib
from datetime import date

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app  # noqa: E402
from app.cli import seed_base  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Propriedade, PropriedadeMembro, User  # noqa: E402
from config import TestConfig  # noqa: E402

SENHA = "SenhaForte123"


@pytest.fixture()
def app(tmp_path):
    class Cfg(TestConfig):
        UPLOAD_ROOT = str(tmp_path / "uploads")
        BACKUP_DIR = str(tmp_path / "backups")

    application = create_app(Cfg)
    application.instance_path = str(tmp_path)

    @application.teardown_request
    def _isolar_requisicoes(_exc):
        # Nos testes há um app context externo aberto; sem isto, o usuário logado de um cliente
        # ficaria em cache em `g` para as requisições de outros clientes.
        from flask import g
        g.pop("_login_user", None)

    @application.route("/_teste/erro")
    def _erro():  # rota usada apenas nos testes da página 500
        raise RuntimeError("detalhe-interno-secreto")

    with application.app_context():
        db.create_all()
        seed_base()
        yield application
        db.session.remove()
        db.engine.dispose()  # banco em memória: descartar o engine descarta os dados


@pytest.fixture()
def client(app):
    return app.test_client()


def criar_usuario(nome="Vitor Teste", email="vitor@teste.com", papeis=("produtor",), admin=False, senha=SENHA):
    u = User(nome=nome, email=email, is_admin=admin, email_verificado=True)
    u.set_password(senha)
    for p in papeis:
        u.add_role(p)
    db.session.add(u)
    db.session.commit()
    return u


def criar_propriedade(dono, nome="Fazenda Boa Vista", municipio="Uberaba", uf="MG"):
    p = Propriedade(owner_id=dono.id, nome=nome, municipio=municipio, uf=uf, atividade="pecuaria_corte")
    db.session.add(p)
    db.session.flush()
    db.session.add(PropriedadeMembro(propriedade_id=p.id, user_id=dono.id, papel="proprietario"))
    dono.propriedade_ativa_id = p.id
    db.session.commit()
    return p


def adicionar_membro(prop, user, papel):
    db.session.add(PropriedadeMembro(propriedade_id=prop.id, user_id=user.id, papel=papel))
    user.propriedade_ativa_id = prop.id
    db.session.commit()


def login(client, email="vitor@teste.com", senha=SENHA):
    return client.post("/entrar", data={"email": email, "senha": senha}, follow_redirects=False)


def logout(client):
    return client.post("/sair")


@pytest.fixture()
def produtor(app, client):
    u = criar_usuario()
    prop = criar_propriedade(u)
    login(client)
    return u, prop


def png_bytes():
    """PNG 1x1 válido."""
    def chunk(tipo, dados):
        return struct.pack(">I", len(dados)) + tipo + dados + struct.pack(">I", zlib.crc32(tipo + dados) & 0xFFFFFFFF)
    raw = b"\x00\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def arquivo(conteudo, nome):
    return (io.BytesIO(conteudo), nome)


def hoje():
    return date.today().isoformat()
