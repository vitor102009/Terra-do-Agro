from app.extensions import db
from app.models.base import Money, TimestampMixin, utcnow

CATEGORIAS_ANUNCIO = {
    "animais": "Animais",
    "maquinas": "Máquinas e implementos",
    "racoes": "Rações e suplementos",
    "sementes": "Sementes e mudas",
    "insumos": "Insumos agrícolas",
    "equipamentos": "Equipamentos rurais",
    "ferramentas": "Ferramentas",
    "produtos": "Produtos agropecuários",
    "servicos_profissionais": "Serviços profissionais",
    "servicos_mecanizados": "Serviços mecanizados",
    "transporte": "Transporte",
    "outros": "Outros produtos e serviços rurais",
}

ICONES_CATEGORIA_ANUNCIO = {
    "animais": "bi-tags",
    "maquinas": "bi-truck-front",
    "racoes": "bi-basket",
    "sementes": "bi-flower1",
    "insumos": "bi-droplet",
    "equipamentos": "bi-tools",
    "ferramentas": "bi-hammer",
    "produtos": "bi-box-seam",
    "servicos_profissionais": "bi-person-badge",
    "servicos_mecanizados": "bi-gear-wide-connected",
    "transporte": "bi-truck",
    "outros": "bi-grid",
}

TIPOS_PRECO = {"fixo": "Preço fixo", "negociavel": "Preço negociável", "orcamento": "Sob orçamento"}

STATUS_ANUNCIO = {
    "ativo": "Ativo",
    "pausado": "Pausado",
    "vendido": "Vendido / encerrado",
    "removido": "Removido pela moderação",
}


class Anuncio(TimestampMixin, db.Model):
    __tablename__ = "anuncios"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    categoria = db.Column(db.String(30), nullable=False, index=True)
    titulo = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    tipo_preco = db.Column(db.String(15), nullable=False, default="fixo")
    preco = db.Column(Money)
    unidade_preco = db.Column(db.String(40))  # por cabeça, por saca, por hora...
    municipio = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2), nullable=False, index=True)
    condicoes = db.Column(db.Text)
    entrega = db.Column(db.Text)
    # Campos específicos de anúncios de animais
    especie = db.Column(db.String(20))
    raca = db.Column(db.String(60))
    quantidade = db.Column(db.Integer)
    idade_meses = db.Column(db.Integer)
    sexo = db.Column(db.String(10))
    peso_medio_kg = db.Column(db.Numeric(8, 2))
    documentacao = db.Column(db.Text)  # GTA, registro genealógico, atestados sanitários informados pelo anunciante
    status = db.Column(db.String(15), nullable=False, default="ativo", index=True)
    visualizacoes = db.Column(db.Integer, nullable=False, default=0)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)

    autor = db.relationship("User")
    fotos = db.relationship("AnuncioFoto", back_populates="anuncio", cascade="all, delete-orphan", order_by="AnuncioFoto.ordem")

    @property
    def categoria_label(self):
        return CATEGORIAS_ANUNCIO.get(self.categoria, self.categoria)

    @property
    def capa(self):
        return self.fotos[0].arquivo if self.fotos else None


class AnuncioFoto(db.Model):
    __tablename__ = "anuncio_fotos"

    id = db.Column(db.Integer, primary_key=True)
    anuncio_id = db.Column(db.Integer, db.ForeignKey("anuncios.id", ondelete="CASCADE"), nullable=False, index=True)
    arquivo = db.Column(db.String(255), nullable=False)
    ordem = db.Column(db.Integer, nullable=False, default=0)

    anuncio = db.relationship("Anuncio", back_populates="fotos")


