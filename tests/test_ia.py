"""IA Agro com provedor SIMULADO (nenhuma chamada real). Testes reais ficam em tests/test_ia_real.py."""
import json
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.extensions import db
from app.models import Animal, ConversaIA, Cotacao, Lancamento, MensagemIA
from app.services import ia_agro
from tests.conftest import adicionar_membro, criar_propriedade, criar_usuario, login, logout


class FakeProvedor:
    """Responde com uma sequência roteirizada e guarda as requisições recebidas."""

    def __init__(self, roteiro, validacao=None):
        self.roteiro = list(roteiro)
        self.requisicoes = []
        self.validacao = validacao  # exceção a levantar na validação da chave (models.retrieve)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.models = SimpleNamespace(retrieve=self._retrieve)
        self.messages = SimpleNamespace(create=self._mensagem_minima)
        self.minimas = 0

    def _mensagem_minima(self, **_kw):
        self.minimas += 1
        if self.validacao:
            raise self.validacao
        return texto("ok")

    def with_options(self, **_kw):
        return self

    def _retrieve(self, model):
        if self.validacao:
            raise self.validacao
        return SimpleNamespace(id=model)

    def _create(self, **kw):
        self.requisicoes.append(json.loads(json.dumps(kw, default=lambda o: getattr(o, "__dict__", str(o)))))
        passo = self.roteiro.pop(0)
        if isinstance(passo, Exception):
            raise passo
        return passo


def texto(t, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, content=[SimpleNamespace(type="text", text=t)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def ferramenta(nome, entrada, uid="tu_1"):
    return SimpleNamespace(stop_reason="tool_use", content=[SimpleNamespace(type="tool_use", id=uid, name=nome, input=entrada)],
                           usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def resultados_de_ferramenta(req):
    ultimo = req["messages"][-1]
    return [json.loads(b["content"]) for b in ultimo["content"] if b.get("type") == "tool_result"]


@pytest.fixture(autouse=True)
def _estado_limpo():
    ia_agro._status.update(estado=None, detalhe=None, em=None)
    yield
    ia_agro._status.update(estado=None, detalhe=None, em=None)


@pytest.fixture()
def ia(app, monkeypatch):
    app.config["ANTHROPIC_API_KEY"] = "chave-ficticia-de-teste"

    def instalar(roteiro, validacao=None):
        fake = FakeProvedor(roteiro, validacao)
        monkeypatch.setattr(ia_agro, "cliente", lambda: fake)
        return fake
    return instalar


def perguntar(client, texto_, conversa_id=None):
    return client.post("/ia/perguntar", json={"pergunta": texto_, "conversa_id": conversa_id})


def _animal(prop, brinco, peso=None):
    a = Animal(propriedade_id=prop.id, brinco=brinco, sexo="M", especie="bovino", categoria="Garrote",
               data_entrada=date.today(), peso_atual=peso, data_peso_atual=date.today() if peso else None)
    db.session.add(a)
    db.session.commit()
    return a


def _req():
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_sem_chave_aviso_generico_para_usuario_e_detalhado_para_admin(app, client, produtor):
    pagina = client.get("/ia/").get_data(as_text=True)
    assert "ainda não está disponível" in pagina and "ANTHROPIC_API_KEY" not in pagina
    assert 'id="ia-pergunta"' in pagina and "disabled" in pagina
    assert perguntar(client, "Oi").status_code == 503
    logout(client)
    criar_usuario(email="adm@teste.com", admin=True)
    login(client, "adm@teste.com")
    pagina = client.get("/ia/").get_data(as_text=True)
    assert "ANTHROPIC_API_KEY" in pagina and "Verificar configuração agora" in pagina


def test_chave_invalida_bloqueia_envio_sem_expor_a_chave(app, client, produtor, ia):
    fake = ia([texto("nunca chamado")], validacao=anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=_req()), body=None))
    pagina = client.get("/ia/").get_data(as_text=True)
    assert "temporariamente indisponível" in pagina and "chave-ficticia" not in pagina
    r = perguntar(client, "Oi")
    assert r.status_code == 503 and r.get_json()["estado"] == "chave_invalida"
    assert fake.requisicoes == []  # nenhuma pergunta enviada ao provedor


def test_chave_validada_remove_aviso(app, client, produtor, ia):
    ia([])
    pagina = client.get("/ia/").get_data(as_text=True)
    assert "indisponível" not in pagina and "ainda não está" not in pagina
    assert 'data-restantes>40<' in pagina


def test_sem_creditos_detectado_na_pergunta_e_mostrado_ao_admin(app, client, ia):
    criar_usuario(email="adm@teste.com", admin=True)
    login(client, "adm@teste.com")
    ia([anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                  response=httpx2.Response(400, request=_req()), body=None)])
    r = perguntar(client, "Oi")
    assert r.status_code == 502 and "temporariamente indisponível" in r.get_json()["mensagem"]["texto"]
    assert "sem créditos" in client.get("/ia/").get_data(as_text=True)
    r = perguntar(client, "Oi de novo")
    assert r.status_code == 503 and r.get_json()["estado"] == "sem_creditos"


