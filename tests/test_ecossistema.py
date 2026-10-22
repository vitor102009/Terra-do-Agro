import uuid
from datetime import date, timedelta

from app.extensions import db
from app.models import (
    Anuncio, Avaliacao, Candidatura, CategoriaProfissional, Conversa, Denuncia, Favorito, Mensagem, Notificacao,
    PerfilProfissional, Post, Reacao, SolicitacaoServico, SolicitacaoVerificacao, User, Vaga,
)

from conftest import arquivo, criar_usuario, login, png_bytes


def _cliente(app, email, nome="Pessoa Teste", admin=False, papeis=("produtor",)):
    criar_usuario(nome=nome, email=email, admin=admin, papeis=papeis)
    c = app.test_client()
    login(c, email)
    return c


def _anuncio(c, **extra):
    dados = {"categoria": "maquinas", "titulo": "Trator 75 cv", "descricao": "Trator revisado, pneus bons, documentação ok.",
             "tipo_preco": "fixo", "preco": "85.000,00", "municipio": "Uberaba", "uf": "MG"}
    dados.update(extra)
    return c.post("/marketplace/novo", data=dados, content_type="multipart/form-data")


def test_marketplace_anuncio_fotos_busca_favorito(app):
    vendedor = _cliente(app, "vend@teste.com", "Vendedor")
    r = _anuncio(vendedor, fotos=[arquivo(png_bytes(), "foto.png")])
    assert r.status_code == 302
    an = Anuncio.query.one()
    assert len(an.fotos) == 1 and str(an.preco) == "85000.00"
    foto = vendedor.get(f"/media/{an.fotos[0].arquivo}")
    assert foto.status_code == 200
    # Arquivo falso com extensão de imagem é recusado
    r = _anuncio(vendedor, titulo="Outro anúncio", fotos=[arquivo(b"MZ executavel", "virus.png")])
    assert "não corresponde ao formato" in r.get_data(as_text=True)
    # Preço obrigatório quando não é "sob orçamento"
    r = _anuncio(vendedor, titulo="Sem preço", preco="")
    assert "Informe o preço" in r.get_data(as_text=True)
    # Anúncio de animal exige quantidade
    r = _anuncio(vendedor, categoria="animais", titulo="Novilhas", especie="bovino")
    assert "Campo obrigatório" in r.get_data(as_text=True)
    comprador = _cliente(app, "comp@teste.com", "Comprador", papeis=("comprador",))
    html = comprador.get("/marketplace/?q=trator&preco_max=90000").get_data(as_text=True)
    assert "Trator 75 cv" in html
    assert "Trator 75 cv" not in comprador.get("/marketplace/?preco_max=1000").get_data(as_text=True)
    comprador.post(f"/marketplace/{an.id}/favoritar")
    assert Favorito.query.count() == 1
    # Outro usuário não pode editar
    assert comprador.get(f"/marketplace/{an.id}/editar").status_code == 404
    assert comprador.post(f"/marketplace/{an.id}/status", data={"status": "vendido"}).status_code == 404
    # Arquivos privados não são servidos pela rota pública
    assert comprador.get("/media/private/financeiro/x.pdf").status_code == 404
    assert comprador.get("/media/public/../../secret_key").status_code == 404
    # Pausado some da busca
    vendedor.post(f"/marketplace/{an.id}/status", data={"status": "pausado"})
    assert "Trator 75 cv" not in comprador.get("/marketplace/").get_data(as_text=True)


def _perfil_vet(c):
    cat = CategoriaProfissional.query.filter_by(slug="veterinario").one()
    return c.post("/profissionais/meus-perfis/editar", data={
        "categoria_id": cat.id, "titulo": "Clínica de bovinos", "descricao": "Atendimento clínico e reprodutivo de bovinos de corte e leite.",
        "especialidades": "reprodução, IATF", "registro_numero": "12345", "registro_uf": "MG", "municipio": "Uberaba", "uf": "MG",
        "modalidades": ["presencial"], "disponivel": "1", "publico": "1", "preco_minimo": "250"})


