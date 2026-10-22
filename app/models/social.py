from app.extensions import db
from app.models.base import TimestampMixin, utcnow


class Conversa(db.Model):
    """Conversa individual. user_a_id é sempre o menor id para garantir unicidade do par."""

    __tablename__ = "conversas"
    __table_args__ = (
        db.UniqueConstraint("user_a_id", "user_b_id", name="uq_conversa_par"),
        db.CheckConstraint("user_a_id < user_b_id", name="ordem_par"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_a_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    user_b_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    ultima_mensagem_em = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user_a = db.relationship("User", foreign_keys=[user_a_id])
    user_b = db.relationship("User", foreign_keys=[user_b_id])
    mensagens = db.relationship("Mensagem", back_populates="conversa", cascade="all, delete-orphan",
                                lazy="dynamic", order_by="Mensagem.id")

    def participa(self, user_id):
        return user_id in (self.user_a_id, self.user_b_id)

    def outro(self, user_id):
        return self.user_b if user_id == self.user_a_id else self.user_a


class Mensagem(db.Model):
    __tablename__ = "mensagens"

    id = db.Column(db.Integer, primary_key=True)
    conversa_id = db.Column(db.Integer, db.ForeignKey("conversas.id", ondelete="CASCADE"), nullable=False, index=True)
    remetente_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    anuncio_id = db.Column(db.Integer, db.ForeignKey("anuncios.id", ondelete="SET NULL"))
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("solicitacoes_servico.id", ondelete="SET NULL"))
    vaga_id = db.Column(db.Integer, db.ForeignKey("vagas.id", ondelete="SET NULL"))
    client_uuid = db.Column(db.String(36), unique=True)  # evita envio duplicado em reenvios
    lida_em = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    conversa = db.relationship("Conversa", back_populates="mensagens")
    remetente = db.relationship("User")
    anuncio = db.relationship("Anuncio")
    solicitacao = db.relationship("SolicitacaoServico")
    vaga = db.relationship("Vaga")


class Bloqueio(db.Model):
    __tablename__ = "bloqueios"
    __table_args__ = (db.UniqueConstraint("user_id", "bloqueado_id", name="uq_bloqueio"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    bloqueado_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


TEMAS_GRUPO = {
    "corte": "Pecuária de corte",
    "leite": "Pecuária leiteira",
    "agricultura": "Agricultura",
    "reproducao": "Reprodução bovina",
    "nutricao": "Nutrição animal",
    "maquinas": "Máquinas agrícolas",
    "mercado": "Mercado agropecuário",
    "gestao": "Gestão rural",
    "emprego": "Emprego rural",
    "veterinaria": "Veterinária",
    "assistencia": "Assistência técnica",
    "regional": "Regiões e municípios",
    "outro": "Outros temas",
}

PAPEIS_GRUPO = {"admin": "Administrador", "moderador": "Moderador", "membro": "Participante"}


class Grupo(TimestampMixin, db.Model):
    __tablename__ = "grupos"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(120), nullable=False, unique=True)
    descricao = db.Column(db.Text, nullable=False)
    tema = db.Column(db.String(20), nullable=False, default="outro", index=True)
    capa = db.Column(db.String(255))
    regras = db.Column(db.Text)
    municipio = db.Column(db.String(120))
    uf = db.Column(db.String(2))
    criado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    membros = db.relationship("GrupoMembro", back_populates="grupo", cascade="all, delete-orphan")

    @property
    def tema_label(self):
        return TEMAS_GRUPO.get(self.tema, self.tema)


class GrupoMembro(db.Model):
    __tablename__ = "grupo_membros"
    __table_args__ = (db.UniqueConstraint("grupo_id", "user_id", name="uq_grupo_membro"),)

    id = db.Column(db.Integer, primary_key=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    papel = db.Column(db.String(15), nullable=False, default="membro")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    grupo = db.relationship("Grupo", back_populates="membros")
    user = db.relationship("User")


TIPOS_POST = {
    "geral": "Publicação",
    "experiencia": "Experiência de manejo",
    "dica": "Dica profissional",
    "propriedade": "Sobre a propriedade",
    "educativo": "Conteúdo educativo",
    "noticia": "Notícia do setor",
    "comercial": "Comercial (publicidade)",
}


class Post(TimestampMixin, db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    grupo_id = db.Column(db.Integer, db.ForeignKey("grupos.id", ondelete="CASCADE"), index=True)
    tipo = db.Column(db.String(15), nullable=False, default="geral")
    texto = db.Column(db.Text)
    midia = db.Column(db.String(255))
    midia_tipo = db.Column(db.String(10))  # imagem ou video
    compartilhado_id = db.Column(db.Integer, db.ForeignKey("posts.id", ondelete="SET NULL"))
    editado = db.Column(db.Boolean, nullable=False, default=False)
    removido = db.Column(db.Boolean, nullable=False, default=False, index=True)
    removido_motivo = db.Column(db.String(255))

    autor = db.relationship("User")
    grupo = db.relationship("Grupo")
    compartilhado = db.relationship("Post", remote_side=[id])
    comentarios = db.relationship("Comentario", back_populates="post", cascade="all, delete-orphan",
                                  order_by="Comentario.created_at")
    reacoes = db.relationship("Reacao", back_populates="post", cascade="all, delete-orphan")

    @property
    def tipo_label(self):
        return TIPOS_POST.get(self.tipo, self.tipo)


class Comentario(db.Model):
    __tablename__ = "comentarios"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    removido = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    post = db.relationship("Post", back_populates="comentarios")
    autor = db.relationship("User")


class Reacao(db.Model):
    __tablename__ = "reacoes"
    __table_args__ = (db.UniqueConstraint("post_id", "user_id", name="uq_reacao"),)

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    tipo = db.Column(db.String(15), nullable=False, default="curtir")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    post = db.relationship("Post", back_populates="reacoes")
