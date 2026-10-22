from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.models import (
    Animal, EventoReprodutivo, EventoSanitario, Insumo, Lancamento, Lote, MovimentacaoAnimal, Pesagem, Propriedade,
)
from app.services import custos, indicadores

from conftest import adicionar_membro, criar_propriedade, criar_usuario, hoje, login


def _animal(client, brinco, **extra):
    dados = {"brinco": brinco, "especie": "bovino", "sexo": "F", "origem": "nascido", "data_entrada": "2024-01-10",
             "situacao_reprodutiva": "vazia", "situacao_sanitaria": "saudavel"}
    dados.update(extra)
    return client.post("/rebanho/novo", data=dados)


def test_cadastro_de_propriedade_e_propriedade_ativa(app, client):
    criar_usuario()
    login(client)
    r = client.post("/propriedades/nova", data={"nome": "Sítio Esperança", "municipio": "Lavras", "uf": "MG", "atividade": "pecuaria_leite",
                                                "area_total_ha": "50", "area_pastagem_ha": "60"})
    assert "não pode superar a área total" in r.get_data(as_text=True)
    r = client.post("/propriedades/nova", data={"nome": "Sítio Esperança", "municipio": "Lavras", "uf": "MG", "atividade": "pecuaria_leite",
                                                "area_total_ha": "50,5", "area_pastagem_ha": "30"})
    assert r.status_code == 302
    p = Propriedade.query.filter_by(nome="Sítio Esperança").one()
    assert p.area_total_ha == Decimal("50.50")
    assert "Sítio Esperança" in client.get("/painel").get_data(as_text=True)


def test_animais_brinco_unico_por_propriedade(app, client, produtor):
    u, prop = produtor
    assert _animal(client, "1023", peso_inicial="350,5").status_code == 302
    a = Animal.query.filter_by(brinco="1023").one()
    assert a.peso_atual == Decimal("350.50") and Pesagem.query.filter_by(animal_id=a.id).count() == 1
    assert MovimentacaoAnimal.query.filter_by(animal_id=a.id, tipo="entrada").count() == 1
    r = _animal(client, "1023")
    assert "Já existe um animal com esta identificação" in r.get_data(as_text=True)
    # O mesmo brinco é permitido em outra propriedade
    outra = criar_propriedade(u, nome="Retiro")
    client.post(f"/propriedades/{outra.id}/ativar", data={"next": "/painel"})
    assert _animal(client, "1023").status_code == 302
    assert Animal.query.filter_by(brinco="1023").count() == 2


def test_editar_animal_e_filtros(app, client, produtor):
    _animal(client, "A1")
    _animal(client, "B2", sexo="M", situacao_reprodutiva="nao_aplicavel")
    a = Animal.query.filter_by(brinco="A1").one()
    lote = Lote(propriedade_id=a.propriedade_id, nome="Matrizes")
    db.session.add(lote)
    db.session.commit()
    r = client.post(f"/rebanho/{a.id}/editar", data={"brinco": "A1", "especie": "bovino", "sexo": "F", "origem": "nascido",
                                                    "data_entrada": "2024-01-10", "lote_id": lote.id, "raca": "Gir",
                                                    "situacao_reprodutiva": "vazia", "situacao_sanitaria": "saudavel"})
    assert r.status_code == 302 and a.lote_id == lote.id and a.raca == "Gir"
    assert MovimentacaoAnimal.query.filter_by(animal_id=a.id, tipo="troca_lote").count() == 1
    html = client.get("/rebanho/?sexo=M").get_data(as_text=True)
    assert "B2" in html and ">A1<" not in html
    csv = client.get("/rebanho/exportar.csv").get_data(as_text=True)
    assert "A1" in csv and "B2" in csv


def test_venda_gera_receita_unica_e_preserva_historico(app, client, produtor):
    _animal(client, "V1")
    a = Animal.query.filter_by(brinco="V1").one()
    r = client.post(f"/rebanho/{a.id}/movimentar", data={"tipo": "venda", "data": hoje(), "valor": "4.500,00", "destino": "Frigorífico X",
                                                         "gerar_receita": "1", "recebido": "1"})
    assert r.status_code == 302
    assert a.status == "vendido"
    lanc = Lancamento.query.filter_by(animal_id=a.id, tipo="receita").all()
    assert len(lanc) == 1 and lanc[0].valor == Decimal("4500.00") and lanc[0].situacao == "quitado"
    assert client.get(f"/rebanho/{a.id}").status_code == 200  # histórico preservado


