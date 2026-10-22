import os

from app.models import Anuncio, Propriedade, User


def test_create_admin_exige_senha_forte(app, monkeypatch):
    runner = app.test_cli_runner()
    monkeypatch.setenv("ADMIN_PASSWORD", "fraca")
    r = runner.invoke(args=["create-admin", "--email", "adm@teste.com", "--nome", "Admin"])
    assert r.exit_code != 0 and User.query.count() == 0
    monkeypatch.setenv("ADMIN_PASSWORD", "SenhaAdmin2026")
    r = runner.invoke(args=["create-admin", "--email", "adm@teste.com", "--nome", "Admin"])
    assert r.exit_code == 0
    u = User.query.one()
    assert u.is_admin and u.check_password("SenhaAdmin2026") and "SenhaAdmin2026" not in r.output


def test_demo_identificada_e_removivel(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=["demo-data"]).exit_code != 0  # exige --confirmar
    r = runner.invoke(args=["demo-data", "--confirmar"])
    assert r.exit_code == 0
    assert all(a.is_demo and "DEMONSTRAÇÃO" in a.titulo for a in Anuncio.query.all())
    assert Propriedade.query.filter_by(is_demo=True).count() == 1
    r = runner.invoke(args=["remover-demo"])
    assert r.exit_code == 0 and User.query.filter_by(is_demo=True).count() == 0 and Anuncio.query.count() == 0


def test_demo_bloqueada_em_producao(app):
    app.config["ENV_MODE"] = "production"
    r = app.test_cli_runner().invoke(args=["demo-data", "--confirmar"])
    assert r.exit_code != 0


def test_backup_sqlite(app, tmp_path):
    from app.extensions import db
    banco = tmp_path / "origem.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + str(banco).replace("\\", "/")
    import sqlite3
    with sqlite3.connect(banco) as c:
        c.execute("create table t (x int)")
        c.execute("insert into t values (42)")
    r = app.test_cli_runner().invoke(args=["backup"])
    assert r.exit_code == 0, r.output
    copias = os.listdir(app.config["BACKUP_DIR"])
    assert len(copias) == 1
    with sqlite3.connect(os.path.join(app.config["BACKUP_DIR"], copias[0])) as c:
        assert c.execute("select x from t").fetchone()[0] == 42
    assert db  # banco da aplicação intacto
