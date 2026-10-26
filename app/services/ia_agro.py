"""IA Agro: assistente em português que consulta os dados reais do usuário por ferramentas.

Segurança dos dados:
- Cada ferramenta recebe um `Contexto` montado no servidor (usuário logado, propriedade ativa e permissões).
  O modelo não escolhe propriedade nem usuário: só filtra dentro do que o usuário já pode ver.
- Financeiro só com a permissão "financeiro" da propriedade. Anúncios: apenas os do próprio usuário.
- Não são enviados ao provedor: e-mail, telefone, endereço, coordenadas, senhas ou dados de outros usuários.
- A IA não cria anúncios, não envia mensagens e não faz pagamentos (não existe ferramenta para isso).
"""
import json
import time
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from flask import current_app

from app.extensions import db
from app.models import (
    Animal, Anuncio, ConversaIA, EventoReprodutivo, EventoSanitario, Insumo, Lote, MensagemIA, MovimentacaoInsumo, Pesagem,
    utcnow,
)
from app.models.propriedade import PAPEIS_PROPRIEDADE
from app.services import cotacoes as servico_cotacoes
from app.services import custos as servico_custos
from app.services import indicadores

ARROBA_KG = Decimal("15")  # arroba de carcaça

SYSTEM = """Você é a IA Agro, assistente do Terra do Agro para produtores rurais brasileiros. Responda sempre em \
português do Brasil, de forma clara, prática e objetiva, adequada para leitura no celular.

Regras sobre dados:
- Para qualquer número da propriedade (rebanho, pesagens, sanidade, reprodução, estoque, finanças, anúncios) ou \
preço de mercado, consulte as ferramentas. Nunca use preços ou valores de memória e nunca invente dados.
- Quando uma ferramenta não trouxer o dado, diga que ele não está registrado e o que o produtor precisa cadastrar.
- Ao citar uma cotação, informe produto, valor, unidade, praça, fonte e data/horário da fonte, e avise quando o \
estado for "desatualizada". Preço de referência não é oferta de compra. Só diga "tempo real" se a fonte publica \
nessa frequência (nenhuma fonte conectada publica a cada segundo).
- Em estimativas, mostre a fórmula, as premissas, a unidade, a fonte e a data do preço usado.
- Em análises financeiras, explique os critérios (competência x caixa) e aponte dados faltantes antes de concluir.
- Se uma ferramenta responder que o usuário não tem permissão, explique isso sem tentar contornar.
- Você pode redigir textos de anúncio de venda, mas não publica anúncios, não envia mensagens e não faz pagamentos: \
o produtor revisa e publica pelo marketplace.
- Para diagnóstico, prescrição de medicamentos ou receituário, oriente procurar médico-veterinário ou agrônomo \
habilitado; você pode dar orientações gerais de manejo.
- Não peça nem repita dados pessoais (e-mail, telefone, documentos)."""


@dataclass
class Contexto:
    user_id: int
    propriedade: object  # Propriedade ou None
    perms: set

    @property
    def pid(self):
        return self.propriedade.id if self.propriedade else None


class IAIndisponivel(Exception):
    """Falha com mensagem segura para o usuário."""


INDISPONIVEL = "A IA Agro está temporariamente indisponível. O administrador foi avisado pelo registro do servidor."


class LimiteExcedido(Exception):
    pass


def _f(v, casas=2):
    return None if v is None else round(float(v), casas)


def _sem_propriedade():
    return {"erro": "Nenhuma propriedade ativa. O usuário precisa cadastrar ou selecionar uma propriedade."}


# ------------------------------------------------------------------ ferramentas

