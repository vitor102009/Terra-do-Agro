"""Migrações em banco SQLite real (arquivo): dados existentes preservados no upgrade, downgrade reversível."""
import sqlite3

from flask_migrate import downgrade, upgrade

from app import create_app
from app.utils.esquema import estado_do_banco, pasta_migracoes
from config import TestConfig


def _app(tmp_path):
    caminho = tmp_path / "m.db"

    class Cfg(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{caminho.as_posix()}"
        UPLOAD_ROOT = str(tmp_path / "uploads")

    return create_app(Cfg), caminho


def test_upgrade_preserva_dados_e_downgrade_reverte(tmp_path):
    app, caminho = _app(tmp_path)
    with app.app_context():
        upgrade(directory=pasta_migracoes(app), revision="2cc9c1229360")
    con = sqlite3.connect(caminho)
    con.execute("insert into usuarios (nome,email,senha_hash,is_admin,ativo,email_verificado,session_version,is_demo,created_at,updated_at)"
                " values ('Fulano','f@t.com','x',0,1,1,1,0,'2026-01-01','2026-01-01')")
    con.execute("insert into tentativas_login (email,ip,sucesso,created_at) values ('f@t.com','1.1.1.1',0,'2026-01-01')")
    con.execute("insert into cotacoes (produto,praca,valor,unidade,fonte,data_referencia,created_at)"
                " values ('Boi gordo','SP',321.55,'R$/@','Fonte X','2026-01-02','2026-01-02')")
    con.commit()
    con.close()

    with app.app_context():
        upgrade(directory=pasta_migracoes(app))
        assert estado_do_banco(app)[0] is True
    con = sqlite3.connect(caminho)
    assert con.execute("select count(*) from usuarios").fetchone()[0] == 1
    assert con.execute("select count(*) from tentativas_login").fetchone()[0] == 1
    assert con.execute("select produto, valor, origem, moeda from cotacoes").fetchone() == ("Boi gordo", 321.55, "manual", "BRL")
    assert con.execute("pragma integrity_check").fetchone()[0] == "ok"
    con.close()

    with app.app_context():
        downgrade(directory=pasta_migracoes(app), revision="2cc9c1229360")
        upgrade(directory=pasta_migracoes(app))
        assert estado_do_banco(app)[0] is True
    con = sqlite3.connect(caminho)
    assert con.execute("select count(*) from cotacoes").fetchone()[0] == 1
    con.close()
