from decimal import Decimal

from app.extensions import db
from app.models.base import Money, Quantity, TimestampMixin, utcnow

CATEGORIAS_INSUMO = {
    "racao": "Ração",
    "suplemento": "Suplemento / sal mineral",
    "medicamento": "Medicamento veterinário",
    "vacina": "Vacina",
    "semente": "Semente",
    "fertilizante": "Fertilizante / corretivo",
    "defensivo": "Defensivo agrícola",
    "combustivel": "Combustível / lubrificante",
    "material": "Material de manutenção",
    "outro": "Outro",
}

UNIDADES = {"kg": "kg", "t": "tonelada", "sc": "saco", "l": "litro", "ml": "mL", "dose": "dose", "un": "unidade", "cx": "caixa", "fr": "frasco", "m": "metro"}

TIPOS_MOV_INSUMO = {"entrada": "Entrada", "saida": "Saída / consumo", "ajuste": "Ajuste de inventário"}


class Insumo(TimestampMixin, db.Model):
    __tablename__ = "insumos"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "nome", name="uq_insumo_nome"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    nome = db.Column(db.String(150), nullable=False)
    categoria = db.Column(db.String(30), nullable=False, default="outro")
    unidade = db.Column(db.String(10), nullable=False, default="kg")
    quantidade = db.Column(Quantity, nullable=False, default=0)
    quantidade_minima = db.Column(Quantity, nullable=False, default=0)
    custo_medio = db.Column(db.Numeric(14, 4), nullable=False, default=0)  # custo médio ponderado por unidade
    fornecedor = db.Column(db.String(150))
    validade = db.Column(db.Date)
    local_armazenamento = db.Column(db.String(120))
    ativo = db.Column(db.Boolean, nullable=False, default=True)

    movimentacoes = db.relationship("MovimentacaoInsumo", back_populates="insumo", cascade="all, delete-orphan",
                                    order_by="desc(MovimentacaoInsumo.data)")

    @property
    def estoque_baixo(self):
        return self.quantidade_minima and self.quantidade <= self.quantidade_minima


class MovimentacaoInsumo(db.Model):
    __tablename__ = "movimentacoes_insumos"

    id = db.Column(db.Integer, primary_key=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey("insumos.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(10), nullable=False)
    quantidade = db.Column(Quantity, nullable=False)  # sempre positiva; ajuste pode ser negativo
    data = db.Column(db.Date, nullable=False, index=True)
    custo_unitario = db.Column(db.Numeric(14, 4))
    custo_total = db.Column(Money)
    fornecedor = db.Column(db.String(150))
    validade = db.Column(db.Date)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="SET NULL"))
    animal_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="SET NULL"))
    atividade = db.Column(db.String(60))
    justificativa = db.Column(db.Text)
    # Quando uma entrada gerou despesa no financeiro, guardamos o vínculo para não duplicar o custo.
    lancamento_id = db.Column(db.Integer, db.ForeignKey("lancamentos.id", ondelete="SET NULL"), unique=True)
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    insumo = db.relationship("Insumo", back_populates="movimentacoes")
    lote = db.relationship("Lote")
    animal = db.relationship("Animal")
    lancamento = db.relationship("Lancamento")
    autor = db.relationship("User")


FORMAS_PAGAMENTO = {
    "dinheiro": "Dinheiro",
    "pix": "Pix",
    "transferencia": "Transferência",
    "boleto": "Boleto",
    "cartao": "Cartão",
    "cheque": "Cheque",
    "prazo": "A prazo",
    "outro": "Outro",
}

ATIVIDADES_CUSTO = {
    "": "Não atribuída",
    "cria": "Cria",
    "recria": "Recria",
    "engorda": "Engorda",
    "leite": "Produção leiteira",
    "lavoura": "Lavoura",
    "geral": "Administração geral",
}


class CategoriaFinanceira(db.Model):
    __tablename__ = "categorias_financeiras"
    __table_args__ = (db.UniqueConstraint("propriedade_id", "tipo", "nome", name="uq_categoria_financeira"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), index=True)
    tipo = db.Column(db.String(10), nullable=False)  # receita ou despesa
    nome = db.Column(db.String(80), nullable=False)
    slug = db.Column(db.String(40))
    custo_producao = db.Column(db.Boolean, nullable=False, default=True)


class Lancamento(TimestampMixin, db.Model):
    """Receita ou despesa. A data é a data de competência; os pagamentos registram o caixa."""

    __tablename__ = "lancamentos"
    __table_args__ = (db.CheckConstraint("valor > 0", name="valor_positivo"),)

    id = db.Column(db.Integer, primary_key=True)
    propriedade_id = db.Column(db.Integer, db.ForeignKey("propriedades.id", ondelete="CASCADE"), nullable=False, index=True)
    tipo = db.Column(db.String(10), nullable=False, index=True)  # receita | despesa
    descricao = db.Column(db.String(200), nullable=False)
    categoria_id = db.Column(db.Integer, db.ForeignKey("categorias_financeiras.id"), nullable=False)
    valor = db.Column(Money, nullable=False)
    data = db.Column(db.Date, nullable=False, index=True)
    vencimento = db.Column(db.Date, index=True)
    forma = db.Column(db.String(20))
    contraparte = db.Column(db.String(150))
    observacoes = db.Column(db.Text)
    anexo = db.Column(db.String(255))
    animal_id = db.Column(db.Integer, db.ForeignKey("animais.id", ondelete="SET NULL"), index=True)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id", ondelete="SET NULL"), index=True)
    atividade = db.Column(db.String(20))
    origem = db.Column(db.String(30), nullable=False, default="manual")  # manual, estoque, servico, animal, offline
    cancelado = db.Column(db.Boolean, nullable=False, default=False)
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))

    categoria = db.relationship("CategoriaFinanceira")
    animal = db.relationship("Animal")
    lote = db.relationship("Lote")
    pagamentos = db.relationship("Pagamento", back_populates="lancamento", cascade="all, delete-orphan",
                                 order_by="Pagamento.data")

    @property
    def valor_pago(self):
        return sum((p.valor for p in self.pagamentos), Decimal("0"))

    @property
    def saldo(self):
        return (self.valor or Decimal("0")) - self.valor_pago

    @property
    def situacao(self):
        if self.cancelado:
            return "cancelado"
        pago = self.valor_pago
        if pago <= 0:
            return "pendente"
        if pago < self.valor:
            return "parcial"
        return "quitado"

    @property
    def situacao_label(self):
        base = {"pendente": "Pendente", "parcial": "Parcial", "cancelado": "Cancelado"}
        if self.situacao == "quitado":
            return "Recebido" if self.tipo == "receita" else "Pago"
        return base[self.situacao]


class Pagamento(db.Model):
    """Pagamento (despesa) ou recebimento (receita) efetivo de um lançamento."""

    __tablename__ = "pagamentos"
    __table_args__ = (db.CheckConstraint("valor > 0", name="pagamento_positivo"),)

    id = db.Column(db.Integer, primary_key=True)
    lancamento_id = db.Column(db.Integer, db.ForeignKey("lancamentos.id", ondelete="CASCADE"), nullable=False, index=True)
    data = db.Column(db.Date, nullable=False, index=True)
    valor = db.Column(Money, nullable=False)
    forma = db.Column(db.String(20))
    registrado_por = db.Column(db.Integer, db.ForeignKey("usuarios.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    lancamento = db.relationship("Lancamento", back_populates="pagamentos")