def test_profissional_busca_e_verificacao_real(app):
    vet = _cliente(app, "vet@teste.com", "Ana Vet", papeis=())
    assert _perfil_vet(vet).status_code == 302
    perfil = PerfilProfissional.query.one()
    assert perfil.verificado is False and perfil.user.has_role("profissional")
    prod = _cliente(app, "prod@teste.com", "Produtor")
    for q in ("/profissionais/?q=IATF", "/profissionais/?uf=MG", "/profissionais/?modalidade=presencial", "/profissionais/?preco_max=300",
              "/profissionais/?grupo=veterinaria"):
        assert "Ana Vet" in prod.get(q).get_data(as_text=True), q
    assert "Ana Vet" not in prod.get("/profissionais/?verificado=1").get_data(as_text=True)
    assert "Ana Vet" not in prod.get("/profissionais/?uf=SP").get_data(as_text=True)
    # Não é possível se autoverificar
    vet.post("/profissionais/meus-perfis/editar?id=%d" % perfil.id, data={"verificado": "1"})
    assert perfil.verificado is False
    r = vet.post(f"/profissionais/meus-perfis/{perfil.id}/verificacao", data={"documento": arquivo(b"%PDF-1.4 teste", "crmv.pdf")},
                 content_type="multipart/form-data")
    assert r.status_code == 302
    sv = SolicitacaoVerificacao.query.one()
    assert prod.post(f"/admin/verificacoes/{sv.id}", data={"decisao": "aprovada"}).status_code == 403
    admin = _cliente(app, "admin@teste.com", "Admin", admin=True)
    assert admin.get(f"/admin/verificacoes/{sv.id}/documento").status_code == 200
    admin.post(f"/admin/verificacoes/{sv.id}", data={"decisao": "aprovada"})
    assert perfil.verificado is True
    assert "Ana Vet" in prod.get("/profissionais/?verificado=1").get_data(as_text=True)
    assert Notificacao.query.filter_by(user_id=perfil.user_id, tipo="verificacao").count() == 1


def test_fluxo_completo_de_contratacao(app):
    vet = _cliente(app, "vet@teste.com", "Ana Vet", papeis=())
    _perfil_vet(vet)
    perfil = PerfilProfissional.query.one()
    prod = _cliente(app, "prod@teste.com", "Produtor")
    r = prod.post("/servicos/nova", data={"perfil": perfil.id, "tipo": "orcamento", "titulo": "Diagnóstico de gestação",
                                          "descricao": "40 matrizes para diagnóstico.", "municipio": "Uberaba", "uf": "MG"})
    assert r.status_code == 302
    s = SolicitacaoServico.query.one()
    assert s.status == "aguardando"
    # Cliente não pode avaliar nem concluir antes da hora; profissional não pode aceitar o próprio orçamento
    assert prod.post(f"/servicos/{s.id}/avaliar", data={"nota": 5}).status_code == 403
    prod.post(f"/servicos/{s.id}/acao", data={"acao": "concluir"})
    assert s.status == "aguardando"
    vet.post(f"/servicos/{s.id}/orcamento", data={"valor": "600", "descricao": "Ultrassom em 40 matrizes"})
    assert s.status == "orcamento_enviado"
    vet.post(f"/servicos/{s.id}/acao", data={"acao": "aceitar_orcamento"})
    assert s.status == "orcamento_enviado"
    prod.post(f"/servicos/{s.id}/acao", data={"acao": "aceitar_orcamento"})
    assert s.status == "orcamento_aceito"
    quando = (date.today() + timedelta(days=2)).isoformat() + "T08:30"
    vet.post(f"/servicos/{s.id}/acao", data={"acao": "agendar", "data_agendada": quando})
    assert s.status == "agendado" and s.data_agendada
    vet.post(f"/servicos/{s.id}/acao", data={"acao": "iniciar"})
    vet.post(f"/servicos/{s.id}/acao", data={"acao": "concluir"})
    assert s.status == "concluido"
    estados = [h.status_novo for h in s.historico]
    assert estados == ["criada", "aguardando", "orcamento_enviado", "orcamento_aceito", "agendado", "em_andamento", "concluido"]
    assert all(h.user_id for h in s.historico)
    # Terceiro não vê a solicitação
    outro = _cliente(app, "outro@teste.com", "Outro")
    assert outro.get(f"/servicos/{s.id}").status_code == 404
    # Avaliação vinculada, uma única vez
    prod.post(f"/servicos/{s.id}/avaliar", data={"nota": 5, "comentario": "Excelente"})
    assert prod.post(f"/servicos/{s.id}/avaliar", data={"nota": 1}).status_code == 403
    assert Avaliacao.query.count() == 1 and float(perfil.nota_media) == 5.0 and perfil.total_avaliacoes == 1