def test_nova_conversa_e_independente_e_saldo_diminui(app, client, produtor, ia):
    ia([texto("um"), texto("dois")])
    a = perguntar(client, "Primeira").get_json()
    b = perguntar(client, "Outra conversa").get_json()
    assert a["conversa_id"] != b["conversa_id"]
    assert b["restantes"] == a["restantes"] - 1 == 38
    assert [c.titulo for c in ConversaIA.query.order_by(ConversaIA.id)] == ["Primeira", "Outra conversa"]


def test_cli_ia_verificar_sem_chave(app):
    r = app.test_cli_runner().invoke(args=["ia-verificar"])
    assert r.exit_code == 1 and "sem_chave" in r.output


def test_pergunta_resposta_e_historico(app, client, produtor, ia):
    fake = ia([texto("Olá! Posso ajudar com sua fazenda.")])
    r = perguntar(client, "Olá")
    assert r.status_code == 200
    d = r.get_json()
    assert d["mensagem"]["texto"] == "Olá! Posso ajudar com sua fazenda."
    conv = db.session.get(ConversaIA, d["conversa_id"])
    assert [m.papel for m in conv.mensagens] == ["user", "assistant"]
    req = fake.requisicoes[0]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": "medium"}
    assert req["messages"][-1]["role"] == "system" and "Fazenda Boa Vista" in req["messages"][-1]["content"]
    assert "chave-ficticia" not in json.dumps(req)  # a chave não vai no corpo
    fake.roteiro.append(texto("Segunda resposta"))
    perguntar(client, "E agora?", conversa_id=conv.id)
    hist = fake.requisicoes[1]["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant", "user", "system"]
    assert "Olá" in client.get(f"/ia/conversa/{conv.id}").get_data(as_text=True)


def test_ferramentas_so_veem_a_propriedade_ativa(app, client, produtor, ia):
    _, prop = produtor
    _animal(prop, "MEU-01", Decimal("400"))
    outro = criar_usuario(nome="Outro Produtor", email="outro@teste.com")
    prop_outro = criar_propriedade(outro, nome="Fazenda Alheia")
    _animal(prop_outro, "ALHEIO-99", Decimal("500"))
    fake = ia([ferramenta("listar_animais", {"lote": None, "categoria": None, "status": "todos", "limite": 100}),
               texto("Você tem 1 animal.")])
    r = perguntar(client, "Liste meus animais")
    assert r.status_code == 200 and r.get_json()["mensagem"]["ferramentas"] == ["listar_animais"]
    resultado = resultados_de_ferramenta(fake.requisicoes[1])[0]
    assert [a["brinco"] for a in resultado["animais"]] == ["MEU-01"]
    assert "ALHEIO" not in json.dumps(fake.requisicoes)
    fake.roteiro += [ferramenta("pesagens_animal", {"brinco": "ALHEIO-99"}), texto("Não encontrado.")]
    perguntar(client, "Pesagens do ALHEIO-99")
    assert "Nenhum animal" in resultados_de_ferramenta(fake.requisicoes[3])[0]["erro"]


def test_resumo_nao_envia_dados_sensiveis(app, client, produtor, ia):
    _, prop = produtor
    prop.endereco, prop.latitude, prop.longitude = "Estrada Secreta km 9", -19.75, -47.93
    db.session.commit()
    fake = ia([ferramenta("resumo_propriedade", {}), texto("Resumo pronto.")])
    perguntar(client, "Resumo da fazenda")
    enviado = json.dumps(fake.requisicoes, ensure_ascii=False)
    assert "Estrada Secreta" not in enviado and "-19.75" not in enviado and "vitor@teste.com" not in enviado


