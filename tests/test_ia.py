"""IA Agro com provedor SIMULADO (nenhuma chamada real). Testes reais ficam em tests/test_ia_real.py."""
import json
from datetime import date
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

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.requisicoes = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

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


@pytest.fixture()
def ia(app, monkeypatch):
    app.config["ANTHROPIC_API_KEY"] = "chave-ficticia-de-teste"

    def instalar(roteiro):
        fake = FakeProvedor(roteiro)
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


def test_sem_chave_mostra_aviso_e_nao_chama_provedor(app, client, produtor):
    pagina = client.get("/ia/").get_data(as_text=True)
    assert "ANTHROPIC_API_KEY" in pagina and "chave-ficticia" not in pagina
    assert perguntar(client, "Oi").status_code == 503


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
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    ia([anthropic.APIConnectionError(request=req)])
    r = perguntar(client, "Oi")
    assert r.status_code == 502 and "Sem conexão" in r.get_json()["mensagem"]["texto"]
    ia([anthropic.AuthenticationError("chave inválida", response=httpx2.Response(401, request=req), body=None)])
    r = perguntar(client, "Oi")
    assert r.status_code == 502 and "chave" not in r.get_json()["mensagem"]["texto"].lower()
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