def test_vagas_e_candidaturas(app):
    emp = _cliente(app, "emp@teste.com", "Empregador")
    r = emp.post("/vagas/nova", data={"empregador": "Fazenda Sol", "cargo": "Vaqueiro", "categoria": "vaqueiro", "descricao": "Manejo de gado de corte em pasto.",
                                      "tipo_contratacao": "clt", "municipio": "Uberaba", "uf": "MG", "salario_min": "2500", "salario_max": "2000"})
    assert "maior que o mínimo" in r.get_data(as_text=True)
    emp.post("/vagas/nova", data={"empregador": "Fazenda Sol", "cargo": "Vaqueiro", "categoria": "vaqueiro", "descricao": "Manejo de gado de corte em pasto.",
                                  "tipo_contratacao": "clt", "municipio": "Uberaba", "uf": "MG"})
    vaga = Vaga.query.one()
    trab = _cliente(app, "trab@teste.com", "João Peão", papeis=())
    r = trab.post(f"/vagas/{vaga.id}/candidatar", data={"mensagem": "Tenho experiência"})
    assert "meu-perfil" in r.headers["Location"]  # precisa de perfil antes
    trab.post("/vagas/meu-perfil", data={"titulo": "Vaqueiro experiente", "categorias": ["vaqueiro"], "municipio": "Uberaba", "uf": "MG",
                                         "telefone_contato": "34 99999-0000", "visivel_busca": "1", "disponivel": "1"})
    trab.post(f"/vagas/{vaga.id}/candidatar", data={"mensagem": "Tenho experiência"})
    trab.post(f"/vagas/{vaga.id}/candidatar", data={"mensagem": "De novo"})
    assert Candidatura.query.count() == 1
    html = emp.get(f"/vagas/{vaga.id}").get_data(as_text=True)
    assert "João Peão" in html and "34 99999-0000" not in html  # sem consentimento, telefone não aparece
    assert "34 99999-0000" not in emp.get("/vagas/trabalhadores").get_data(as_text=True)
    from app.models import PerfilTrabalhador
    PerfilTrabalhador.query.one().compartilhar_contato = True
    db.session.commit()
    assert "34 99999-0000" in emp.get(f"/vagas/{vaga.id}").get_data(as_text=True)
    cand = Candidatura.query.one()
    assert trab.post(f"/vagas/candidaturas/{cand.id}/status", data={"status": "contato_solicitado"}).status_code == 404
    emp.post(f"/vagas/candidaturas/{cand.id}/status", data={"status": "em_analise"})
    assert cand.status == "em_analise"
    assert "Em análise" in trab.get("/vagas/minhas-candidaturas").get_data(as_text=True)


def test_chat_idempotente_privado_e_bloqueio(app):
    a = _cliente(app, "a@teste.com", "Ana")
    b = _cliente(app, "b@teste.com", "Bruno")
    ub = User.query.filter_by(email="b@teste.com").one()
    r = a.get(f"/mensagens/iniciar/{ub.id}")
    conv = Conversa.query.one()
    uid = str(uuid.uuid4())
    for _ in range(2):  # reenvio da mesma mensagem (ex.: conexão caiu)
        r = a.post(f"/mensagens/{conv.id}/enviar", json={"texto": "Olá, tudo bem?", "uuid": uid})
        assert r.status_code in (200, 201)
    assert Mensagem.query.count() == 1
    assert r.get_json()["duplicada"] is True
    d = b.get(f"/mensagens/{conv.id}/mensagens.json").get_json()
    assert d["mensagens"][0]["texto"] == "Olá, tudo bem?" and Mensagem.query.one().lida_em is not None
    intruso = _cliente(app, "c@teste.com", "Carlos")
    assert intruso.get(f"/mensagens/{conv.id}/mensagens.json").status_code == 404
    assert intruso.post(f"/mensagens/{conv.id}/enviar", json={"texto": "oi"}).status_code == 404
    assert intruso.get(f"/mensagens/?c={conv.id}").status_code == 404
    b.post(f"/mensagens/usuario/{User.query.filter_by(email='a@teste.com').one().id}/bloquear")
    r = a.post(f"/mensagens/{conv.id}/enviar", json={"texto": "Ainda aí?", "uuid": str(uuid.uuid4())})
    assert r.status_code == 403 and Mensagem.query.count() == 1
    assert a.post(f"/mensagens/{conv.id}/enviar", json={"texto": "", "uuid": str(uuid.uuid4())}).status_code == 400


