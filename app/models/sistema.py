from app.extensions import db
from app.models.base import TimestampMixin, utcnow


class Notificacao(db.Model):
    __tablename__ = "notificacoes"
    __table_args__ = (db.UniqueConstraint("user_id", "chave", name="uq_notificacao_chave"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(30), nullable=False)
    titulo = db.Column(db.String(150), nullable=False)
    mensagem = db.Column(db.String(500))
    link = db.Column(db.String(255))
    chave = db.Column(db.String(120))  # evita alertas repetidos para o mesmo evento
    lida = db.Column(db.Boolean, nullable=False, default=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


TIPOS_TAREFA = {"tarefa": "Tarefa", "compromisso": "Compromisso", "atendimento": "Atendimento", "pesagem": "Pesagem programada"}


class Tarefa(TimestampMixin, db.Model):
    __tablename__ = "tarefas"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), index=True)
    responsavel_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"))
    tipo = db.Column(db.String(15), nullable=False, default="tarefa")
    titulo = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.Text)
    data = db.Column(db.Date, nullable=False, index=True)
    hora = db.Column(db.Time)
    status = db.Column(db.String(12), nullable=False, default="pendente", index=True)  # pendente, concluida, cancelada
    concluida_em = db.Column(db.DateTime)
    offline_uuid = db.Column(db.String(36), unique=True)

    user = db.relationship("User", foreign_keys=[user_id])
    responsavel = db.relationship("User", foreign_keys=[responsavel_id])
    propriedade = db.relationship("Propriedade")


ALVOS_DENUNCIA = {
    "anuncio": "Anúncio",
    "post": "Publicação",
    "comentario": "Comentário",
    "usuario": "Usuário",
    "grupo": "Grupo",
    "perfil": "Perfil profissional",
    "vaga": "Vaga",
    "mensagem": "Mensagem",
    "avaliacao": "Avaliação",
}

MOTIVOS_DENUNCIA = {
    "fraude": "Fraude ou golpe",
    "falso": "Informação falsa ou enganosa",
    "ofensivo": "Conteúdo ofensivo ou discriminatório",
    "spam": "Spam ou propaganda indevida",
    "ilegal": "Produto ou serviço ilegal",
    "privacidade": "Exposição de dados pessoais",
    "outro": "Outro motivo",
}


class Denuncia(TimestampMixin, db.Model):
    __tablename__ = "denuncias"

    id = db.Column(db.Integer, primary_key=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"), index=True)
    alvo_tipo = db.Column(db.String(20), nullable=False)
    alvo_id = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(20), nullable=False)
    descricao = db.Column(db.Text)
    status = db.Column(db.String(15), nullable=False, default="aberta", index=True)  # aberta, procedente, improcedente
    resolvido_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    nota_resolucao = db.Column(db.Text)

    autor = db.relationship("User", foreign_keys=[autor_id])


class AuditLog(db.Model):
    """Registro de atividades. Também alimenta as 'atividades recentes' do dashboard."""

    __tablename__ = "auditoria"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"), index=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), index=True)
    acao = db.Column(db.String(60), nullable=False, index=True)
    descricao = db.Column(db.String(300), nullable=False)
    link = db.Column(db.String(255))
    administrativa = db.Column(db.Boolean, nullable=False, default=False, index=True)
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    user = db.relationship("User")


TIPOS_CHAMADO = {
    "duvida": "Dúvida",
    "problema": "Problema técnico",
    "sugestao": "Sugestão",
    "lgpd_acesso": "LGPD: acesso aos meus dados",
    "lgpd_correcao": "LGPD: correção de dados",
    "lgpd_exclusao": "LGPD: exclusão de dados / conta",
}


class ChamadoSuporte(TimestampMixin, db.Model):
    __tablename__ = "chamados_suporte"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False, default="duvida")
    assunto = db.Column(db.String(150), nullable=False)
    mensagem = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(15), nullable=False, default="aberto", index=True)  # aberto, respondido, encerrado
    resposta = db.Column(db.Text)
    respondido_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    respondido_em = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id])


