from app.extensions import db
from app.models.base import TimestampMixin, utcnow

ESPECIES = {
    "bovino": "Bovino",
    "bubalino": "Bubalino",
    "ovino": "Ovino",
    "caprino": "Caprino",
    "equino": "Equino",
    "suino": "Suíno",
    "outra": "Outra",
}

# Categorias padrão por espécie. Cada propriedade pode cadastrar categorias adicionais.
CATEGORIAS_PADRAO = {
    "bovino": ["Bezerro", "Bezerra", "Novilha", "Vaca", "Touro", "Garrote", "Boi", "Engorda"],
    "bubalino": ["Bezerro", "Bezerra", "Novilha", "Búfala", "Búfalo reprodutor", "Engorda"],
    "ovino": ["Cordeiro", "Cordeira", "Borrego", "Matriz", "Reprodutor", "Engorda"],
    "caprino": ["Cabrito", "Cabrita", "Matriz", "Reprodutor", "Engorda"],
    "equino": ["Potro", "Potra", "Égua", "Garanhão", "Cavalo de serviço"],
    "suino": ["Leitão", "Marrã", "Matriz", "Cachaço", "Terminação"],
    "outra": ["Geral"],
}

SEXOS = {"F": "Fêmea", "M": "Macho"}

SITUACOES_PRODUTIVAS = {
    "cria": "Cria",
    "recria": "Recria",
    "engorda": "Engorda / terminação",
    "lactacao": "Em lactação",
    "seca": "Seca",
    "reproducao": "Reprodução",
    "trabalho": "Trabalho",
    "outra": "Outra",
}

SITUACOES_REPRODUTIVAS = {
    "nao_aplicavel": "Não se aplica",
    "vazia": "Vazia",
    "servida": "Coberta / inseminada (aguardando diagnóstico)",
    "gestante": "Gestante",
    "parida": "Parida",
}

SITUACOES_SANITARIAS = {
    "saudavel": "Sem ocorrências",
    "observacao": "Em observação",
    "tratamento": "Em tratamento",
    "quarentena": "Quarentena",
}

ORIGENS = {
    "nascido": "Nascido na propriedade",
    "compra": "Compra",
    "transferencia": "Transferência",
    "doacao": "Doação",
    "outra": "Outra",
}

STATUS_ANIMAL = {
    "ativo": "Ativo",
    "vendido": "Vendido",
    "morto": "Óbito",
    "transferido": "Transferido",
    "arquivado": "Arquivado",
}

TIPOS_MOVIMENTACAO = {
    "entrada": "Entrada",
    "troca_lote": "Troca de lote",
    "venda": "Venda",
    "obito": "Óbito",
    "transferencia": "Transferência para outra propriedade",
    "arquivamento": "Arquivamento",
    "reativacao": "Reativação",
}

# Duração média de gestação (dias) usada apenas como estimativa de previsão de parto.
GESTACAO_DIAS = {"bovino": 283, "bubalino": 310, "ovino": 150, "caprino": 150, "equino": 340, "suino": 114}


class CategoriaAnimal(db.Model):
    __tablename__ = "categorias_animais"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "especie", "nome", name="uq_categoria_animal"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), index=True)
    especie = db.Column(db.String(20), nullable=False)
    nome = db.Column(db.String(60), nullable=False)


class Lote(TimestampMixin, db.Model):
    __tablename__ = "lotes"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "nome", name="uq_lote_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(100), nullable=False)
    descricao = db.Column(db.Text)
    finalidade = db.Column(db.String(60))
    pastagem_id = db.Column(db.Integer, db.ForeignKey("pastagens.id", ondelete="SET NULL"))
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    pastagem = db.relationship("Pastagem", back_populates="lotes")
    animais = db.relationship("Animal", back_populates="lote", lazy="dynamic")