def test_feed_grupo_reacoes_comentarios_e_video(app):
    a = _cliente(app, "a@teste.com", "Ana")
    b = _cliente(app, "b@teste.com", "Bruno")
    a.post("/feed/publicar", data={"texto": "Dica de manejo", "tipo": "dica"})
    p = Post.query.one()
    b.post(f"/feed/{p.id}/reagir")
    assert Reacao.query.count() == 1
    b.post(f"/feed/{p.id}/reagir")
    assert Reacao.query.count() == 0
    b.post(f"/feed/{p.id}/comentar", data={"texto": "Muito bom"})
    assert len(p.comentarios) == 1
    b.post(f"/feed/{p.id}/compartilhar")
    assert Post.query.filter(Post.compartilhado_id == p.id).count() == 1
    assert b.get(f"/feed/{p.id}/editar").status_code == 404
    assert b.post(f"/feed/{p.id}/excluir").status_code == 403
    a.post(f"/feed/{p.id}/editar", data={"texto": "Dica de manejo atualizada", "tipo": "dica"})
    assert p.editado
    # Vídeo inválido é recusado no backend
    r = a.post("/feed/publicar", data={"texto": "vídeo", "midia": arquivo(b"isto nao e um mp4", "clip.mp4")}, content_type="multipart/form-data",
               follow_redirects=True)
    assert "não corresponde ao formato" in r.get_data(as_text=True)
    video = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    a.post("/feed/publicar", data={"texto": "vídeo ok", "midia": arquivo(video, "clip.mp4")}, content_type="multipart/form-data")
    assert Post.query.filter_by(midia_tipo="video").count() == 1
    # Grupo: só participantes publicam
    a.post("/comunidade/novo", data={"nome": "Leite do Triângulo", "descricao": "Grupo de produtores de leite.", "tema": "leite"})
    from app.models import Grupo
    g = Grupo.query.filter_by(nome="Leite do Triângulo").one()
    b.post("/feed/publicar", data={"texto": "post no grupo", "grupo_id": g.id})
    assert Post.query.filter_by(grupo_id=g.id).count() == 0
    b.post(f"/comunidade/{g.id}/entrar")
    b.post("/feed/publicar", data={"texto": "post no grupo", "grupo_id": g.id})
    gp = Post.query.filter_by(grupo_id=g.id).one()
    a.post(f"/feed/{gp.id}/excluir")  # admin do grupo modera
    assert gp.removido
    a.post(f"/comunidade/{g.id}/sair")  # único admin com outros membros não sai
    from app.models import GrupoMembro
    assert GrupoMembro.query.filter_by(grupo_id=g.id).count() == 2


def test_admin_moderacao_denuncia_e_suspensao(app):
    vend = _cliente(app, "vend@teste.com", "Vendedor")
    _anuncio(vend)
    an = Anuncio.query.one()
    comp = _cliente(app, "comp@teste.com", "Comprador")
    comp.post(f"/denunciar/anuncio/{an.id}", data={"motivo": "fraude", "descricao": "Pediu pagamento antecipado"})
    d = Denuncia.query.one()
    admin = _cliente(app, "admin@teste.com", "Admin", admin=True)
    assert admin.get(f"/admin/denuncias/{d.id}").status_code == 200
    admin.post(f"/admin/conteudo/anuncio/{an.id}", data={"acao": "remover", "motivo": "Fraude confirmada"})
    assert an.status == "removido"
    assert comp.get(f"/marketplace/{an.id}").status_code == 404
    admin.post(f"/admin/denuncias/{d.id}", data={"decisao": "procedente"})
    assert d.status == "procedente"
    uv = User.query.filter_by(email="vend@teste.com").one()
    admin.post(f"/admin/usuarios/{uv.id}", data={"acao": "suspender", "motivo": "Fraude"})
    assert uv.ativo is False
    assert vend.get("/painel").status_code == 302  # sessão encerrada
    from app.models import AuditLog
    assert AuditLog.query.filter_by(administrativa=True).count() >= 3
    assert comp.get("/admin/").status_code == 403
