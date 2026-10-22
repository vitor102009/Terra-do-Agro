from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Candidatura, PerfilTrabalhador, User, Vaga
from app.models.mercado import CATEGORIAS_TRABALHO, STATUS_CANDIDATURA, TIPOS_CONTRATACAO
from app.utils.navegacao import pagina
from app.utils.registro import notificar
from app.utils.validacao import Form, valores_de

bp = Blueprint("vagas", __name__)

CAMPOS_VAGA = ["empregador", "cargo", "categoria", "descricao", "atividades", "requisitos", "experiencia", "tipo_contratacao",
               "jornada", "salario_min", "salario_max", "beneficios", "municipio", "uf", "prazo", "forma_candidatura"]


@bp.route("/")
@login_required
def index():
    a = request.args
    q = Vaga.query.join(User).filter(Vaga.status == "aberta", User.ativo.is_(True),
                                     db.or_(Vaga.prazo.is_(None), Vaga.prazo >= date.today()))
    if a.get("categoria") in CATEGORIAS_TRABALHO:
        q = q.filter(Vaga.categoria == a["categoria"])
    if a.get("tipo") in TIPOS_CONTRATACAO:
        q = q.filter(Vaga.tipo_contratacao == a["tipo"])
    if a.get("uf"):
        q = q.filter(Vaga.uf == a["uf"].upper()[:2])
    if a.get("municipio"):
        q = q.filter(Vaga.municipio.ilike(f"%{a['municipio'].strip()}%"))
    if a.get("q"):
        termo = f"%{a['q'].strip()}%"
        q = q.filter(db.or_(Vaga.cargo.ilike(termo), Vaga.descricao.ilike(termo), Vaga.empregador.ilike(termo)))
    pag = q.order_by(Vaga.created_at.desc()).paginate(page=pagina(), per_page=20, error_out=False)
    minhas = Candidatura.query.filter_by(user_id=current_user.id).count()
    publicadas = Vaga.query.filter_by(user_id=current_user.id).count()
    return render_template("vagas/index.html", pag=pag, categorias=CATEGORIAS_TRABALHO, tipos=TIPOS_CONTRATACAO, args=a,
                           minhas=minhas, publicadas=publicadas,
                           perfil=PerfilTrabalhador.query.filter_by(user_id=current_user.id).first())


def _ler_vaga(form):
    dados = {
        "empregador": form.text("empregador", required=True, max_len=150),
        "cargo": form.text("cargo", required=True, max_len=150),
        "categoria": form.choice("categoria", CATEGORIAS_TRABALHO),
        "descricao": form.text("descricao", required=True, max_len=5000, min_len=20),
        "atividades": form.text("atividades", max_len=4000),
        "requisitos": form.text("requisitos", max_len=4000),
        "experiencia": form.text("experiencia", max_len=150),
        "tipo_contratacao": form.choice("tipo_contratacao", TIPOS_CONTRATACAO),
        "jornada": form.text("jornada", max_len=150),
        "salario_min": form.decimal("salario_min", min_value=0),
        "salario_max": form.decimal("salario_max", min_value=0),
        "beneficios": form.text("beneficios", max_len=2000),
        "municipio": form.text("municipio", required=True, max_len=120),
        "uf": form.uf("uf"),
        "prazo": form.date("prazo", min_date=date.today()),
        "forma_candidatura": form.text("forma_candidatura", max_len=1000),
    }
    if dados["salario_min"] and dados["salario_max"] and dados["salario_max"] < dados["salario_min"]:
        form.error("salario_max", "O valor máximo deve ser maior que o mínimo.")
    return dados


@bp.route("/nova", methods=["GET", "POST"])
@login_required
def nova():
    from flask import g

    v = {"empregador": g.propriedade.nome if g.get("propriedade") else current_user.nome, "tipo_contratacao": "clt",
         "municipio": g.propriedade.municipio if g.get("propriedade") else (current_user.municipio or ""),
         "uf": g.propriedade.uf if g.get("propriedade") else (current_user.uf or "")}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler_vaga(form)
        if form.ok:
            vaga = Vaga(user_id=current_user.id, **dados)
            db.session.add(vaga)
            db.session.commit()
            flash("Vaga publicada.", "success")
            return redirect(url_for("vagas.detalhe", vid=vaga.id))
        v, e = form.values, form.errors
    return render_template("vagas/form.html", v=v, e=e, vaga=None, categorias=CATEGORIAS_TRABALHO, tipos=TIPOS_CONTRATACAO)