def t_resumo_propriedade(ctx):
    if not ctx.pid:
        return _sem_propriedade()
    p, pid = ctx.propriedade, ctx.pid
    gmd, n_gmd = indicadores.gmd_medio_rebanho(pid, date.today() - timedelta(days=365))
    proximas, vencidas = indicadores.vacinas_alerta(pid)
    baixo, vencendo = indicadores.insumos_alerta(pid)
    return {
        "propriedade": p.nome, "municipio": p.municipio, "uf": p.uf, "atividade": p.atividade_label,
        "area_total_ha": _f(p.area_total_ha), "area_pastagem_ha": _f(p.area_pastagem_ha),
        "area_agricultura_ha": _f(p.area_agricultura_ha),
        "animais_ativos": indicadores.animais_ativos(pid).count(),
        "por_categoria": dict(indicadores.contagem_por(pid, Animal.categoria)),
        "por_lote": dict(indicadores.animais_por_lote(pid)),
        "reproducao": {k: _f(v, 1) if k == "taxa_prenhez" else v for k, v in indicadores.reprodutivo(pid).items()},
        "gmd_medio_12m_kg_dia": _f(gmd, 3), "animais_com_gmd": n_gmd,
        "alertas": {"vacinas_vencidas": len(vencidas), "vacinas_proximos_15_dias": len(proximas),
                    "insumos_estoque_baixo": len(baixo), "insumos_vencendo_30_dias": len(vencendo)},
        "permissoes_do_usuario": sorted(ctx.perms),
    }


def t_listar_animais(ctx, lote=None, categoria=None, status="ativo", limite=50):
    if not ctx.pid:
        return _sem_propriedade()
    q = Animal.query.filter(Animal.propriedade_id == ctx.pid)
    if status and status != "todos":
        q = q.filter(Animal.status == status)
    if lote:
        q = q.join(Lote, Animal.lote_id == Lote.id).filter(Lote.nome.ilike(f"%{lote[:60]}%"))
    if categoria:
        q = q.filter(Animal.categoria.ilike(f"%{categoria[:60]}%"))
    total = q.count()
    limite = max(1, min(int(limite or 50), 100))
    animais = q.order_by(Animal.brinco).limit(limite).all()
    return {"total_encontrado": total, "exibidos": len(animais), "animais": [
        {"brinco": a.brinco, "nome": a.nome, "especie": a.especie, "categoria": a.categoria, "sexo": a.sexo,
         "raca": a.raca, "lote": a.lote.nome if a.lote else None, "status": a.status,
         "nascimento": a.data_nascimento.isoformat() if a.data_nascimento else None,
         "peso_atual_kg": _f(a.peso_atual), "data_peso": a.data_peso_atual.isoformat() if a.data_peso_atual else None,
         "situacao_reprodutiva": a.situacao_reprodutiva, "situacao_sanitaria": a.situacao_sanitaria} for a in animais]}


def t_pesagens_animal(ctx, brinco):
    if not ctx.pid:
        return _sem_propriedade()
    a = Animal.query.filter_by(propriedade_id=ctx.pid, brinco=(brinco or "").strip()[:40]).first()
    if not a:
        return {"erro": f"Nenhum animal com brinco {brinco!r} nesta propriedade."}
    pesagens = Pesagem.query.filter_by(propriedade_id=ctx.pid, animal_id=a.id).order_by(Pesagem.data).all()
    g = indicadores.gmd(pesagens)
    return {"brinco": a.brinco, "pesagens": [{"data": p.data.isoformat(), "peso_kg": _f(p.peso_kg)} for p in pesagens[-30:]],
            "gmd": ({"kg_dia": _f(g["gmd"], 3), "dias": g["dias"], "ganho_kg": _f(g["ganho"])} if g else None),
            "criterio_gmd": "(último peso − primeiro peso) ÷ dias; exige 2 pesagens com 7+ dias de intervalo."}


def t_sanidade(ctx, dias=30):
    if not ctx.pid:
        return _sem_propriedade()
    dias = max(1, min(int(dias or 30), 180))
    proximas, vencidas = indicadores.vacinas_alerta(ctx.pid, dias=dias)

    def item(e):
        alvo = f"animal {e.animal.brinco}" if e.animal else (f"lote {e.lote.nome}" if e.lote else "não informado")
        return {"tipo": e.tipo, "produto": e.produto, "alvo": alvo, "aplicado_em": e.data_aplicacao.isoformat(),
                "proxima_data": e.proxima_data.isoformat() if e.proxima_data else None}
    recentes = (EventoSanitario.query.filter_by(propriedade_id=ctx.pid)
                .order_by(EventoSanitario.data_aplicacao.desc()).limit(20).all())
    return {"vencidas": [item(e) for e in vencidas[:30]], f"proximas_{dias}_dias": [item(e) for e in proximas[:30]],
            "ultimos_registros": [item(e) for e in recentes]}