class Pastagem(TimestampMixin, db.Model):
    __tablename__ = "pastagens"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "nome", name="uq_pastagem_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(100), nullable=False)
    area_ha = db.Column(db.Numeric(12, 2))
    forrageira = db.Column(db.String(100))
    condicao = db.Column(db.String(30))  # boa, regular, degradada, em_descanso
    disponibilidade = db.Column(db.Text)
    observacoes = db.Column(db.Text)
    ativa = db.Column(db.Boolean, nullable=False, default=True)

    lotes = db.relationship("Lote", back_populates="pastagem")
    manejos = db.relationship("ManejoPastagem", back_populates="pastagem", cascade="all, delete-orphan",
                              order_by="desc(ManejoPastagem.data)")


CONDICOES_PASTAGEM = {
    "boa": "Boa",
    "regular": "Regular",
    "degradada": "Degradada",
    "descanso": "Em descanso / vedada",
    "reforma": "Em reforma",
}


class ManejoPastagem(db.Model):
    __tablename__ = "manejos_pastagem"

    id = db.Column(db.Integer, primary_key=True)
    pastagem_id = db.Column(db.Integer, db.ForeignKey("pastagens.id", ondelete="CASCADE"), nullable=False, index=True)
    data = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(40), nullable=False)  # avaliacao, entrada_lote, saida_lote, adubacao, rocada...
    condicao = db.Column(db.String(30))
    altura_cm = db.Column(db.Numeric(6, 1))
    observacao = db.Column(db.Text)
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    pastagem = db.relationship("Pastagem", back_populates="manejos")
    autor = db.relationship("User")


class Animal(TimestampMixin, db.Model):
    __tablename__ = "animais"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "brinco", name="uq_animal_brinco"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    brinco = db.Column(db.String(40), index=True)  # identificação visual principal (opcional)
    nome = db.Column(db.String(80))  # opcional
    especie = db.Column(db.String(20), nullable=False, default="bovino")
    raca = db.Column(db.String(60))
    sexo = db.Column(db.String(1), nullable=False)
    data_nascimento = db.Column(db.Date)
    nascimento_estimado = db.Column(db.Boolean, nullable=False, default=False)
    categoria = db.Column(db.String(60))
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="SET NULL"), index=True)
    situacao_produtiva = db.Column(db.String(20))
    situacao_reprodutiva = db.Column(db.String(20), nullable=False, default="nao_aplicavel")
    situacao_sanitaria = db.Column(db.String(20), nullable=False, default="saudavel")
    origem = db.Column(db.String(20), nullable=False, default="nascido")
    data_entrada = db.Column(db.Date, nullable=False)
    mae_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="SET NULL"))
    pai_descricao = db.Column(db.String(120))
    peso_atual = db.Column(db.Numeric(8, 2))  # cache do último registro em pesagens
    data_peso_atual = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="ativo", index=True)
    data_saida = db.Column(db.Date)
    observacoes = db.Column(db.Text)
    foto = db.Column(db.String(255))
    criado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))

    lote = db.relationship("Lote", back_populates="animais")
    mae = db.relationship("Animal", remote_side=[id])
    movimentacoes = db.relationship("MovimentacaoAnimal", back_populates="animal", cascade="all, delete-orphan",
                                    order_by="desc(MovimentacaoAnimal.data)")
    pesagens = db.relationship("Pesagem", back_populates="animal", order_by="Pesagem.data")

    @property
    def identificacao(self):
        if self.brinco:
            return self.brinco
        if self.nome:
            return self.nome
        return f"#{self.id}"

    @property
    def ativo(self):
        return self.status == "ativo"


class MovimentacaoAnimal(db.Model):
    __tablename__ = "movimentacoes_animais"

    id = db.Column(db.Integer, primary_key=True)
    animal_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)
    data = db.Column(db.Date, nullable=False)
    lote_origem_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="SET NULL"))
    lote_destino_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="SET NULL"))
    valor = db.Column(db.Numeric(14, 2))
    destino = db.Column(db.String(150))
    observacao = db.Column(db.Text)
    lancamento_id = db.Column(db.Integer, db.ForeignKey("lancamentos.id", ondelete="SET NULL"))
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    animal = db.relationship("Animal", back_populates="movimentacoes")
    lote_origem = db.relationship("Lote", foreign_keys=[lote_origem_id])
    lote_destino = db.relationship("Lote", foreign_keys=[lote_destino_id])
    autor = db.relationship("User")