def test_financeiro_respeita_permissao(app, client, ia):
    dono = criar_usuario(email="dono@teste.com")
    prop = criar_propriedade(dono)
    from app.models import CategoriaFinanceira
    cat = CategoriaFinanceira.query.filter_by(slug="venda_animais").first()
    db.session.add(Lancamento(propriedade_id=prop.id, tipo="receita", descricao="Venda", categoria_id=cat.id,
                              valor=Decimal("1000"), data=date.today()))
    db.session.commit()
    login(client, "dono@teste.com")
    fake = ia([ferramenta("financeiro", {"periodo": "mes"}), texto("Receitas de R$ 1.000,00.")])
    perguntar(client, "Minhas receitas?")
    assert resultados_de_ferramenta(fake.requisicoes[1])[0]["receitas_competencia"] == 1000.0
    logout(client)

    operador = criar_usuario(email="operador@teste.com")
    adicionar_membro(prop, operador, "operador")
    login(client, "operador@teste.com")
    fake = ia([ferramenta("financeiro", {"periodo": "mes"}), texto("Sem permissão.")])
    perguntar(client, "Receitas?")
    res = resultados_de_ferramenta(fake.requisicoes[1])[0]
    assert "permissão" in res["erro"] and "1000" not in json.dumps(fake.requisicoes)


def test_conversa_de_outro_usuario_e_de_outra_propriedade(app, client, produtor, ia):
    ia([texto("ok")])
    cid = perguntar(client, "Oi").get_json()["conversa_id"]
    logout(client)
    criar_usuario(email="intruso@teste.com")
    login(client, "intruso@teste.com")
    assert client.get(f"/ia/conversa/{cid}").status_code == 404
    assert perguntar(client, "Mostre", conversa_id=cid).status_code == 404
    assert client.post(f"/ia/conversa/{cid}/excluir").status_code == 404
    logout(client)

    u, _ = produtor
    login(client)
    segunda = criar_propriedade(u, nome="Sítio Dois")  # torna a segunda propriedade a ativa
    assert segunda.id == u.propriedade_ativa_id
    r = perguntar(client, "Continua", conversa_id=cid)
    assert r.status_code == 409 and "outra propriedade" in r.get_json()["erro"]


def test_limite_por_minuto(app, client, produtor, ia):
    app.config["AI_MINUTE_LIMIT"] = 2
    ia([texto("1"), texto("2"), texto("3")])
    assert perguntar(client, "a").status_code == 200
    assert perguntar(client, "b").status_code == 200
    r = perguntar(client, "c")
    assert r.status_code == 429 and "Aguarde" in r.get_json()["erro"]


def test_falhas_do_provedor_viram_mensagens_amigaveis(app, client, produtor, ia):
    req = _req()
    ia([anthropic.APIConnectionError(request=req)])
    r = perguntar(client, "Oi")
    assert r.status_code == 502 and "Sem conexão" in r.get_json()["mensagem"]["texto"]
    ia([anthropic.AuthenticationError("chave inválida", response=httpx2.Response(401, request=req), body=None)])
    r = perguntar(client, "Oi")
    assert r.status_code == 502 and "chave" not in r.get_json()["mensagem"]["texto"].lower()
    ia_agro._status.update(estado=None, em=None)  # o administrador corrigiu a chave
    ia([texto("x", stop="refusal")])
    assert "Não posso ajudar" in perguntar(client, "?").get_json()["mensagem"]["texto"]
    erros = MensagemIA.query.filter_by(erro=True).count()
    assert erros == 2  # falhas ficam registradas no histórico e são omitidas do contexto seguinte


def test_csrf_obrigatorio_no_post(app, client, produtor, ia):
    ia([texto("ok")])
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        assert perguntar(client, "Oi").status_code == 400
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_estimativa_do_rebanho_mostra_formula_e_fonte(app, client, produtor, ia):
    _, prop = produtor
    _animal(prop, "B1", Decimal("450"))
    _animal(prop, "B2", Decimal("510"))
    _animal(prop, "B3")  # sem pesagem
    db.session.add(Cotacao(produto="Boi gordo", praca="Estado de SP", uf="SP", valor=Decimal("300"), unidade="R$/@",
                           fonte="Fonte de teste", data_referencia=date.today()))
    db.session.commit()
    fake = ia([ferramenta("estimar_valor_rebanho", {"lote": None, "rendimento_carcaca_pct": 50}), texto("Estimativa pronta.")])
    perguntar(client, "Quanto vale meu rebanho?")
    res = resultados_de_ferramenta(fake.requisicoes[1])[0]
    # 960 kg × 50% ÷ 15 = 32 @ × R$ 300 = R$ 9.600
    assert res["arrobas_estimadas"] == 32.0 and res["valor_estimado_reais"] == 9600.0
    assert res["sem_pesagem"] == 1 and "formula" in res and res["preco_usado"]["fonte"] == "Fonte de teste"


