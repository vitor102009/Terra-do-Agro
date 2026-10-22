"""Regras de domínio do rebanho compartilhadas entre módulos (cadastro, pesagens, reprodução e sincronização offline)."""
from datetime import timedelta

from app.extensions import db
from app.models import Animal, CategoriaAnimal, CategoriaFinanceira, EventoReprodutivo, Lote, Pesagem
from app.models.rebanho import CATEGORIAS_PADRAO, GESTACAO_DIAS


def atualizar_peso_atual(animal):
    """O peso atual é sempre o da pesagem individual mais recente (por data e, em empate, pela mais nova)."""
    ultima = (Pesagem.query.filter_by(animal_id=animal.id)
              .order_by(Pesagem.data.desc(), Pesagem.id.desc()).first())
    animal.peso_atual = ultima.peso_kg if ultima else None
    animal.data_peso_atual = ultima.data if ultima else None


def categorias_disponiveis(propriedade_id):
    """Categorias padrão por espécie mais as cadastradas pela propriedade."""
    extras = CategoriaAnimal.query.filter_by(propriedade_id=propriedade_id).order_by(CategoriaAnimal.nome).all()
    nomes = []
    for lista in CATEGORIAS_PADRAO.values():
        for n in lista:
            if n not in nomes:
                nomes.append(n)
    for c in extras:
        if c.nome not in nomes:
            nomes.append(c.nome)
    return nomes


def lotes_da_propriedade(propriedade_id, somente_ativos=True):
    q = Lote.query.filter_by(propriedade_id=propriedade_id)
    if somente_ativos:
        q = q.filter_by(ativo=True)
    return q.order_by(Lote.nome).all()


def animais_opcoes(propriedade_id, sexo=None):
    q = Animal.query.filter_by(propriedade_id=propriedade_id, status="ativo")
    if sexo:
        q = q.filter_by(sexo=sexo)
    return q.order_by(Animal.brinco, Animal.id).all()


def brinco_em_uso(propriedade_id, brinco, ignorar_id=None):
    if not brinco:
        return False
    q = Animal.query.filter(Animal.propriedade_id == propriedade_id, db.func.lower(Animal.brinco) == brinco.lower())
    if ignorar_id:
        q = q.filter(Animal.id != ignorar_id)
    return db.session.query(q.exists()).scalar()


def ultimo_servico(animal_id, antes_de):
    """Última cobertura/inseminação até a data informada (base para previsão de parto)."""
    return (EventoReprodutivo.query.filter(EventoReprodutivo.animal_id == animal_id,
                                           EventoReprodutivo.tipo.in_(("cobertura", "inseminacao")),
                                           EventoReprodutivo.data <= antes_de)
            .order_by(EventoReprodutivo.data.desc()).first())


def aplicar_evento_reprodutivo(animal, evento):
    """Atualiza a situação reprodutiva da fêmea conforme o evento registrado.

    A previsão de parto é apenas estimativa (data do último serviço + duração média de gestação da espécie)
    e só é calculada após diagnóstico positivo informado por quem realizou o exame.
    """
    if evento.tipo in ("cobertura", "inseminacao"):
        animal.situacao_reprodutiva = "servida"
    elif evento.tipo == "diagnostico":
        if evento.diagnostico_resultado == "positivo":
            animal.situacao_reprodutiva = "gestante"
            servico = ultimo_servico(animal.id, evento.data)
            dias = GESTACAO_DIAS.get(animal.especie)
            if servico and dias:
                evento.previsao_parto = servico.data + timedelta(days=dias)
        elif evento.diagnostico_resultado == "negativo":
            animal.situacao_reprodutiva = "vazia"
    elif evento.tipo == "parto":
        animal.situacao_reprodutiva = "parida"
        if animal.especie in ("bovino", "bubalino", "caprino", "ovino"):
            animal.situacao_produtiva = "lactacao"
    elif evento.tipo == "aborto":
        animal.situacao_reprodutiva = "vazia"
    elif evento.tipo == "secagem":
        animal.situacao_produtiva = "seca"


def categoria_financeira(propriedade_id, tipo, slug):
    """Categoria financeira padrão pelo identificador (ex.: 'compra_animais')."""
    return (CategoriaFinanceira.query.filter(CategoriaFinanceira.tipo == tipo, CategoriaFinanceira.slug == slug,
                                             db.or_(CategoriaFinanceira.propriedade_id.is_(None),
                                                    CategoriaFinanceira.propriedade_id == propriedade_id))
            .first())