class Favorito(db.Model):
    __tablename__ = "favoritos"
    __table_args__ = (db.UniqueConstraint("user_id", "anuncio_id", name="uq_favorito"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    anuncio_id = db.Column(db.Integer, db.ForeignKey("anuncios.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    anuncio = db.relationship("Anuncio")


# ---------------------------------------------------------------- Profissionais

GRUPOS_PROFISSIONAIS = {
    "veterinaria": "Medicina veterinária",
    "agronomia": "Agronomia",
    "zootecnia": "Zootecnia",
    "tecnico": "Técnicos e assistência técnica",
    "reproducao": "Reprodução animal",
    "mecanizacao": "Máquinas e serviços mecanizados",
    "campo": "Outros profissionais do campo",
    "empresa": "Empresas de serviços",
}

MODALIDADES = {"presencial": "Presencial", "remoto": "Remoto (quando permitido)", "consultoria": "Consultoria"}


class CategoriaProfissional(db.Model):
    """Categorias administráveis: novas profissões são cadastradas pelo painel administrativo."""

    __tablename__ = "categorias_profissionais"

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    slug = db.Column(db.String(60), nullable=False, unique=True)
    grupo = db.Column(db.String(30), nullable=False, default="campo")
    descricao = db.Column(db.Text)
    registro_label = db.Column(db.String(40))  # ex.: CRMV, CREA, CRBio, CFTA
    aviso_habilitacao = db.Column(db.Text)
    ativa = db.Column(db.Boolean, nullable=False, default=True)
    ordem = db.Column(db.Integer, nullable=False, default=100)


class PerfilProfissional(TimestampMixin, db.Model):
    __tablename__ = "perfis_profissionais"
    __table_args__ = (db.UniqueConstraint("user_id", "categoria_id", name="uq_perfil_categoria"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias_profissionais.id"), nullable=False, index=True)
    titulo = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    especialidades = db.Column(db.Text)  # separadas por vírgula
    experiencia = db.Column(db.Text)
    qualificacoes = db.Column(db.Text)
    registro_numero = db.Column(db.String(40))
    registro_uf = db.Column(db.String(2))
    municipio = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2), nullable=False, index=True)
    regioes_atendidas = db.Column(db.Text)
    raio_km = db.Column(db.Integer)
    modalidades = db.Column(db.String(80))  # lista separada por vírgula
    disponibilidade = db.Column(db.String(150))
    disponivel = db.Column(db.Boolean, nullable=False, default=True)
    preco_info = db.Column(db.String(200))
    preco_minimo = db.Column(Money)
    equipamentos = db.Column(db.Text)
    portfolio = db.Column(db.Text)
    publico = db.Column(db.Boolean, nullable=False, default=True)
    # Verificação: somente administradores alteram após análise real de documentos.
    verificado = db.Column(db.Boolean, nullable=False, default=False)
    verificado_em = db.Column(db.DateTime)
    verificado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    nota_media = db.Column(db.Numeric(3, 2))
    total_avaliacoes = db.Column(db.Integer, nullable=False, default=0)
    is_demo = db.Column(db.Boolean, nullable=False, default=False)

    user = db.relationship("User", foreign_keys=[user_id])
    categoria = db.relationship("CategoriaProfissional")
    servicos = db.relationship("ServicoProfissional", back_populates="perfil", cascade="all, delete-orphan")

    @property
    def lista_especialidades(self):
        return [e.strip() for e in (self.especialidades or "").split(",") if e.strip()]

    @property
    def lista_modalidades(self):
        return [m for m in (self.modalidades or "").split(",") if m]


class ServicoProfissional(db.Model):
    __tablename__ = "servicos_profissionais"

    id = db.Column(db.Integer, primary_key=True)
    perfil_id = db.Column(db.Integer, db.ForeignKey("perfis_profissionais.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.Text)
    preco_info = db.Column(db.String(120))

    perfil = db.relationship("PerfilProfissional", back_populates="servicos")


class SolicitacaoVerificacao(TimestampMixin, db.Model):
    __tablename__ = "solicitacoes_verificacao"

    id = db.Column(db.Integer, primary_key=True)
    perfil_id = db.Column(db.Integer, db.ForeignKey("perfis_profissionais.id", ondelete="CASCADE"), nullable=False, index=True)
    documento = db.Column(db.String(255))  # arquivo privado
    mensagem = db.Column(db.Text)
    status = db.Column(db.String(15), nullable=False, default="pendente")  # pendente, aprovada, recusada
    analisado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    analisado_em = db.Column(db.DateTime)
    nota_admin = db.Column(db.Text)

    perfil = db.relationship("PerfilProfissional")
    analista = db.relationship("User")


STATUS_SOLICITACAO = {
    "criada": "Solicitação criada",
    "aguardando": "Aguardando resposta",
    "orcamento_enviado": "Orçamento enviado",
    "orcamento_aceito": "Orçamento aceito",
    "orcamento_recusado": "Orçamento recusado",
    "agendado": "Serviço agendado",
    "em_andamento": "Serviço em andamento",
    "concluido": "Serviço concluído",
    "cancelado": "Cancelado",
}

TIPOS_SOLICITACAO = {"orcamento": "Solicitar orçamento", "atendimento": "Solicitar atendimento"}


class SolicitacaoServico(TimestampMixin, db.Model):
    __tablename__ = "solicitacoes_servico"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    perfil_id = db.Column(db.Integer, db.ForeignKey("perfis_profissionais.id", ondelete="CASCADE"), nullable=False, index=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="SET NULL"))
    tipo = db.Column(db.String(15), nullable=False, default="orcamento")
    titulo = db.Column(db.String(150), nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    data_desejada = db.Column(db.Date)
    municipio = db.Column(db.String(120))
    uf = db.Column(db.String(2))
    observacoes = db.Column(db.Text)
    anexo = db.Column(db.String(255))
    status = db.Column(db.String(25), nullable=False, default="aguardando", index=True)
    data_agendada = db.Column(db.DateTime)
    concluido_em = db.Column(db.DateTime)
    lancamento_id = db.Column(db.Integer, db.ForeignKey("lancamentos.id", ondelete="SET NULL"))

    cliente = db.relationship("User", foreign_keys=[cliente_id])
    perfil = db.relationship("PerfilProfissional")
    propriedade = db.relationship("Propriedade")
    historico = db.relationship("HistoricoSolicitacao", back_populates="solicitacao", cascade="all, delete-orphan",
                                order_by="HistoricoSolicitacao.created_at")
    orcamentos = db.relationship("Orcamento", back_populates="solicitacao", cascade="all, delete-orphan",
                                 order_by="desc(Orcamento.created_at)")
    avaliacao = db.relationship("Avaliacao", back_populates="solicitacao", uselist=False)

    @property
    def status_label(self):
        return STATUS_SOLICITACAO.get(self.status, self.status)

    @property
    def orcamento_vigente(self):
        for o in self.orcamentos:
            if o.status in ("enviado", "aceito"):
                return o
        return None


class HistoricoSolicitacao(db.Model):
    __tablename__ = "historico_solicitacoes"

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("solicitacoes_servico.id", ondelete="CASCADE"), nullable=False, index=True)
    status_anterior = db.Column(db.String(25))
    status_novo = db.Column(db.String(25), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    nota = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    solicitacao = db.relationship("SolicitacaoServico", back_populates="historico")
    user = db.relationship("User")


class Orcamento(db.Model):
    __tablename__ = "orcamentos"

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("solicitacoes_servico.id", ondelete="CASCADE"), nullable=False, index=True)
    valor = db.Column(Money, nullable=False)
    descricao = db.Column(db.Text, nullable=False)
    validade = db.Column(db.Date)
    prazo_execucao = db.Column(db.String(120))
    status = db.Column(db.String(15), nullable=False, default="enviado")  # enviado, aceito, recusado, substituido
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    solicitacao = db.relationship("SolicitacaoServico", back_populates="orcamentos")


class Avaliacao(db.Model):
    __tablename__ = "avaliacoes"
    __table_args__ = (db.CheckConstraint("nota BETWEEN 1 AND 5", name="nota_valida"),)

    id = db.Column(db.Integer, primary_key=True)
    solicitacao_id = db.Column(db.Integer, db.ForeignKey("solicitacoes_servico.id", ondelete="CASCADE"), nullable=False, unique=True)
    perfil_id = db.Column(db.Integer, db.ForeignKey("perfis_profissionais.id", ondelete="CASCADE"), nullable=False, index=True)
    autor_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False)
    nota = db.Column(db.Integer, nullable=False)
    comentario = db.Column(db.Text)
    oculta = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    solicitacao = db.relationship("SolicitacaoServico", back_populates="avaliacao")
    autor = db.relationship("User")
    perfil = db.relationship("PerfilProfissional")


TIPOS_EMPRESA = {
    "casa_agropecuaria": "Casa agropecuária",
    "racoes": "Loja de rações",
    "medicamentos": "Medicamentos veterinários (estabelecimento autorizado)",
    "sementes": "Fornecedor de sementes",
    "fertilizantes": "Fornecedor de fertilizantes",
    "maquinas": "Máquinas e implementos",
    "equipamentos": "Fabricante / distribuidor de equipamentos",
    "ferramentas": "Loja de ferramentas",
    "transporte": "Empresa de transporte",
    "assistencia_tecnica": "Empresa de assistência técnica",
    "outro": "Outro fornecedor rural",
}


class PerfilEmpresa(TimestampMixin, db.Model):
    __tablename__ = "perfis_empresas"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    nome_fantasia = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.String(30), nullable=False, default="outro")
    descricao = db.Column(db.Text)
    municipio = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2), nullable=False)
    regiao_atendida = db.Column(db.Text)
    condicoes_entrega = db.Column(db.Text)
    canais_atendimento = db.Column(db.Text)
    cnpj = db.Column(db.String(20))  # privado
    publico = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User")


# ---------------------------------------------------------------- Vagas

CATEGORIAS_TRABALHO = {
    "manejo": "Peão / trabalhador de manejo",
    "vaqueiro": "Vaqueiro",
    "campeiro": "Campeiro",
    "ordenha": "Trabalhador de ordenha",
    "auxiliar_manejo": "Auxiliar de manejo",
    "operador_maquinas": "Operador de máquinas agrícolas",
    "tratorista": "Tratorista",
    "plantio_colheita": "Plantio e colheita",
    "granja": "Funcionário de granja",
    "manutencao": "Auxiliar de manutenção rural",
    "capataz": "Capataz / encarregado",
    "outro": "Outro trabalhador agropecuário",
}

TIPOS_CONTRATACAO = {
    "clt": "Emprego com carteira assinada (CLT)",
    "safra": "Contrato de safra",
    "temporario": "Temporário / prazo determinado",
    "diarista": "Diária",
    "estagio": "Estágio",
    "outro": "Outro",
}

STATUS_CANDIDATURA = {
    "recebida": "Candidatura recebida",
    "em_analise": "Em análise",
    "contato_solicitado": "Contato solicitado",
    "encerrada": "Encerrada",
}


class PerfilTrabalhador(TimestampMixin, db.Model):
    __tablename__ = "perfis_trabalhadores"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, unique=True)
    titulo = db.Column(db.String(150), nullable=False)
    categorias = db.Column(db.String(255))  # chaves separadas por vírgula
    habilidades = db.Column(db.Text)
    experiencias = db.Column(db.Text)
    descricao = db.Column(db.Text)
    municipio = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2), nullable=False, index=True)
    regiao_interesse = db.Column(db.String(255))
    disponibilidade = db.Column(db.String(150))
    disponivel = db.Column(db.Boolean, nullable=False, default=True)
    telefone_contato = db.Column(db.String(30))
    # Consentimento explícito para exibir o telefone a quem publica vagas e recebe a candidatura.
    compartilhar_contato = db.Column(db.Boolean, nullable=False, default=False)
    visivel_busca = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User")

    @property
    def lista_categorias(self):
        return [c for c in (self.categorias or "").split(",") if c]