def test_estimativa_sem_cotacao_nao_inventa_preco(app, client, produtor, ia):
    _, prop = produtor
    _animal(prop, "B1", Decimal("450"))
    fake = ia([ferramenta("estimar_valor_rebanho", {"lote": None, "rendimento_carcaca_pct": 50}), texto("Sem cotação.")])
    perguntar(client, "Quanto vale?")
    res = resultados_de_ferramenta(fake.requisicoes[1])[0]
    assert "Não há cotação" in res["erro"] and "valor_estimado_reais" not in res


def test_ferramentas_tem_esquema_estrito_valido(app):
    for t in ia_agro.TOOLS:
        s = t["input_schema"]
        assert t["strict"] is True and s["additionalProperties"] is False
        assert set(s["required"]) == set(s["properties"])
        assert t["name"] in ia_agro.EXECUTORES


def test_custos_e_estoque_respeitam_permissao_financeira(app, client, ia):
    from app.services.estoque import registrar_entrada
    from app.models import Insumo
    dono = criar_usuario(email="dono2@teste.com")
    prop = criar_propriedade(dono)
    ins = Insumo(propriedade_id=prop.id, nome="Ração", unidade="kg")
    db.session.add(ins)
    db.session.flush()
    registrar_entrada(ins, Decimal("100"), date.today(), dono.id, custo_unitario=Decimal("2.5"))
    db.session.commit()
    operador = criar_usuario(email="op2@teste.com")
    adicionar_membro(prop, operador, "operador")
    login(client, "op2@teste.com")
    fake = ia([ferramenta("estoque", {}, "t1"), ferramenta("custos_producao", {"periodo": "ano"}, "t2"), texto("ok")])
    perguntar(client, "Estoque e custos?")
    estoque = resultados_de_ferramenta(fake.requisicoes[1])[0]
    custos = resultados_de_ferramenta(fake.requisicoes[2])[0]
    assert estoque["movimentacoes_recentes"][0]["insumo"] == "Ração" and "custo_total" not in estoque["movimentacoes_recentes"][0]
    assert "permissão" in custos["erro"]


def test_historico_longo_sempre_comeca_pelo_usuario(app, client, produtor, ia):
    u, prop = produtor
    conv = ConversaIA(user_id=u.id, propriedade_id=prop.id, titulo="Longa")
    db.session.add(conv)
    db.session.flush()
    for i in range(21):  # u, a, u, ..., u  → os últimos 20 começariam por "assistant"
        db.session.add(MensagemIA(conversa_id=conv.id, papel="user" if i % 2 == 0 else "assistant", conteudo=f"m{i}",
                                  created_at=datetime(2026, 1, 1, 10, i)))  # histórico antigo
    db.session.commit()
    fake = ia([texto("ok")])
    assert perguntar(client, "Mais uma", conversa_id=conv.id).status_code == 200
    papeis = [m["role"] for m in fake.requisicoes[0]["messages"]]
    assert papeis[0] == "user" and papeis[-2:] == ["user", "system"]


def test_prazo_total_esgotado_nao_chama_provedor(app, client, produtor, ia):
    app.config["AI_TOTAL_SECONDS"] = 5
    fake = ia([texto("nunca")])
    r = perguntar(client, "Oi")
    assert r.status_code == 502 and "demorou demais" in r.get_json()["mensagem"]["texto"]
    assert fake.requisicoes == []


def test_limite_diario_de_tokens(app, client, produtor, ia):
    u, prop = produtor
    conv = ConversaIA(user_id=u.id, propriedade_id=prop.id)
    db.session.add(conv)
    db.session.flush()
    db.session.add(MensagemIA(conversa_id=conv.id, papel="assistant", conteudo="x", tokens_entrada=390000, tokens_saida=10000))
    db.session.commit()
    ia([texto("nunca")])
    r = perguntar(client, "Oi")
    assert r.status_code == 429 and "controle de custos" in r.get_json()["erro"]


