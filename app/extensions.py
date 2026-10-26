from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData

# Convenção de nomes para que as migrações do SQLite gerem restrições nomeadas.
naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(naming_convention=naming_convention))
migrate = Migrate()
csrf = CSRFProtect()
login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Entre na sua conta para acessar esta página."
login_manager.login_message_category = "warning"
# "basic": no celular o IP muda (Wi-Fi ↔ 4G); "strong" derrubava a sessão a cada troca de rede.
# A sessão continua protegida por cookie HttpOnly/SameSite e pela versão de sessão do usuário.
login_manager.session_protection = "basic"