@bp.route("/<int:vid>/editar", methods=["GET", "POST"])
@login_required
def editar(vid):
    vaga = Vaga.query.filter_by(id=vid, user_id=current_user.id).first_or_404()
    v, e = valores_de(vaga, CAMPOS_VAGA), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler_vaga(form)
        if form.ok:
            for k, val in dados.items():
                setattr(vaga, k, val)
            db.session.commit()
            flash("Vaga atualizada.", "success")
            return redirect(url_for("vagas.detalhe", vid=vid))
        v, e = form.values, form.errors
    return render_template("vagas/form.html", v=v, e=e, vaga=vaga, categorias=CATEGORIAS_TRABALHO, tipos=TIPOS_CONTRATACAO)


@bp.route("/<int:vid>")
@login_required
def detalhe(vid):
    vaga = db.session.get(Vaga, vid)
    if not vaga or (vaga.status == "removida" and not current_user.is_admin) or (not vaga.autor.ativo and vaga.user_id != current_user.id):
        abort(404)
    dono = vaga.user_id == current_user.id
    minha = Candidatura.query.filter_by(vaga_id=vid, user_id=current_user.id).first()
    candidaturas = []
    if dono:
        candidaturas = Candidatura.query.filter_by(vaga_id=vid).order_by(Candidatura.created_at).all()
    perfis = {c.user_id: PerfilTrabalhador.query.filter_by(user_id=c.user_id).first() for c in candidaturas}
    return render_template("vagas/detalhe.html", vaga=vaga, dono=dono, minha=minha, candidaturas=candidaturas, perfis=perfis,
                           categorias=CATEGORIAS_TRABALHO, tipos=TIPOS_CONTRATACAO, status=STATUS_CANDIDATURA,
                           perfil=PerfilTrabalhador.query.filter_by(user_id=current_user.id).first(), hoje=date.today())


@bp.route("/<int:vid>/candidatar", methods=["POST"])
@login_required
def candidatar(vid):
    vaga = Vaga.query.filter_by(id=vid, status="aberta").first_or_404()
    if vaga.user_id == current_user.id:
        abort(400)
    if vaga.prazo and vaga.prazo < date.today():
        flash("O prazo de inscrição desta vaga terminou.", "warning")
        return redirect(url_for("vagas.detalhe", vid=vid))
    if not PerfilTrabalhador.query.filter_by(user_id=current_user.id).first():
        flash("Crie seu perfil de trabalhador antes de se candidatar.", "info")
        return redirect(url_for("vagas.meu_perfil", next=url_for("vagas.detalhe", vid=vid)))
    mensagem = (request.form.get("mensagem") or "").strip()[:2000]
    try:
        db.session.add(Candidatura(vaga_id=vid, user_id=current_user.id, mensagem=mensagem))
        notificar(vaga.user_id, "candidatura", f"Nova candidatura: {vaga.cargo}", f"{current_user.nome} se candidatou.",
                  url_for("vagas.detalhe", vid=vid))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("Você já se candidatou a esta vaga.", "info")
        return redirect(url_for("vagas.detalhe", vid=vid))
    flash("Candidatura enviada. Acompanhe a situação em Vagas › Minhas candidaturas.", "success")
    return redirect(url_for("vagas.detalhe", vid=vid))