def test_pesagens_peso_atual_gmd_e_planilha(app, client, produtor):
    _animal(client, "P1")
    a = Animal.query.filter_by(brinco="P1").one()
    d0 = date.today() - timedelta(days=30)
    client.post("/pesagens/nova", data={"modo": "animal", "animal_id": a.id, "data": d0.isoformat(), "peso_kg": "300"})
    client.post("/pesagens/nova", data={"modo": "animal", "animal_id": a.id, "data": hoje(), "peso_kg": "330"})
    assert a.peso_atual == Decimal("330.00")
    r = indicadores.gmd(Pesagem.query.filter_by(animal_id=a.id).order_by(Pesagem.data).all())
    assert r["dias"] == 30 and r["gmd"] == Decimal("1")
    # Dados insuficientes: menos de 7 dias de intervalo
    assert indicadores.gmd(Pesagem.query.filter_by(animal_id=a.id).order_by(Pesagem.data).all()[-1:]) is None
    # Validação
    r = client.post("/pesagens/nova", data={"modo": "animal", "animal_id": a.id, "data": hoje(), "peso_kg": "-5"})
    assert "maior que zero" in r.get_data(as_text=True)
    # Planilha por lote
    lote = Lote(propriedade_id=a.propriedade_id, nome="L1")
    db.session.add(lote)
    db.session.flush()
    a.lote_id = lote.id
    db.session.commit()
    client.post("/pesagens/nova", data={"modo": "planilha", "lote_id": lote.id, "data": hoje(), f"peso_{a.id}": "335"})
    assert Pesagem.query.filter_by(animal_id=a.id).count() == 3
    # Excluir pesagem recalcula o peso atual
    ultima = Pesagem.query.filter_by(animal_id=a.id, peso_kg=Decimal("335")).one()
    client.post(f"/pesagens/{ultima.id}/excluir")
    assert a.peso_atual == Decimal("330.00")


def test_vacinacao_agenda_e_resolucao(app, client, produtor):
    _animal(client, "S1")
    a = Animal.query.filter_by(brinco="S1").one()
    passado = (date.today() - timedelta(days=170)).isoformat()
    proxima = (date.today() + timedelta(days=5)).isoformat()
    client.post("/sanidade/novo", data={"tipo": "vacina", "produto": "Clostridioses", "data_aplicacao": passado, "proxima_data": proxima,
                                        "alvo": "animal", "animal_id": a.id})
    proximas, _ = indicadores.vacinas_alerta(a.propriedade_id)
    assert len(proximas) == 1
    assert "Clostridioses" in client.get("/sanidade/?aba=agenda").get_data(as_text=True)
    client.post("/sanidade/novo", data={"tipo": "vacina", "produto": "clostridioses", "data_aplicacao": hoje(), "alvo": "animal", "animal_id": a.id})
    proximas, _ = indicadores.vacinas_alerta(a.propriedade_id)
    assert proximas == []
    assert EventoSanitario.query.count() == 2


def test_reproducao_diagnostico_e_parto(app, client, produtor):
    _animal(client, "R1")
    a = Animal.query.filter_by(brinco="R1").one()
    ia = date.today() - timedelta(days=60)
    client.post("/reproducao/novo", data={"animal_id": a.id, "tipo": "inseminacao", "data": ia.isoformat(), "reprodutor_descricao": "Touro X"})
    assert a.situacao_reprodutiva == "servida"
    r = client.post("/reproducao/novo", data={"animal_id": a.id, "tipo": "diagnostico", "data": hoje(), "diagnostico_resultado": "positivo"})
    assert "Campo obrigatório" in r.get_data(as_text=True)  # responsável obrigatório para diagnóstico
    client.post("/reproducao/novo", data={"animal_id": a.id, "tipo": "diagnostico", "data": hoje(), "diagnostico_resultado": "positivo",
                                          "responsavel": "Dra. Ana"})
    assert a.situacao_reprodutiva == "gestante"
    diag = EventoReprodutivo.query.filter_by(tipo="diagnostico").one()
    assert diag.previsao_parto == ia + timedelta(days=283)
    client.post("/reproducao/novo", data={"animal_id": a.id, "tipo": "parto", "data": hoje(), "registrar_cria": "1",
                                          "cria_brinco": "R1-C", "cria_sexo": "M", "cria_peso": "32"})
    cria = Animal.query.filter_by(brinco="R1-C").one()
    assert a.situacao_reprodutiva == "parida" and cria.mae_id == a.id and cria.peso_atual == Decimal("32.00")
    assert indicadores.reprodutivo(a.propriedade_id)["paridas"] == 1


