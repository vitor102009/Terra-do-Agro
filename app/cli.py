"""Comandos de linha de comando (flask <comando>).

  flask setup               Aplica as migrações e cadastra os dados de referência (categorias, grupos temáticos).
  flask create-admin        Cria ou promove um administrador (senha digitada no terminal, nunca padrão).
  flask backup              Copia o banco SQLite (e, opcionalmente, os arquivos enviados) para a pasta backups/.
  flask demo-data           Cria dados de DEMONSTRAÇÃO identificados (somente fora de produção).
  flask remover-demo        Remove todos os dados de demonstração.
  flask limpar-registros    Aplica a política de retenção (tentativas de login antigas, operações offline).
"""
import os
import re
import secrets
import sqlite3
import zipfile
from datetime import date, timedelta
from decimal import Decimal

import click
from flask import current_app

from app.extensions import db
from app.models import (
    Animal, Anuncio, CategoriaFinanceira, CategoriaProfissional, EventoReprodutivo, EventoSanitario, Grupo, Insumo,
    Lancamento, LoginAttempt, Lote, MovimentacaoAnimal, OperacaoOffline, Pagamento, PerfilProfissional, Pesagem, Propriedade,
    PropriedadeMembro, ServicoProfissional, Tarefa, User, Vaga, utcnow,
)

CATEGORIAS_FINANCEIRAS = [
    ("receita", "venda_animais", "Venda de animais", True),
    ("receita", "venda_leite", "Venda de leite", True),
    ("receita", "venda_graos", "Venda de grãos", True),
    ("receita", "venda_produtos", "Venda de produtos agropecuários", True),
    ("receita", "servicos_prestados", "Prestação de serviços", True),
    ("receita", "outras_receitas", "Outras receitas", True),
    ("despesa", "racao", "Compra de ração", True),
    ("despesa", "medicamentos", "Medicamentos e vacinas", True),
    ("despesa", "mao_obra", "Salários e mão de obra", True),
    ("despesa", "combustivel", "Combustível", True),
    ("despesa", "manutencao", "Manutenção", True),
    ("despesa", "energia", "Energia", True),
    ("despesa", "agua", "Água", True),
    ("despesa", "insumos_agricolas", "Insumos agrícolas", True),
    ("despesa", "transporte", "Transporte", True),
    ("despesa", "compra_animais", "Compra de animais", True),
    ("despesa", "maquinas", "Máquinas e equipamentos", False),
    ("despesa", "servicos_veterinarios", "Serviços veterinários", True),
    ("despesa", "servicos_tecnicos", "Serviços técnicos", True),
    ("despesa", "arrendamento", "Arrendamentos", True),
    ("despesa", "outras_despesas", "Outras despesas", True),
]

AVISO_VET = "Procedimentos clínicos, cirúrgicos, prescrição de medicamentos e diagnósticos são atos privativos de médico-veterinário habilitado."
AVISO_AGRO = "Receituário agronômico e responsabilidade técnica exigem profissional habilitado no sistema CONFEA/CREA ou CFTA, conforme a atividade."

