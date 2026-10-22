from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db
from app.models.base import TimestampMixin, utcnow

PAPEIS_USUARIO = {
    "produtor": "Produtor rural",
    "profissional": "Profissional do agro",
    "trabalhador": "Trabalhador rural",
    "empresa": "Loja, empresa ou fornecedor",
    "comprador": "Comprador ou investidor",
}


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "usuarios"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(180), nullable=False, unique=True, index=True)
    senha_hash = db.Column(db.String(255), nullable=False)
    telefone = db.Column(db.String(30))  # privado
    municipio = db.Column(db.String(120))
    uf = db.Column(db.String(2))
    bio = db.Column(db.Text)
    foto = db.Column(db.String(255))

    # Somente alterável pela CLI ou por outro administrador. Nunca por formulário do próprio usuário.
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    ativo = db.Column(db.Boolean, nullable=False, default=True)
    suspenso_motivo = db.Column(db.String(255))
    email_verificado = db.Column(db.Boolean, nullable=False, default=False)
    session_version = db.Column(db.Integer, nullable=False, default=1)
    ultimo_login = db.Column(db.DateTime)
    aceite_termos_em = db.Column(db.DateTime)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)
    propriedade_ativa_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", use_alter=True))

    papeis = db.relationship("UserRole", back_populates="user", cascade="all, delete-orphan")

    def set_password(self, senha):
        self.senha_hash = generate_password_hash(senha)
        self.session_version = (self.session_version or 0) + 1

    def check_password(self, senha):
        return check_password_hash(self.senha_hash, senha)

    def get_id(self):
        # A versão da sessão invalida logins antigos após troca de senha ou suspensão.
        return f"{self.id}:{self.session_version}"

    @property
    def is_active(self):
        return bool(self.ativo)

    @property
    def lista_papeis(self):
        return [p.papel for p in self.papeis]

    def has_role(self, papel):
        return papel in self.lista_papeis

    def add_role(self, papel):
        if papel in PAPEIS_USUARIO and not self.has_role(papel):
            self.papeis.append(UserRole(papel=papel))

    @property
    def primeiro_nome(self):
        return (self.nome or "").split(" ")[0]

    @property
    def iniciais(self):
        partes = [p for p in (self.nome or "").split() if p]
        if not partes:
            return "?"
        if len(partes) == 1:
            return partes[0][:2].upper()
        return (partes[0][0] + partes[-1][0]).upper()

    def __repr__(self):
        return f"<User {self.id} {self.email}>"


class UserRole(db.Model):
    __tablename__ = "usuario_papeis"
    __table_args__ = (db.UniqueConstraint("user_id", "papel", name="uq_usuario_papel"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    papel = db.Column(db.String(30), nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    user = db.relationship("User", back_populates="papeis")


class LoginAttempt(db.Model):
    __tablename__ = "tentativas_login"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(180), index=True)
    ip = db.Column(db.String(64), index=True)
    sucesso = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