class Vaga(TimestampMixin, db.Model):
    __tablename__ = "vagas"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    empregador = db.Column(db.String(150), nullable=False)
    cargo = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(30), nullable=False, default="outro", index=True)
    descricao = db.Column(db.Text, nullable=False)
    atividades = db.Column(db.Text)
    requisitos = db.Column(db.Text)
    experiencia = db.Column(db.String(150))
    tipo_contratacao = db.Column(db.String(20), nullable=False, default="clt")
    jornada = db.Column(db.String(150))
    salario_min = db.Column(Money)
    salario_max = db.Column(Money)
    beneficios = db.Column(db.Text)
    municipio = db.Column(db.String(120), nullable=False)
    uf = db.Column(db.String(2), nullable=False, index=True)
    prazo = db.Column(db.Date)
    forma_candidatura = db.Column(db.Text)
    status = db.Column(db.String(15), nullable=False, default="aberta", index=True)  # aberta, encerrada, removida
    is_demo = db.Column(db.Boolean, nullable=False, default=False)

    autor = db.relationship("User")
    candidaturas = db.relationship("Candidatura", back_populates="vaga", cascade="all, delete-orphan")

    @property
    def categoria_label(self):
        return CATEGORIAS_TRABALHO.get(self.categoria, self.categoria)


class Candidatura(TimestampMixin, db.Model):
    __tablename__ = "candidaturas"
    __table_args__ = (db.UniqueConstraint("vaga_id", "user_id", name="uq_candidatura"),)

    id = db.Column(db.Integer, primary_key=True)
    vaga_id = db.Column(db.Integer, db.ForeignKey("vagas.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True)
    mensagem = db.Column(db.Text)
    status = db.Column(db.String(25), nullable=False, default="recebida")
    nota_empregador = db.Column(db.Text)  # visível somente ao empregador

    vaga = db.relationship("Vaga", back_populates="candidaturas")
    candidato = db.relationship("User")

    @property
    def status_label(self):
        return STATUS_CANDIDATURA.get(self.status, self.status)