CATEGORIAS_PROFISSIONAIS = [
    ("Médico-veterinário", "veterinario", "veterinaria", "CRMV", AVISO_VET),
    ("Engenheiro agrônomo", "agronomo", "agronomia", "CREA", AVISO_AGRO),
    ("Zootecnista", "zootecnista", "zootecnia", "CRMV", "Atuação conforme as atribuições do zootecnista definidas pelo sistema CFMV/CRMV."),
    ("Técnico agrícola", "tecnico-agricola", "tecnico", "CFTA", AVISO_AGRO),
    ("Técnico agropecuário", "tecnico-agropecuario", "tecnico", "CFTA", AVISO_AGRO),
    ("Assistência técnica e extensão rural", "assistencia-tecnica", "tecnico", None, None),
    ("Inseminador", "inseminador", "reproducao", None, "Inseminação artificial deve seguir capacitação reconhecida e orientação de médico-veterinário responsável pelo programa."),
    ("Técnico em reprodução animal", "tecnico-reproducao", "reproducao", None, AVISO_VET),
    ("Especialista em programas reprodutivos (IATF)", "programas-reprodutivos", "reproducao", "CRMV", AVISO_VET),
    ("Consultor em melhoramento genético", "melhoramento-genetico", "reproducao", None, None),
    ("Operador de máquinas / serviço de trator", "servico-trator", "mecanizacao", None, None),
    ("Preparo do solo", "preparo-solo", "mecanizacao", None, None),
    ("Plantio mecanizado", "plantio-mecanizado", "mecanizacao", None, None),
    ("Colheita mecanizada", "colheita-mecanizada", "mecanizacao", None, None),
    ("Pulverização (aplicador habilitado)", "pulverizacao", "mecanizacao", None, "Aplicação de agrotóxicos exige capacitação, receituário agronômico e cumprimento da legislação vigente."),
    ("Distribuição de insumos", "distribuicao-insumos", "mecanizacao", None, None),
    ("Manutenção de máquinas e implementos", "manutencao-maquinas", "mecanizacao", None, None),
    ("Locação de equipamentos", "locacao-equipamentos", "mecanizacao", None, None),
    ("Ferrador", "ferrador", "campo", None, None),
    ("Casqueamento e podologia animal", "casqueamento", "campo", None, "Tratamentos clínicos de cascos devem ser realizados ou supervisionados por médico-veterinário."),
    ("Especialista em pastagens", "pastagens", "campo", None, None),
    ("Consultor de gestão rural", "gestao-rural", "campo", None, None),
    ("Manutenção elétrica e mecânica rural", "manutencao-rural", "campo", None, "Instalações elétricas devem seguir as normas técnicas e a NR-10."),
    ("Técnico de irrigação", "irrigacao", "campo", None, None),
    ("Transporte de animais e cargas", "transporte", "campo", None, "Transporte de animais exige GTA e veículos adequados às normas de bem-estar animal."),
    ("Cercas e instalações rurais", "cercas-instalacoes", "campo", None, None),
    ("Consultor de gestão ambiental", "gestao-ambiental", "campo", None, "Laudos e projetos ambientais exigem profissional habilitado para a atividade."),
    ("Segurança e saúde ocupacional rural", "seguranca-trabalho", "campo", None, "Atuação conforme a NR-31 e a habilitação profissional exigida."),
    ("Serviços para pecuária leiteira", "servicos-leite", "campo", None, None),
    ("Serviços para agricultura", "servicos-agricultura", "campo", None, None),
    ("Gestão de propriedades", "gestao-propriedades", "campo", None, None),
    ("Empresa de assistência técnica", "empresa-assistencia", "empresa", None, None),
]

GRUPOS_TEMATICOS = [
    ("Pecuária de corte", "corte", "Manejo, nutrição, genética e mercado do boi."),
    ("Pecuária leiteira", "leite", "Produção, qualidade do leite, ordenha e gestão de rebanhos leiteiros."),
    ("Agricultura", "agricultura", "Plantio, manejo de culturas, solo e colheita."),
    ("Reprodução bovina", "reproducao", "IATF, inseminação, estação de monta e índices reprodutivos."),
    ("Nutrição animal", "nutricao", "Suplementação, dietas, pastagens e confinamento."),
    ("Máquinas agrícolas", "maquinas", "Tratores, implementos, manutenção e agricultura de precisão."),
    ("Mercado agropecuário", "mercado", "Preços, comercialização e tendências do setor."),
    ("Gestão rural", "gestao", "Custos, planejamento, pessoas e tecnologia na fazenda."),
    ("Emprego rural", "emprego", "Oportunidades, qualificação e direitos do trabalhador do campo."),
    ("Veterinária", "veterinaria", "Sanidade, bem-estar e saúde animal."),
    ("Assistência técnica", "assistencia", "Extensão rural e assistência técnica ao produtor."),
]

REGRAS_PADRAO = "Respeite os participantes. Não publique dados pessoais de terceiros. Identifique conteúdos comerciais. Denuncie abusos."