def t_reproducao(ctx):
    if not ctx.pid:
        return _sem_propriedade()
    hoje = date.today()
    partos = (EventoReprodutivo.query.filter(EventoReprodutivo.propriedade_id == ctx.pid,
                                             EventoReprodutivo.previsao_parto >= hoje)
              .order_by(EventoReprodutivo.previsao_parto).limit(30).all())
    r = indicadores.reprodutivo(ctx.pid)
    r["taxa_prenhez"] = _f(r["taxa_prenhez"], 1)
    return {"resumo_femeas_ativas": r,
            "criterio_taxa_prenhez": "gestantes ÷ (vazias + servidas aguardando diagnóstico + gestantes)",
            "proximos_partos_previstos": [{"brinco": e.animal.brinco, "previsao": e.previsao_parto.isoformat()} for e in partos],
            "observacao": "Previsão de parto = último serviço + gestação média da espécie; é estimativa."}


def t_estoque(ctx):
    if not ctx.pid:
        return _sem_propriedade()
    itens = Insumo.query.filter_by(propriedade_id=ctx.pid, ativo=True).order_by(Insumo.nome).limit(100).all()
    return {"insumos": [{"nome": i.nome, "categoria": i.categoria, "unidade": i.unidade, "quantidade": _f(i.quantidade, 3),
                         "minimo": _f(i.quantidade_minima, 3), "custo_medio_unitario": _f(i.custo_medio, 4),
                         "validade": i.validade.isoformat() if i.validade else None,
                         "abaixo_do_minimo": bool(i.quantidade_minima and i.quantidade <= i.quantidade_minima)} for i in itens],
            "movimentacoes_recentes": [
                {"insumo": m.insumo.nome, "tipo": m.tipo, "quantidade": _f(m.quantidade, 3), "data": m.data.isoformat(),
                 # custo das movimentações é dado financeiro: só para quem tem a permissão
                 **({"custo_total": _f(m.custo_total)} if "financeiro" in ctx.perms else {})}
                for m in (MovimentacaoInsumo.query.join(Insumo).filter(Insumo.propriedade_id == ctx.pid)
                          .order_by(MovimentacaoInsumo.data.desc(), MovimentacaoInsumo.id.desc()).limit(20))],
            "criterio": "Custo médio ponderado por unidade."}


def t_custos_producao(ctx, periodo="ano"):
    if not ctx.pid:
        return _sem_propriedade()
    if "financeiro" not in ctx.perms:
        return {"erro": "O usuário não tem permissão para ver custos desta propriedade."}
    if periodo not in indicadores.PERIODOS:
        periodo = "ano"
    inicio, fim = indicadores.periodo(periodo)
    c = servico_custos.calcular(ctx.pid, inicio, fim)
    return {"periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
            "custo_total": _f(c["total_geral"]), "animais_ativos": c["total_animais"],
            "custo_medio_por_cabeca": _f(c["custo_medio_cabeca"]),
            "por_lote": [{"lote": l["lote"], "animais": l["animais"], "total": _f(l["total"]), "por_cabeca": _f(l["por_cabeca"])}
                         for l in c["lotes"]],
            "despesas_consideradas": c["qtd_despesas"], "consumos_de_estoque": c["qtd_consumos"],
            "criterios": ("Despesas de categorias marcadas como custo + consumo de estoque valorizado pelo custo médio "
                          "(compras não entram, para não contar duas vezes). Custos sem lote são rateados por animais ativos.")}