class Configuracao(db.Model):
    __tablename__ = "configuracoes"

    chave = db.Column(db.String(60), primary_key=True)
    valor = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Cotacao(db.Model):
    """Cotação de uma fonte identificada: registrada pelo administrador (origem "manual") ou coletada
    automaticamente de uma fonte oficial (ex.: "bcb"). Nunca é gerada pelo sistema."""

    __tablename__ = "cotacoes"
    __table_args__ = (db.UniqueConstraint("serie", "ref_externa", name="uq_cotacoes_serie_ref"),)

    id = db.Column(db.Integer, primary_key=True)
    produto = db.Column(db.String(80), nullable=False, index=True)
    praca = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2))
    valor = db.Column(db.Numeric(16, 4), nullable=False)
    unidade = db.Column(db.String(40), nullable=False)
    moeda = db.Column(db.String(3), nullable=False, default="BRL", server_default="BRL")
    fonte = db.Column(db.String(150), nullable=False)
    fonte_url = db.Column(db.String(255))
    data_referencia = db.Column(db.Date, nullable=False, index=True)
    # Coleta automática: série da fonte (ex.: "bcb:USD"), identificador do dado na fonte (evita duplicar),
    # horário publicado pela fonte (quando ela informa) e horário em que o Terra do Agro coletou.
    origem = db.Column(db.String(20), nullable=False, default="manual", server_default="manual", index=True)
    serie = db.Column(db.String(60), index=True)
    ref_externa = db.Column(db.String(60))
    publicado_em = db.Column(db.DateTime)
    coletado_em = db.Column(db.DateTime)
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


class FonteCotacao(db.Model):
    """Estado da coleta de cada fonte automática (controle de frequência e de falhas)."""

    __tablename__ = "fontes_cotacao"

    slug = db.Column(db.String(30), primary_key=True)
    proxima_em = db.Column(db.DateTime)
    ultima_tentativa = db.Column(db.DateTime)
    ultimo_sucesso = db.Column(db.DateTime)
    ultimo_erro = db.Column(db.String(255))
    falhas_seguidas = db.Column(db.Integer, nullable=False, default=0, server_default="0")


class ConversaIA(TimestampMixin, db.Model):
    """Conversa com a IA Agro. Pertence a um usuário e, quando houver, à propriedade ativa no início."""

    __tablename__ = "ia_conversas"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="SET NULL"), index=True)
    titulo = db.Column(db.String(120), nullable=False, default="Nova conversa")

    mensagens = db.relationship("MensagemIA", back_populates="conversa", cascade="all, delete-orphan",
                                order_by="MensagemIA.id")


class MensagemIA(db.Model):
    __tablename__ = "ia_mensagens"

    id = db.Column(db.Integer, primary_key=True)
    conversa_id = db.Column(db.Integer, db.ForeignKey("ia_conversas.id", ondelete="CASCADE"), nullable=False, index=True)
    papel = db.Column(db.String(10), nullable=False)  # user | assistant
    conteudo = db.Column(db.Text, nullable=False)
    ferramentas = db.Column(db.String(500))  # consultas feitas aos dados (somente os nomes)
    tokens_entrada = db.Column(db.Integer)
    tokens_saida = db.Column(db.Integer)
    erro = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    conversa = db.relationship("ConversaIA", back_populates="mensagens")


class ClimaCache(db.Model):
    __tablename__ = "clima_cache"

    id = db.Column(db.Integer, primary_key=True)
    chave = db.Column(db.String(120), nullable=False, unique=True)
    payload = db.Column(db.Text, nullable=False)
    fonte = db.Column(db.String(120), nullable=False)
    obtido_em = db.Column(db.DateTime, nullable=False)


class OperacaoOffline(db.Model):
    """Registro idempotente de operações enviadas pela fila offline do aplicativo."""

    __tablename__ = "operacoes_offline"

    id = db.Column(db.Integer, primary_key=True)
    uuid = db.Column(db.String(36), nullable=False, unique=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(15), nullable=False)  # aplicada, conflito, rejeitada
    resultado = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