def seed_base():
    novos = 0
    for tipo, slug, nome, custo in CATEGORIAS_FINANCEIRAS:
        if not CategoriaFinanceira.query.filter_by(propriedade_id=None, tipo=tipo, slug=slug).first():
            db.session.add(CategoriaFinanceira(propriedade_id=None, tipo=tipo, slug=slug, nome=nome, custo_producao=custo))
            novos += 1
    for ordem, (nome, slug, grupo, registro, aviso) in enumerate(CATEGORIAS_PROFISSIONAIS):
        if not CategoriaProfissional.query.filter_by(slug=slug).first():
            db.session.add(CategoriaProfissional(nome=nome, slug=slug, grupo=grupo, registro_label=registro,
                                                 aviso_habilitacao=aviso, ordem=ordem * 10))
            novos += 1
    for nome, tema, desc in GRUPOS_TEMATICOS:
        if not Grupo.query.filter_by(nome=nome).first():
            db.session.add(Grupo(nome=nome, tema=tema, descricao=desc, regras=REGRAS_PADRAO))
            novos += 1
    db.session.commit()
    return novos


def _validar_senha(senha):
    return len(senha) >= 10 and re.search(r"[A-Za-z]", senha) and re.search(r"\d", senha)


def registrar_cli(app):
    @app.cli.command("setup")
    def setup():
        """Aplica migrações (sem apagar dados) e cadastra os dados de referência."""
        from flask_migrate import upgrade

        pasta = os.path.join(os.path.dirname(current_app.root_path), "migrations")
        if os.path.isdir(pasta):
            upgrade(directory=pasta)
        else:
            db.create_all()
        n = seed_base()
        click.echo(f"Banco pronto. {n} registro(s) de referência adicionados.")
        if not User.query.filter_by(is_admin=True).first():
            click.echo("Nenhum administrador encontrado. Crie um com: flask create-admin")

    @app.cli.command("seed-base")
    def seed_cmd():
        """Cadastra categorias e grupos temáticos padrão (idempotente)."""
        click.echo(f"{seed_base()} registro(s) adicionados.")

    @app.cli.command("create-admin")
    @click.option("--email", prompt="E-mail do administrador")
    @click.option("--nome", prompt="Nome completo")
    def create_admin(email, nome):
        """Cria um administrador ou promove uma conta existente. A senha é digitada de forma oculta."""
        email = email.strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise click.ClickException("E-mail inválido.")
        user = User.query.filter_by(email=email).first()
        if user:
            user.is_admin = True
            user.ativo = True
            user.session_version += 1
            db.session.commit()
            click.echo(f"Conta {email} promovida a administrador.")
            return
        senha = os.environ.get("ADMIN_PASSWORD") or click.prompt("Senha (mínimo 10 caracteres, letras e números)",
                                                                 hide_input=True, confirmation_prompt="Confirme a senha")
        if not _validar_senha(senha):
            raise click.ClickException("Senha fraca: use pelo menos 10 caracteres com letras e números.")
        user = User(nome=nome.strip(), email=email, is_admin=True, email_verificado=True, aceite_termos_em=utcnow())
        user.set_password(senha)
        user.add_role("produtor")
        db.session.add(user)
        db.session.commit()
        click.echo(f"Administrador {email} criado.")

    @app.cli.command("backup")
    @click.option("--com-arquivos", is_flag=True, help="Inclui os arquivos enviados (fotos, anexos) em um .zip.")
    def backup(com_arquivos):
        """Cópia consistente do banco SQLite usando a API de backup do SQLite."""
        uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
        if not uri.startswith("sqlite:///"):
            raise click.ClickException("O comando backup suporta SQLite. Para outros bancos, use a ferramenta nativa do banco.")
        origem = uri.replace("sqlite:///", "", 1)
        destino_dir = current_app.config["BACKUP_DIR"]
        os.makedirs(destino_dir, exist_ok=True)
        carimbo = utcnow().strftime("%Y%m%d-%H%M%S")
        destino = os.path.join(destino_dir, f"terradoagro-{carimbo}.db")
        with sqlite3.connect(origem) as src, sqlite3.connect(destino) as dst:
            src.backup(dst)
        click.echo(f"Banco copiado para {destino}")
        if com_arquivos:
            zip_path = os.path.join(destino_dir, f"arquivos-{carimbo}.zip")
            raiz = current_app.config["UPLOAD_ROOT"]
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
                for pasta, _, arquivos in os.walk(raiz):
                    for a in arquivos:
                        full = os.path.join(pasta, a)
                        z.write(full, os.path.relpath(full, raiz))
            click.echo(f"Arquivos copiados para {zip_path}")

    @app.cli.command("cotacoes-atualizar")
    @click.option("--forcar", is_flag=True, help="Ignora o intervalo mínimo entre coletas.")
    def cotacoes_atualizar(forcar):
        """Coleta as fontes automáticas de cotações (para tarefa agendada)."""
        from app.services import cotacoes as servico

        ativas = servico.fontes_ativas()
        if not ativas:
            click.echo("Nenhuma fonte automática ativa.")
        for slug in ativas:
            n = servico.coletar(slug, forcar=forcar)
            click.echo(f"{slug}: " + ("intervalo mínimo ainda não passou" if n is None and not forcar else
                                      "falha (veja o log)" if n is None else f"{n} cotação(ões) nova(s)"))

    @app.cli.command("ia-verificar")
    def ia_verificar():
        """Valida a ANTHROPIC_API_KEY com uma chamada autenticada sem custo de tokens (não exibe a chave)."""
        from app.services import ia_agro

        st = ia_agro.status(forcar=True)
        click.echo(f"IA Agro: {st['estado']} - {st['motivo']} (modelo: {current_app.config['AI_MODEL']})")
        if st["estado"] != "ok":
            raise SystemExit(1)

    @app.cli.command("limpar-registros")
    @click.option("--dias", default=90, show_default=True)
    def limpar(dias):
        """Retenção: remove tentativas de login e registros de operações offline mais antigos que N dias."""
        limite = utcnow() - timedelta(days=dias)
        a = LoginAttempt.query.filter(LoginAttempt.created_at < limite).delete()
        b = OperacaoOffline.query.filter(OperacaoOffline.created_at < limite).delete()
        db.session.commit()
        click.echo(f"{a} tentativa(s) de login e {b} operação(ões) offline removidas.")

    @app.cli.command("demo-data")
    @click.option("--confirmar", is_flag=True, help="Confirma a criação dos dados de demonstração.")
    def demo_data(confirmar):
        """Cria dados de DEMONSTRAÇÃO claramente identificados. Bloqueado em produção."""
        if current_app.config.get("ENV_MODE") == "production":
            raise click.ClickException("Dados de demonstração não podem ser criados em produção.")
        if not confirmar:
            raise click.ClickException("Use --confirmar para criar os dados de demonstração.")
        if User.query.filter_by(email="demo.produtor@exemplo.com.br").first():
            raise click.ClickException("Os dados de demonstração já existem. Use 'flask remover-demo' antes de recriar.")
        seed_base()
        senha = secrets.token_urlsafe(9)
        criar_demo(senha)
        click.echo("Dados de demonstração criados (marcados como DEMONSTRAÇÃO).")
        click.echo("  Produtor:     demo.produtor@exemplo.com.br")
        click.echo("  Veterinária:  demo.vet@exemplo.com.br")
        click.echo(f"  Senha (ambos): {senha}")
        click.echo("Guarde a senha: ela não será exibida novamente.")

    @app.cli.command("remover-demo")
    def remover_demo():
        """Remove todos os registros marcados como demonstração."""
        props = Propriedade.query.filter_by(is_demo=True).all()
        ids = [p.id for p in props]
        if ids:
            User.query.filter(User.propriedade_ativa_id.in_(ids)).update({"propriedade_ativa_id": None}, synchronize_session=False)
            db.session.flush()
        for p in props:
            db.session.delete(p)
        db.session.flush()
        usuarios = User.query.filter_by(is_demo=True).all()
        for u in usuarios:
            db.session.delete(u)
        db.session.commit()
        click.echo(f"{len(props)} propriedade(s) e {len(usuarios)} usuário(s) de demonstração removidos.")