def t_financeiro(ctx, periodo="mes"):
    if not ctx.pid:
        return _sem_propriedade()
    if "financeiro" not in ctx.perms:
        return {"erro": "O usuário não tem permissão para ver o financeiro desta propriedade."}
    if periodo not in indicadores.PERIODOS:
        periodo = "mes"
    pid = ctx.pid
    inicio, fim = indicadores.periodo(periodo)
    rec = indicadores.total_lancamentos(pid, "receita", inicio, fim)
    desp = indicadores.total_lancamentos(pid, "despesa", inicio, fim)
    _, a_pagar = indicadores.em_aberto(pid, "despesa")
    _, a_receber = indicadores.em_aberto(pid, "receita")
    vencidas = indicadores.contas_vencidas(pid)
    serie = indicadores.serie_financeira(pid)
    return {
        "periodo": {"chave": periodo, "rotulo": indicadores.PERIODOS[periodo], "inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "tem_lancamentos": indicadores._base_lanc(pid).count() > 0,
        "receitas_competencia": _f(rec), "despesas_competencia": _f(desp), "resultado_competencia": _f(rec - desp),
        "caixa_no_periodo": _f(indicadores.total_caixa(pid, "receita", inicio, fim) - indicadores.total_caixa(pid, "despesa", inicio, fim)),
        "saldo_caixa_acumulado": _f(indicadores.saldo_caixa_acumulado(pid, fim)),
        "despesas_por_categoria": {n: _f(v) for n, v in indicadores.despesas_por_categoria(pid, inicio, fim)},
        "contas_a_pagar_em_aberto": _f(a_pagar), "contas_a_receber_em_aberto": _f(a_receber),
        "contas_vencidas": {"quantidade": len(vencidas), "total": _f(sum((l.saldo for l in vencidas), Decimal(0)))},
        "ultimos_12_meses": {k: serie[k] for k in ("labels", "receitas", "despesas", "resultado")},
        "criterios": ("Receitas/despesas pela data de competência de lançamentos não cancelados; caixa pelos pagamentos "
                      "efetivos; saldo de caixa calculado somente com os lançamentos registrados no sistema."),
    }


def t_meus_anuncios(ctx):
    anuncios = Anuncio.query.filter_by(user_id=ctx.user_id).order_by(Anuncio.created_at.desc()).limit(30).all()
    return {"anuncios": [{"titulo": a.titulo, "categoria": a.categoria, "status": a.status, "tipo_preco": a.tipo_preco,
                          "preco": _f(a.preco), "unidade_preco": a.unidade_preco, "quantidade": a.quantidade,
                          "municipio": a.municipio, "uf": a.uf, "visualizacoes": a.visualizacoes} for a in anuncios],
            "observacao": "Somente anúncios do próprio usuário. A IA não publica nem altera anúncios."}


def _cot_resumo(c):
    return {k: c[k] for k in ("chave", "produto", "categoria", "praca", "uf", "valor", "unidade", "moeda", "fonte",
                              "data_referencia", "publicado_em", "fuso_publicacao", "estado", "variacao", "variacao_pct",
                              "comparado_com")}


def t_cotacoes(ctx, produto=None, categoria=None):
    filtros = {}
    if produto:
        filtros["produto"] = produto
    if categoria:
        filtros["categoria"] = categoria
    itens = servico_cotacoes.recentes(filtros)
    return {"cotacoes": [_cot_resumo(c) for c in itens[:40]], "fontes": servico_cotacoes.estado_fontes(),
            "observacao": ("Valores como publicados pela fonte. 'variacao' compara com o último valor de um dia anterior "
                           "da mesma série. Produtos sem cotação não têm fonte conectada nem registro do administrador.")}


def t_historico_cotacao(ctx, chave, dias=30):
    if dias not in (7, 30, 90, 180, 365):
        dias = 30
    h = servico_cotacoes.historico(chave, dias)
    h["pontos"] = h["pontos"][-120:]
    return h


def t_estimar_valor_rebanho(ctx, lote=None, rendimento_carcaca_pct=50):
    if not ctx.pid:
        return _sem_propriedade()
    rend = Decimal(str(rendimento_carcaca_pct or 50))
    if not (Decimal(40) <= rend <= Decimal(65)):
        return {"erro": "Rendimento de carcaça fora da faixa aceita (40% a 65%)."}
    q = Animal.query.filter(Animal.propriedade_id == ctx.pid, Animal.status == "ativo", Animal.especie == "bovino")
    if lote:
        q = q.join(Lote, Animal.lote_id == Lote.id).filter(Lote.nome.ilike(f"%{lote[:60]}%"))
    animais = q.all()
    com_peso = [a for a in animais if a.peso_atual]
    preco = next((c for c in servico_cotacoes.recentes({"produto": "boi"}) if "@" in c["unidade"]), None)
    base = {"animais_bovinos_ativos": len(animais), "com_pesagem": len(com_peso), "sem_pesagem": len(animais) - len(com_peso)}
    if not preco:
        return {**base, "erro": "Não há cotação de boi gordo em R$/@ disponível no sistema (fonte desativada ou sem registro)."}
    if not com_peso:
        return {**base, "erro": "Nenhum animal com pesagem registrada; não é possível estimar."}
    peso_vivo = sum((Decimal(a.peso_atual) for a in com_peso), Decimal(0))
    arrobas = peso_vivo * rend / 100 / ARROBA_KG
    valor = arrobas * Decimal(str(preco["valor"]))
    return {**base,
            "peso_vivo_total_kg": _f(peso_vivo), "rendimento_carcaca_pct": _f(rend, 1),
            "arrobas_estimadas": _f(arrobas), "valor_estimado_reais": _f(valor),
            "formula": "valor = (peso vivo total × rendimento de carcaça ÷ 15 kg por arroba) × preço R$/@",
            "preco_usado": _cot_resumo(preco),
            "avisos": ["Estimativa de referência, não é oferta de compra; o preço real depende de categoria, acabamento, "
                       "frete, prazo e comprador.",
                       "O rendimento de carcaça é uma premissa (padrão 50%); ajuste conforme o lote.",
                       "Animais sem pesagem não entram no cálculo."]}


def _tool(name, desc, props=None, required=()):
    return {"name": name, "description": desc, "strict": True,
            "input_schema": {"type": "object", "properties": props or {}, "required": list(required),
                             "additionalProperties": False}}


TOOLS = [
    _tool("resumo_propriedade", "Resumo da propriedade ativa: área, atividade, contagem do rebanho por categoria e lote, "
          "indicadores reprodutivos, GMD médio e alertas. Use primeiro para entender a propriedade."),
    _tool("listar_animais", "Lista animais da propriedade ativa (brinco, categoria, lote, peso atual, situação).",
          {"lote": {"type": ["string", "null"], "description": "Parte do nome do lote"},
           "categoria": {"type": ["string", "null"], "description": "Parte do nome da categoria (ex.: Vaca, Garrote)"},
           "status": {"type": "string", "enum": ["ativo", "vendido", "morto", "transferido", "arquivado", "todos"]},
           "limite": {"type": "integer", "description": "Máximo de animais (1 a 100)"}},
          ["lote", "categoria", "status", "limite"]),
    _tool("pesagens_animal", "Pesagens e ganho médio diário (GMD) de um animal pelo brinco.",
          {"brinco": {"type": "string"}}, ["brinco"]),
    _tool("sanidade", "Vacinas e tratamentos vencidos, próximos e últimos registros sanitários.",
          {"dias": {"type": "integer", "description": "Janela de dias à frente para 'próximas' (1 a 180)"}}, ["dias"]),
    _tool("reproducao", "Situação reprodutiva das fêmeas, taxa de prenhez e próximos partos previstos."),
    _tool("estoque", "Insumos em estoque (quantidade, mínimo, custo médio, validade) e últimas movimentações."),
    _tool("financeiro", "Receitas, despesas, resultado, caixa, contas em aberto e série de 12 meses. Exige permissão financeira.",
          {"periodo": {"type": "string", "enum": list(indicadores.PERIODOS)}}, ["periodo"]),
    _tool("custos_producao", "Custo de produção por lote e por cabeça no período. Exige permissão financeira.",
          {"periodo": {"type": "string", "enum": list(indicadores.PERIODOS)}}, ["periodo"]),
    _tool("meus_anuncios", "Anúncios do próprio usuário no marketplace."),
    _tool("cotacoes", "Cotações mais recentes disponíveis no sistema, com fonte, praça, unidade, data, estado e variação.",
          {"produto": {"type": ["string", "null"], "description": "Parte do nome (ex.: boi, dólar, soja)"},
           "categoria": {"type": ["string", "null"],
                         "description": "Uma de: " + ", ".join(list(servico_cotacoes.CATEGORIAS) + ["outros"])}},
          ["produto", "categoria"]),
    _tool("historico_cotacao", "Histórico diário e variação no período de uma série de cotação (use a 'chave' da ferramenta cotacoes).",
          {"chave": {"type": "string"}, "dias": {"type": "integer", "enum": [7, 30, 90, 180, 365]}}, ["chave", "dias"]),
    _tool("estimar_valor_rebanho", "Estima o valor de referência dos bovinos ativos com pesagem usando a cotação de boi gordo (R$/@) disponível.",
          {"lote": {"type": ["string", "null"]},
           "rendimento_carcaca_pct": {"type": "number", "description": "Rendimento de carcaça em % (padrão 50)"}},
          ["lote", "rendimento_carcaca_pct"]),
]

EXECUTORES = {
    "resumo_propriedade": t_resumo_propriedade, "listar_animais": t_listar_animais, "pesagens_animal": t_pesagens_animal,
    "sanidade": t_sanidade, "reproducao": t_reproducao, "estoque": t_estoque, "financeiro": t_financeiro,
    "custos_producao": t_custos_producao, "meus_anuncios": t_meus_anuncios, "cotacoes": t_cotacoes, "historico_cotacao": t_historico_cotacao,
    "estimar_valor_rebanho": t_estimar_valor_rebanho,
}


def executar_ferramenta(ctx, nome, entrada):
    fn = EXECUTORES.get(nome)
    if fn is None:
        return {"erro": "Ferramenta desconhecida."}, True
    try:
        args = {k: v for k, v in (entrada or {}).items() if v is not None}
        return fn(ctx, **args), False
    except TypeError:
        return {"erro": "Parâmetros inválidos para a ferramenta."}, True
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Falha na ferramenta da IA %s", nome)
        db.session.rollback()
        return {"erro": "Falha interna ao consultar os dados."}, True


# ------------------------------------------------------------------ limites, cliente e conversa

def configurada():
    return bool(current_app.config.get("ANTHROPIC_API_KEY"))


# Resultado da última validação real da chave (por processo). Sucesso vale 10 min; falha, 2 min.
_status = {"estado": None, "detalhe": None, "em": None}
MOTIVOS = {
    "ok": "Integração validada com o provedor.",
    "sem_chave": "ANTHROPIC_API_KEY não está definida no ambiente do servidor (.env na pasta do projeto).",
    "chave_invalida": "A chave foi recusada pela Anthropic (inválida ou revogada). Gere outra no Console.",
    "sem_permissao": "A chave não tem permissão para usar o modelo configurado.",
    "modelo_indisponivel": "O modelo configurado em AI_MODEL não foi encontrado para esta conta.",
    "sem_creditos": "A conta da API está sem créditos ou sem cobrança habilitada (Console › Billing).",
    "sem_conexao": "O servidor não conseguiu se conectar a api.anthropic.com (rede ou lista de sites permitidos).",
    "limite": "Limite de requisições da conta da API atingido no momento.",
    "erro_provedor": "O provedor respondeu com erro. Veja o log do servidor.",
}


def _registrar_status(estado, detalhe=None):
    _status.update(estado=estado, detalhe=detalhe, em=time.monotonic())


def status(forcar=False):
    """Valida de verdade a chave com uma chamada autenticada sem custo de tokens (consulta do modelo).

    Retorna {"estado", "motivo"}; o valor da chave nunca é incluído.
    """
    import anthropic

    if not configurada():
        return {"estado": "sem_chave", "motivo": MOTIVOS["sem_chave"]}
    validade = 600 if _status["estado"] == "ok" else 120
    if not forcar and _status["em"] and time.monotonic() - _status["em"] < validade:
        return {"estado": _status["estado"], "motivo": MOTIVOS[_status["estado"]]}
    try:
        cliente().with_options(timeout=8, max_retries=0).models.retrieve(current_app.config["AI_MODEL"])
        _registrar_status("ok")
    except anthropic.AuthenticationError:
        _registrar_status("chave_invalida")
    except anthropic.PermissionDeniedError:
        _registrar_status("sem_permissao")
    except anthropic.NotFoundError:
        _registrar_status("modelo_indisponivel")
    except anthropic.RateLimitError:
        _registrar_status("limite")
    except (anthropic.APIConnectionError, anthropic.APITimeoutError):
        _registrar_status("sem_conexao")
    except anthropic.APIStatusError as exc:
        current_app.logger.error("IA Agro: validação recebeu erro %s do provedor", exc.status_code)
        _registrar_status("erro_provedor")
    if _status["estado"] != "ok":
        current_app.logger.warning("IA Agro indisponível: %s", MOTIVOS[_status["estado"]])
    return {"estado": _status["estado"], "motivo": MOTIVOS[_status["estado"]]}


def perguntas_restantes(user_id):
    usadas = (db.session.query(MensagemIA.id).join(ConversaIA)
              .filter(ConversaIA.user_id == user_id, MensagemIA.papel == "user",
                      MensagemIA.created_at >= utcnow() - timedelta(days=1)).count())
    return max(0, current_app.config["AI_DAILY_LIMIT"] - usadas)


def verificar_limites(user_id):
    cfg = current_app.config
    base = (db.session.query(MensagemIA.id).join(ConversaIA)
            .filter(ConversaIA.user_id == user_id, MensagemIA.papel == "user"))
    agora = utcnow()
    if base.filter(MensagemIA.created_at >= agora - timedelta(minutes=1)).count() >= cfg["AI_MINUTE_LIMIT"]:
        raise LimiteExcedido("Muitas perguntas seguidas. Aguarde um minuto e tente novamente.")
    if base.filter(MensagemIA.created_at >= agora - timedelta(days=1)).count() >= cfg["AI_DAILY_LIMIT"]:
        raise LimiteExcedido(f"Limite diário de {cfg['AI_DAILY_LIMIT']} perguntas atingido. Tente novamente amanhã.")
    tokens = (db.session.query(db.func.coalesce(db.func.sum(MensagemIA.tokens_entrada + MensagemIA.tokens_saida), 0))
              .join(ConversaIA).filter(ConversaIA.user_id == user_id, MensagemIA.papel == "assistant",
                                       MensagemIA.created_at >= agora - timedelta(days=1)).scalar())
    if tokens >= cfg["AI_DAILY_TOKEN_LIMIT"]:
        raise LimiteExcedido("Limite diário de uso da IA atingido (controle de custos). Tente novamente amanhã.")


_cliente = None


def cliente():
    """Cliente do SDK oficial. Criado sob demanda; a chave vem somente do ambiente do servidor."""
    global _cliente
    if _cliente is None:
        import anthropic

        cfg = current_app.config
        _cliente = anthropic.Anthropic(api_key=cfg["ANTHROPIC_API_KEY"], timeout=cfg["AI_TIMEOUT_SECONDS"], max_retries=1)
    return _cliente


def _contexto_texto(ctx):
    papel = next((v["label"] for v in PAPEIS_PROPRIEDADE.values() if v["perms"] == ctx.perms), "sem vínculo")
    prop = f"Propriedade ativa: {ctx.propriedade.nome} ({ctx.propriedade.municipio}/{ctx.propriedade.uf})." if ctx.propriedade \
        else "O usuário não tem propriedade ativa."
    return (f"Contexto do servidor: hoje é {date.today():%d/%m/%Y}. {prop} Papel do usuário: {papel}. "
            f"Acesso ao financeiro: {'sim' if 'financeiro' in ctx.perms else 'não'}.")


def _chamar(msgs, timeout=None):
    import anthropic

    cfg = current_app.config
    try:
        return cliente().with_options(timeout=timeout or cfg["AI_TIMEOUT_SECONDS"]).beta.messages.create(
            model=cfg["AI_MODEL"], max_tokens=cfg["AI_MAX_TOKENS"], system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            tools=TOOLS, messages=msgs, output_config={"effort": cfg["AI_EFFORT"]},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.AuthenticationError as exc:
        _registrar_status("chave_invalida")
        current_app.logger.error("IA Agro: chave da Anthropic inválida ou revogada")
        raise IAIndisponivel(INDISPONIVEL) from exc
    except anthropic.PermissionDeniedError as exc:
        _registrar_status("sem_permissao")
        current_app.logger.error("IA Agro: chave sem permissão para o modelo %s", cfg["AI_MODEL"])
        raise IAIndisponivel(INDISPONIVEL) from exc
    except anthropic.NotFoundError as exc:
        _registrar_status("modelo_indisponivel")
        current_app.logger.error("IA Agro: modelo %s não encontrado", cfg["AI_MODEL"])
        raise IAIndisponivel(INDISPONIVEL) from exc
    except anthropic.RateLimitError as exc:
        current_app.logger.warning("IA Agro: limite de uso do provedor atingido")
        raise IAIndisponivel("A IA Agro está muito requisitada agora. Tente novamente em alguns minutos.") from exc
    except anthropic.BadRequestError as exc:
        mensagem = str(getattr(exc, "message", "") or "")
        if "credit" in mensagem.lower() or "billing" in mensagem.lower():
            _registrar_status("sem_creditos")
            current_app.logger.error("IA Agro: conta da API sem créditos ou sem cobrança habilitada")
            raise IAIndisponivel(INDISPONIVEL) from exc
        current_app.logger.error("IA Agro: requisição recusada pelo provedor: %s", mensagem[:300])
        raise IAIndisponivel("Não foi possível processar esta pergunta. Tente reformular ou iniciar uma nova conversa.") from exc
    except anthropic.APITimeoutError as exc:
        raise IAIndisponivel("A IA Agro demorou demais para responder. Tente novamente.") from exc
    except anthropic.APIConnectionError as exc:
        _registrar_status("sem_conexao")
        current_app.logger.warning("IA Agro: sem conexão com o provedor")
        raise IAIndisponivel("Sem conexão com o serviço de IA no momento. Tente novamente em instantes.") from exc
    except anthropic.APIStatusError as exc:
        current_app.logger.error("IA Agro: erro %s do provedor", exc.status_code)
        raise IAIndisponivel("O serviço de IA está instável no momento. Tente novamente em alguns minutos.") from exc


def responder(conversa, pergunta, ctx, pergunta_id=None):
    """Executa o ciclo pergunta → ferramentas → resposta. Retorna (texto, ferramentas_usadas, uso).

    O histórico vai como texto (append-only); falhas e a própria pergunta atual (`pergunta_id`) ficam de fora.
    """
    historico = [m for m in conversa.mensagens if not m.erro and m.id != pergunta_id][-20:]
    while historico and historico[0].papel != "user":  # a API exige que a conversa comece pelo usuário
        historico.pop(0)
    msgs = [{"role": m.papel, "content": m.conteudo} for m in historico]
    msgs.append({"role": "user", "content": pergunta})
    msgs.append({"role": "system", "content": _contexto_texto(ctx)})
    usadas, uso = [], {"entrada": 0, "saida": 0}
    cfg = current_app.config
    prazo = time.monotonic() + cfg["AI_TOTAL_SECONDS"]
    for _ in range(cfg["AI_MAX_TOOL_ROUNDS"] + 1):
        restante = prazo - time.monotonic()
        if restante < 10:
            raise IAIndisponivel("A IA Agro demorou demais para responder. Tente uma pergunta mais específica.")
        resp = _chamar(msgs, timeout=min(cfg["AI_TIMEOUT_SECONDS"], restante))
        uso["entrada"] += getattr(resp.usage, "input_tokens", 0) or 0
        uso["saida"] += getattr(resp.usage, "output_tokens", 0) or 0
        if resp.stop_reason == "refusal":
            return "Não posso ajudar com esse pedido. Posso ajudar com manejo, gestão e dados da sua propriedade.", usadas, uso
        if resp.stop_reason != "tool_use":
            texto = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if resp.stop_reason == "max_tokens":
                texto += "\n\n(A resposta foi interrompida por ser longa demais. Peça um resumo ou detalhe uma parte.)"
            return texto or "Não consegui formular uma resposta. Tente reformular a pergunta.", usadas, uso
        msgs.append({"role": "assistant", "content": resp.content})
        resultados = []
        for b in resp.content:
            if b.type != "tool_use":
                continue
            usadas.append(b.name)
            saida, erro = executar_ferramenta(ctx, b.name, b.input)
            resultados.append({"type": "tool_result", "tool_use_id": b.id, "is_error": erro,
                               "content": json.dumps(saida, ensure_ascii=False, default=str)})
        msgs.append({"role": "user", "content": resultados})
    raise IAIndisponivel("A pergunta exigiu consultas demais. Tente uma pergunta mais específica.")
