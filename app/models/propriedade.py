from app.extensions import db
from app.models.base import TimestampMixin, utcnow

ATIVIDADES = {
    "pecuaria_corte": "Pecuária de corte",
    "pecuaria_leite": "Pecuária leiteira",
    "pecuaria_mista": "Pecuária mista (corte e leite)",
    "agricultura": "Agricultura",
    "integracao": "Integração lavoura-pecuária",
    "ovinocaprinocultura": "Ovinocaprinocultura",
    "avicultura": "Avicultura",
    "suinocultura": "Suinocultura",
    "equinocultura": "Equinocultura",
    "outra": "Outra atividade",
}

# Permissões de cada papel de vínculo com a propriedade.
PAPEIS_PROPRIEDADE = {
    "proprietario": {"label": "Proprietário", "perms": {"ver", "editar", "financeiro", "membros", "excluir"}},
    "gerente": {"label": "Gerente", "perms": {"ver", "editar", "financeiro"}},
    "operador": {"label": "Operador (sem financeiro)", "perms": {"ver", "editar"}},
    "consulta": {"label": "Somente consulta", "perms": {"ver"}},
}


class Propriedade(TimestampMixin, db.Model):
    __tablename__ = "propriedades"

    id = db.Column(db.Integer, primary_key=True)
    owner_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=False, index=True)
    nome = db.Column(db.String(150), nullable=False)
    municipio = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2), nullable=False)
    area_total_ha = db.Column(db.Numeric(12, 2))
    area_pastagem_ha = db.Column(db.Numeric(12, 2))
    area_agricultura_ha = db.Column(db.Numeric(12, 2))
    atividade = db.Column(db.String(40), nullable=False, default="pecuaria_corte")
    tipo_producao = db.Column(db.String(150))
    qtd_animais_aprox = db.Column(db.Integer)
    informacoes = db.Column(db.Text)
    # Localização detalhada: privada, nunca exibida em páginas públicas.
    endereco = db.Column(db.String(255))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    arquivada = db.Column(db.Boolean, nullable=False, default=False)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)

    owner = db.relationship("User", foreign_keys=[owner_id])
    membros = db.relationship("PropriedadeMembro", back_populates="propriedade", cascade="all, delete-orphan")

    @property
    def atividade_label(self):
        return ATIVIDADES.get(self.atividade, self.atividade)


class PropriedadeMembro(db.Model):
    __tablename__ = "propriedade_membros"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "user_id", name="uq_membro_propriedade"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    papel = db.Column(db.String(20), nullable=False, default="consulta")
    adicionado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    propriedade = db.relationship("Propriedade", back_populates="membros")
    user = db.relationship("User", foreign_keys=[user_id])

    @property
    def perms(self):
        return PAPEIS_PROPRIEDADE.get(self.papel, {}).get("perms", set())

    @property
    def papel_label(self):
        return PAPEIS_PROPRIEDADE.get(self.papel, {}).get("label", self.papel)