@bp.route("/candidaturas/<int:cid>/status", methods=["POST"])
@login_required
def status_candidatura(cid):
    cand = db.session.get(Candidatura, cid)
    if not cand:
        abort(404)
    novo = request.form.get("status")
    if cand.vaga.user_id == current_user.id:
        if novo not in STATUS_CANDIDATURA:
            abort(400)
        cand.status = novo
        cand.nota_empregador = (request.form.get("nota_empregador") or cand.nota_empregador or "")[:2000] or None
        notificar(cand.user_id, "candidatura", f"Candidatura atualizada: {cand.vaga.cargo}", STATUS_CANDIDATURA[novo],
                  url_for("vagas.minhas_candidaturas"))
    elif cand.user_id == current_user.id and novo == "encerrada":
        cand.status = "encerrada"  # desistência do candidato
    else:
        abort(404)
    db.session.commit()
    flash("Candidatura atualizada.", "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for("vagas.index"))


@bp.route("/<int:vid>/encerrar", methods=["POST"])
@login_required
def encerrar(vid):
    vaga = Vaga.query.filter_by(id=vid, user_id=current_user.id).first_or_404()
    vaga.status = "encerrada" if vaga.status == "aberta" else "aberta"
    db.session.commit()
    flash("Vaga encerrada." if vaga.status == "encerrada" else "Vaga reaberta.", "info")
    return redirect(url_for("vagas.detalhe", vid=vid))


@bp.route("/minhas")
@login_required
def minhas_vagas():
    vagas = Vaga.query.filter_by(user_id=current_user.id).order_by(Vaga.created_at.desc()).all()
    return render_template("vagas/minhas.html", vagas=vagas)


@bp.route("/minhas-candidaturas")
@login_required
def minhas_candidaturas():
    cands = Candidatura.query.filter_by(user_id=current_user.id).order_by(Candidatura.updated_at.desc()).all()
    return render_template("vagas/candidaturas.html", cands=cands, status=STATUS_CANDIDATURA)


CAMPOS_TRAB = ["titulo", "habilidades", "experiencias", "descricao", "municipio", "uf", "regiao_interesse", "disponibilidade",
               "disponivel", "telefone_contato", "compartilhar_contato", "visivel_busca"]


@bp.route("/meu-perfil", methods=["GET", "POST"])
@login_required
def meu_perfil():
    perfil = PerfilTrabalhador.query.filter_by(user_id=current_user.id).first()
    if perfil:
        v = valores_de(perfil, CAMPOS_TRAB)
        v["categorias"] = perfil.lista_categorias
    else:
        v = {"municipio": current_user.municipio or "", "uf": current_user.uf or "", "disponivel": True, "visivel_busca": True}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = {
            "titulo": form.text("titulo", required=True, max_len=150),
            "habilidades": form.text("habilidades", max_len=3000),
            "experiencias": form.text("experiencias", max_len=5000),
            "descricao": form.text("descricao", max_len=3000),
            "municipio": form.text("municipio", required=True, max_len=120),
            "uf": form.uf("uf"),
            "regiao_interesse": form.text("regiao_interesse", max_len=255),
            "disponibilidade": form.text("disponibilidade", max_len=150),
            "disponivel": form.boolean("disponivel"),
            "telefone_contato": form.text("telefone_contato", max_len=30),
            "compartilhar_contato": form.boolean("compartilhar_contato"),
            "visivel_busca": form.boolean("visivel_busca"),
        }
        cats = form.multi("categorias", CATEGORIAS_TRABALHO)
        if not cats:
            form.error("categorias", "Escolha ao menos um tipo de trabalho.")
        dados["categorias"] = ",".join(cats)
        if form.ok:
            novo = perfil is None
            if novo:
                perfil = PerfilTrabalhador(user_id=current_user.id)
            for k, val in dados.items():
                setattr(perfil, k, val)
            if novo:
                db.session.add(perfil)
                current_user.add_role("trabalhador")
            db.session.commit()
            flash("Perfil de trabalhador salvo.", "success")
            from app.utils.navegacao import destino_seguro

            return redirect(destino_seguro(request.args.get("next"), "vagas.index"))
        v, e = form.values, form.errors
    return render_template("vagas/perfil.html", v=v, e=e, perfil=perfil, categorias=CATEGORIAS_TRABALHO)


@bp.route("/trabalhadores")
@login_required
def trabalhadores():
    a = request.args
    q = PerfilTrabalhador.query.join(User).filter(PerfilTrabalhador.visivel_busca.is_(True), User.ativo.is_(True))
    if a.get("categoria") in CATEGORIAS_TRABALHO:
        q = q.filter(PerfilTrabalhador.categorias.ilike(f"%{a['categoria']}%"))
    if a.get("uf"):
        q = q.filter(PerfilTrabalhador.uf == a["uf"].upper()[:2])
    if a.get("q"):
        termo = f"%{a['q'].strip()}%"
        q = q.filter(db.or_(PerfilTrabalhador.titulo.ilike(termo), PerfilTrabalhador.habilidades.ilike(termo),
                            PerfilTrabalhador.municipio.ilike(termo), PerfilTrabalhador.regiao_interesse.ilike(termo)))
    if a.get("disponivel") == "1":
        q = q.filter(PerfilTrabalhador.disponivel.is_(True))
    pag = q.order_by(PerfilTrabalhador.updated_at.desc()).paginate(page=pagina(), per_page=20, error_out=False)
    return render_template("vagas/trabalhadores.html", pag=pag, categorias=CATEGORIAS_TRABALHO, args=a)