def test_estoque_e_custos_sem_duplicacao(app, client, produtor):
    u, prop = produtor
    from app.models import CategoriaFinanceira
    cat = CategoriaFinanceira.query.filter_by(slug="racao").one()
    r = client.post("/insumos/novo", data={"nome": "Ração", "categoria": "racao", "unidade": "kg", "quantidade_minima": "100",
                                           "quantidade_inicial": "1000", "custo_unitario": "2", "data_entrada": hoje(),
                                           "gerar_despesa": "1", "categoria_financeira_id": cat.id, "pago": "1"})
    assert r.status_code == 302
    ins = Insumo.query.one()
    assert ins.quantidade == Decimal("1000") and ins.custo_medio == Decimal("2")
    assert Lancamento.query.filter_by(origem="estoque").count() == 1
    # saída maior que o estoque é recusada
    r = client.post(f"/insumos/{ins.id}/movimentar/saida", data={"data": hoje(), "quantidade": "5000"})
    assert r.status_code == 400 and "Estoque insuficiente" in r.get_data(as_text=True)
    lote = Lote(propriedade_id=prop.id, nome="Engorda")
    db.session.add(lote)
    db.session.commit()
    _animal(client, "C1", lote_id=lote.id)
    _animal(client, "C2", lote_id=lote.id)
    client.post(f"/insumos/{ins.id}/movimentar/saida", data={"data": hoje(), "quantidade": "950", "lote_id": lote.id})
    assert ins.quantidade == Decimal("50") and ins.estoque_baixo
    assert Lancamento.query.filter_by(origem="estoque").count() == 1  # saída não cria nova despesa
    # ajuste exige justificativa
    r = client.post(f"/insumos/{ins.id}/movimentar/ajuste", data={"data": hoje(), "quantidade_contada": "40"})
    assert r.status_code == 400
    client.post(f"/insumos/{ins.id}/movimentar/ajuste", data={"data": hoje(), "quantidade_contada": "40", "justificativa": "Contagem"})
    assert ins.quantidade == Decimal("40")
    # Custos: a compra (R$ 2000) não entra; entra só o consumo (950 x 2 = 1900), uma única vez.
    c = custos.calcular(prop.id, date.today() - timedelta(days=1), date.today())
    assert c["total_geral"] == Decimal("1900.00")
    linha = next(l for l in c["lotes"] if l["lote_id"] == lote.id)
    assert linha["consumo"] == Decimal("1900.00") and linha["por_cabeca"] == Decimal("950.00")


def test_financeiro_pagamentos_parciais_e_indicadores(app, client, produtor):
    u, prop = produtor
    from app.models import CategoriaFinanceira
    cr = CategoriaFinanceira.query.filter_by(slug="venda_leite").one()
    cd = CategoriaFinanceira.query.filter_by(slug="energia").one()
    client.post("/financeiro/novo/receita", data={"descricao": "Leite setembro", "categoria_id": cr.id, "valor": "10.000,00",
                                                  "data": hoje(), "situacao_inicial": "quitado", "data_pagamento": hoje()})
    client.post("/financeiro/novo/despesa", data={"descricao": "Conta de luz", "categoria_id": cd.id, "valor": "800",
                                                  "data": hoje(), "situacao_inicial": "parcial", "valor_parcial": "300",
                                                  "data_pagamento": hoje(), "vencimento": hoje()})
    luz = Lancamento.query.filter_by(descricao="Conta de luz").one()
    assert luz.situacao == "parcial" and luz.saldo == Decimal("500")
    r = client.post(f"/financeiro/{luz.id}", data={"valor": "600", "data": hoje()})
    assert "excede o saldo" in r.get_data(as_text=True)
    client.post(f"/financeiro/{luz.id}", data={"valor": "500", "data": hoje()})
    assert luz.situacao == "quitado"
    d = indicadores.resumo_dashboard(prop, "mes", True)
    assert d["receitas"] == Decimal("10000") and d["despesas"] == Decimal("800") and d["resultado"] == Decimal("9200")
    assert d["caixa_periodo"] == Decimal("9200")
    html = client.get("/painel").get_data(as_text=True)
    assert "R$ 10.000,00" in html and "R$ 9.200,00" in html
    # estorno e cancelamento
    pg = luz.pagamentos[0]
    client.post(f"/financeiro/{luz.id}/pagamentos/{pg.id}/estornar")
    assert luz.situacao == "parcial"
    client.post(f"/financeiro/{luz.id}/cancelar")
    assert luz.cancelado is False  # há pagamentos: precisa estornar antes
    for p in list(luz.pagamentos):
        client.post(f"/financeiro/{luz.id}/pagamentos/{p.id}/estornar")
    client.post(f"/financeiro/{luz.id}/cancelar")
    assert luz.cancelado is True
    assert indicadores.resumo_dashboard(prop, "mes", True)["despesas"] == Decimal("0")