def criar_demo(senha):
    """Cria um conjunto coerente de dados fictícios, sempre identificados como demonstração."""
    hoje = date.today()
    prod = User(nome="Produtor Demonstração", email="demo.produtor@exemplo.com.br", municipio="Uberaba", uf="MG",
                is_demo=True, email_verificado=True, aceite_termos_em=utcnow())
    prod.set_password(senha)
    prod.add_role("produtor")
    vet = User(nome="Veterinária Demonstração", email="demo.vet@exemplo.com.br", municipio="Uberaba", uf="MG",
               is_demo=True, email_verificado=True, aceite_termos_em=utcnow())
    vet.set_password(senha)
    vet.add_role("profissional")
    db.session.add_all([prod, vet])
    db.session.flush()

    fazenda = Propriedade(owner_id=prod.id, nome="Fazenda Demonstração", municipio="Uberaba", uf="MG", area_total_ha=Decimal("420"),
                          area_pastagem_ha=Decimal("310"), area_agricultura_ha=Decimal("60"), atividade="pecuaria_corte",
                          tipo_producao="Cria e recria a pasto (DADOS DE DEMONSTRAÇÃO)", is_demo=True)
    db.session.add(fazenda)
    db.session.flush()
    db.session.add(PropriedadeMembro(propriedade_id=fazenda.id, user_id=prod.id, papel="proprietario"))
    prod.propriedade_ativa_id = fazenda.id

    matrizes = Lote(propriedade_id=fazenda.id, nome="Matrizes", finalidade="Cria")
    recria = Lote(propriedade_id=fazenda.id, nome="Recria", finalidade="Recria")
    db.session.add_all([matrizes, recria])
    db.session.flush()

    rng = secrets.SystemRandom()
    animais = []
    for i in range(1, 19):
        femea = i <= 12
        nasc = hoje - timedelta(days=rng.randint(900, 2200) if femea else rng.randint(380, 700))
        a = Animal(propriedade_id=fazenda.id, brinco=f"D{i:03d}", especie="bovino", raca="Nelore", sexo="F" if femea else "M",
                   data_nascimento=nasc, categoria="Vaca" if femea else "Garrote", lote_id=matrizes.id if femea else recria.id,
                   origem="nascido", data_entrada=nasc, situacao_reprodutiva="vazia" if femea else "nao_aplicavel",
                   situacao_produtiva="reproducao" if femea else "recria", criado_por=prod.id)
        db.session.add(a)
        animais.append(a)
    db.session.flush()
    for a in animais:
        db.session.add(MovimentacaoAnimal(animal_id=a.id, tipo="entrada", data=a.data_entrada, lote_destino_id=a.lote_id,
                                          observacao="Demonstração", registrado_por=prod.id))
        base = Decimal(rng.randint(400, 480) if a.sexo == "F" else rng.randint(240, 300))
        for k in range(4):
            dia = hoje - timedelta(days=90 - k * 30)
            peso = base + Decimal(k * (rng.randint(8, 16) if a.sexo == "M" else rng.randint(2, 8)))
            db.session.add(Pesagem(propriedade_id=fazenda.id, animal_id=a.id, data=dia, peso_kg=peso, responsavel="Demonstração",
                                   registrado_por=prod.id))
        a.peso_atual, a.data_peso_atual = peso, dia
    for idx, a in enumerate(animais[:12]):
        servico = hoje - timedelta(days=150)
        db.session.add(EventoReprodutivo(propriedade_id=fazenda.id, animal_id=a.id, tipo="inseminacao", data=servico,
                                         reprodutor_descricao="Sêmen (demonstração)", registrado_por=prod.id))
        if idx < 8:
            db.session.add(EventoReprodutivo(propriedade_id=fazenda.id, animal_id=a.id, tipo="diagnostico", data=servico + timedelta(days=35),
                                             diagnostico_resultado="positivo", metodo_diagnostico="Ultrassonografia",
                                             responsavel="Veterinária Demonstração", previsao_parto=servico + timedelta(days=283),
                                             registrado_por=prod.id))
            a.situacao_reprodutiva = "gestante"
        elif idx < 10:
            a.situacao_reprodutiva = "servida"
    db.session.add(EventoSanitario(propriedade_id=fazenda.id, lote_id=matrizes.id, tipo="vacina", produto="Clostridioses",
                                   data_aplicacao=hoje - timedelta(days=170), proxima_data=hoje + timedelta(days=10),
                                   responsavel="Demonstração", registrado_por=prod.id))
    db.session.add(EventoSanitario(propriedade_id=fazenda.id, lote_id=recria.id, tipo="vermifugo", produto="Ivermectina",
                                   data_aplicacao=hoje - timedelta(days=100), proxima_data=hoje - timedelta(days=5),
                                   responsavel="Demonstração", registrado_por=prod.id))
    db.session.add(Insumo(propriedade_id=fazenda.id, nome="Sal mineral (demonstração)", categoria="suplemento", unidade="sc",
                          quantidade=Decimal("3"), quantidade_minima=Decimal("5"), custo_medio=Decimal("120")))
    cat = {c.slug: c.id for c in CategoriaFinanceira.query.filter_by(propriedade_id=None).all()}
    lancs = [
        ("receita", "venda_animais", "Venda de bezerros (demonstração)", Decimal("38500"), 45, True),
        ("despesa", "racao", "Ração e suplemento (demonstração)", Decimal("6200"), 40, True),
        ("despesa", "medicamentos", "Vacinas (demonstração)", Decimal("1450"), 20, True),
        ("despesa", "mao_obra", "Mão de obra (demonstração)", Decimal("4800"), 15, True),
        ("despesa", "energia", "Energia (demonstração)", Decimal("680"), 5, False),
    ]
    for tipo, slug, desc, valor, dias, pago in lancs:
        l = Lancamento(propriedade_id=fazenda.id, tipo=tipo, descricao=desc, categoria_id=cat[slug], valor=valor,
                       data=hoje - timedelta(days=dias), vencimento=hoje - timedelta(days=dias) + timedelta(days=10),
                       registrado_por=prod.id)
        if pago:
            l.pagamentos.append(Pagamento(data=hoje - timedelta(days=dias), valor=valor, registrado_por=prod.id))
        db.session.add(l)
    db.session.add(Tarefa(user_id=prod.id, propriedade_id=fazenda.id, responsavel_id=prod.id, tipo="pesagem",
                          titulo="Pesagem do lote Recria (demonstração)", data=hoje + timedelta(days=3)))

    cat_vet = CategoriaProfissional.query.filter_by(slug="veterinario").first()
    perfil = PerfilProfissional(user_id=vet.id, categoria_id=cat_vet.id, titulo="Reprodução e sanidade de bovinos (DEMONSTRAÇÃO)",
                                descricao="Perfil fictício criado para demonstrar a plataforma. Não corresponde a uma profissional real.",
                                especialidades="reprodução, IATF, sanidade", municipio="Uberaba", uf="MG", modalidades="presencial,consultoria",
                                preco_info="Sob orçamento", is_demo=True)
    db.session.add(perfil)
    db.session.flush()
    db.session.add(ServicoProfissional(perfil_id=perfil.id, nome="Diagnóstico de gestação (demonstração)", preco_info="Sob orçamento"))
    db.session.add(Anuncio(user_id=prod.id, categoria="animais", titulo="[DEMONSTRAÇÃO] Novilhas Nelore",
                           descricao="Anúncio fictício criado apenas para demonstrar a plataforma. Não há animais à venda.",
                           tipo_preco="negociavel", preco=Decimal("3200"), unidade_preco="por cabeça", municipio="Uberaba", uf="MG",
                           especie="bovino", raca="Nelore", quantidade=10, idade_meses=24, sexo="femeas", is_demo=True))
    db.session.add(Vaga(user_id=prod.id, empregador="Fazenda Demonstração", cargo="[DEMONSTRAÇÃO] Vaqueiro", categoria="vaqueiro",
                        descricao="Vaga fictícia criada apenas para demonstrar a plataforma. Não há contratação real.",
                        tipo_contratacao="clt", municipio="Uberaba", uf="MG", is_demo=True))
    db.session.commit()