def test_dados_reais_de_pesagem_sanidade_e_reproducao(app, client, produtor, ia):
    from app.models import EventoReprodutivo, EventoSanitario, Pesagem
    _, prop = produtor
    a = _animal(prop, "V10", Decimal("420"))
    a.sexo, a.situacao_reprodutiva = "F", "gestante"
    hoje = date.today()
    from datetime import timedelta
    db.session.add_all([
        Pesagem(propriedade_id=prop.id, animal_id=a.id, data=hoje - timedelta(days=30), peso_kg=Decimal("390")),
        Pesagem(propriedade_id=prop.id, animal_id=a.id, data=hoje, peso_kg=Decimal("420")),
        EventoSanitario(propriedade_id=prop.id, animal_id=a.id, tipo="vacina", produto="Brucelose",
                        data_aplicacao=hoje - timedelta(days=200), proxima_data=hoje - timedelta(days=1)),
        EventoReprodutivo(propriedade_id=prop.id, animal_id=a.id, tipo="diagnostico", data=hoje - timedelta(days=100),
                          diagnostico_resultado="positivo", previsao_parto=hoje + timedelta(days=150)),
    ])
    db.session.commit()
    fake = ia([ferramenta("pesagens_animal", {"brinco": "V10"}, "a"), ferramenta("sanidade", {"dias": 30}, "b"),
               ferramenta("reproducao", {}, "c"), texto("ok")])
    perguntar(client, "Como está a V10?")
    pes, san, rep = (resultados_de_ferramenta(fake.requisicoes[i])[0] for i in (1, 2, 3))
    assert pes["gmd"] == {"kg_dia": 1.0, "dias": 30, "ganho_kg": 30.0}
    assert san["vencidas"][0]["produto"] == "Brucelose" and san["vencidas"][0]["alvo"] == "animal V10"
    assert rep["resumo_femeas_ativas"]["gestantes"] == 1 and rep["proximos_partos_previstos"][0]["brinco"] == "V10"


def test_propriedade_sem_dados_nao_inventa_valores(app, client, produtor, ia):
    fake = ia([ferramenta("financeiro", {"periodo": "mes"}, "a"), ferramenta("sanidade", {"dias": 30}, "b"),
               ferramenta("reproducao", {}, "c"), ferramenta("listar_animais", {"lote": None, "categoria": None,
                                                                                "status": "ativo", "limite": 10}, "d"),
               texto("Ainda não há dados.")])
    perguntar(client, "Resumo geral")
    fin, san, rep, ani = (resultados_de_ferramenta(fake.requisicoes[i])[0] for i in (1, 2, 3, 4))
    assert fin["tem_lancamentos"] is False and fin["receitas_competencia"] == 0.0
    assert san["vencidas"] == [] and rep["resumo_femeas_ativas"]["taxa_prenhez"] is None
    assert ani["total_encontrado"] == 0


def test_usuario_sem_propriedade_recebe_orientacao(app, client, ia):
    criar_usuario(email="semfazenda@teste.com")
    login(client, "semfazenda@teste.com")
    fake = ia([ferramenta("resumo_propriedade", {}), texto("Cadastre uma propriedade.")])
    assert perguntar(client, "Resumo").status_code == 200
    assert "Nenhuma propriedade ativa" in resultados_de_ferramenta(fake.requisicoes[1])[0]["erro"]


def test_verificacao_com_mensagem_detecta_falta_de_creditos_e_libera_depois(app, client, produtor, ia):
    sem_credito = anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                            response=httpx2.Response(400, request=_req()), body=None)
    fake = ia([], validacao=sem_credito)
    r = app.test_cli_runner().invoke(args=["ia-verificar"])
    assert r.exit_code == 1 and "sem_creditos" in r.output and "chave-ficticia" not in r.output
    assert fake.minimas == 1
    # Com a chave válida mas sem créditos, a página não pode dizer que a IA está disponível
    assert "temporariamente indisponível" in client.get("/ia/").get_data(as_text=True)
    assert perguntar(client, "Oi").status_code == 503
    # Créditos adicionados: a revalidação (forçada) envia mensagem mínima e libera a IA
    fake.validacao = None
    fake.roteiro.append(texto("Funcionando."))
    assert ia_agro.status(forcar=True)["estado"] == "ok"
    assert perguntar(client, "Oi").status_code == 200