def test_dashboard_vazio_orienta_cadastro(app, client, produtor):
    html = client.get("/painel").get_data(as_text=True)
    assert "Olá, Vitor!" in html and "ainda não tem registros" in html


def test_permissoes_por_papel_na_propriedade(app, client, produtor):
    u, prop = produtor
    _animal(client, "X1")
    a = Animal.query.filter_by(brinco="X1").one()
    for papel, email in (("operador", "op@teste.com"), ("consulta", "cons@teste.com")):
        outro = criar_usuario(nome=papel.title(), email=email)
        adicionar_membro(prop, outro, papel)
    c = app.test_client()
    login(c, "op@teste.com")
    assert c.get("/financeiro/").status_code == 403
    assert c.get(f"/rebanho/{a.id}").status_code == 200
    assert c.post("/rebanho/novo", data={"brinco": "Z"}).status_code in (200, 302)
    c2 = app.test_client()
    login(c2, "cons@teste.com")
    assert c2.get(f"/rebanho/{a.id}").status_code == 200
    assert c2.get("/rebanho/novo").status_code == 403
    assert c2.post(f"/rebanho/{a.id}/editar", data={}).status_code == 403
    assert c2.get(f"/propriedades/{prop.id}/membros").status_code == 403


def test_isolamento_entre_usuarios(app, client, produtor):
    u, prop = produtor
    _animal(client, "SEGREDO")
    a = Animal.query.filter_by(brinco="SEGREDO").one()
    from app.models import CategoriaFinanceira
    client.post("/financeiro/novo/despesa", data={"descricao": "Despesa privada", "categoria_id": CategoriaFinanceira.query.filter_by(slug="agua").one().id,
                                                  "valor": "100", "data": hoje(), "situacao_inicial": "pendente"})
    lanc = Lancamento.query.one()
    intruso = criar_usuario(nome="Intruso", email="intruso@teste.com")
    criar_propriedade(intruso, nome="Outra fazenda")
    c = app.test_client()
    login(c, "intruso@teste.com")
    for url in (f"/rebanho/{a.id}", f"/rebanho/{a.id}/editar", f"/financeiro/{lanc.id}", f"/propriedades/{prop.id}",
                f"/financeiro/{lanc.id}/anexo"):
        assert c.get(url).status_code == 404, url
    assert c.post(f"/propriedades/{prop.id}/ativar", data={}).status_code == 404
    html = c.get("/rebanho/").get_data(as_text=True) + c.get("/financeiro/").get_data(as_text=True)
    assert "SEGREDO" not in html and "Despesa privada" not in html
    busca = c.get("/busca?q=SEGREDO").get_data(as_text=True)
    assert f"/rebanho/{a.id}" not in busca and "Nada encontrado" in busca
    snap = c.get("/api/offline/snapshot").get_json()
    assert all(p["id"] != prop.id for p in snap["propriedades"])


def test_relatorios_respeitam_permissao_financeira(app, client, produtor):
    u, prop = produtor
    op = criar_usuario(nome="Operador", email="op2@teste.com")
    adicionar_membro(prop, op, "operador")
    c = app.test_client()
    login(c, "op2@teste.com")
    assert c.get("/relatorios/inventario").status_code == 200
    assert c.get("/relatorios/despesas").status_code == 403
    assert c.get("/relatorios/custos").status_code == 403
    assert "Despesas" not in c.get("/relatorios/").get_data(as_text=True).split("Operacional")[0].split("Rebanho")[-1]