class Pesagem(db.Model):
    __tablename__ = "pesagens"
    __table_args__ = (
        db.CheckConstraint("peso_kg > 0", name="peso_positivo"),
        db.CheckConstraint("(animal_id IS NOT NULL) OR (lote_id IS NOT NULL)", name="alvo_pesagem"),
    )

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    animal_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="CASCADE"), index=True)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="CASCADE"), index=True)
    data = db.Column(db.Date, nullable=False, index=True)
    peso_kg = db.Column(db.Numeric(8, 2), nullable=False)  # para lote: peso médio por animal
    qtd_animais = db.Column(db.Integer)  # apenas pesagem de lote
    observacoes = db.Column(db.Text)
    responsavel = db.Column(db.String(120))
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    offline_uuid = db.Column(db.String(36), unique=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    animal = db.relationship("Animal", back_populates="pesagens")
    lote = db.relationship("Lote")
    autor = db.relationship("User")


TIPOS_SANITARIOS = {
    "vacina": "Vacinação",
    "vermifugo": "Vermifugação / antiparasitário",
    "tratamento": "Tratamento",
    "exame": "Exame",
    "consulta": "Consulta / atendimento veterinário",
    "outro": "Outro",
}


class EventoSanitario(db.Model):
    __tablename__ = "eventos_sanitarios"

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    animal_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="CASCADE"), index=True)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="SET NULL"), index=True)
    tipo = db.Column(db.String(20), nullable=False, default="vacina")
    produto = db.Column(db.String(150), nullable=False)
    lote_produto = db.Column(db.String(60))
    dose = db.Column(db.String(60))
    data_aplicacao = db.Column(db.Date, nullable=False, index=True)
    proxima_data = db.Column(db.Date, index=True)
    proxima_resolvida = db.Column(db.Boolean, nullable=False, default=False)
    responsavel = db.Column(db.String(120))
    profissional_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    insumo_id = db.Column(db.Integer, db.ForeignKey("insumos.id", ondelete="SET NULL"))
    observacoes = db.Column(db.Text)
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    offline_uuid = db.Column(db.String(36), unique=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    animal = db.relationship("Animal")
    lote = db.relationship("Lote")
    profissional = db.relationship("User", foreign_keys=[profissional_id])
    autor = db.relationship("User", foreign_keys=[registrado_por])

    @property
    def tipo_label(self):
        return TIPOS_SANITARIOS.get(self.tipo, self.tipo)

    @property
    def alvo(self):
        if self.animal:
            return f"Animal {self.animal.identificacao}"
        if self.lote:
            return f"Lote {self.lote.nome}"
        return "Rebanho da propriedade"


TIPOS_REPRODUTIVOS = {
    "cobertura": "Cobertura (monta natural)",
    "inseminacao": "Inseminação artificial",
    "diagnostico": "Diagnóstico de gestação",
    "parto": "Parto",
    "aborto": "Aborto / perda gestacional",
    "secagem": "Secagem",
}

RESULTADOS_DIAGNOSTICO = {"positivo": "Positivo (gestante)", "negativo": "Negativo (vazia)", "inconclusivo": "Inconclusivo"}


class EventoReprodutivo(db.Model):
    __tablename__ = "eventos_reprodutivos"

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    animal_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(20), nullable=False)
    data = db.Column(db.Date, nullable=False, index=True)
    reprodutor_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="SET NULL"))
    reprodutor_descricao = db.Column(db.String(150))  # touro externo, sêmen, partida
    diagnostico_resultado = db.Column(db.String(20))
    metodo_diagnostico = db.Column(db.String(60))
    responsavel = db.Column(db.String(120))
    profissional_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    previsao_parto = db.Column(db.Date)
    cria_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="SET NULL"))
    observacoes = db.Column(db.Text)
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    animal = db.relationship("Animal", foreign_keys=[animal_id])
    reprodutor = db.relationship("Animal", foreign_keys=[reprodutor_id])
    cria = db.relationship("Animal", foreign_keys=[cria_id])
    autor = db.relationship("User", foreign_keys=[registrado_por])

    @property
    def tipo_label(self):
        return TIPOS_REPRODUTIVOS.get(self.tipo, self.tipo)
