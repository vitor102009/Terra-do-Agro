from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Anuncio, AnuncioFoto, Favorito, PerfilEmpresa, User
from app.models.mercado import CATEGORIAS_ANUNCIO, ICONES_CATEGORIA_ANUNCIO, STATUS_ANUNCIO, TIPOS_PRECO
from app.models.rebanho import ESPECIES
from app.utils.navegacao import pagina
from app.utils.uploads import UploadError, remover_upload, salvar_upload, tem_arquivo
from app.utils.validacao import Form, parse_decimal, valores_de

bp = Blueprint("marketplace", __name__)

MAX_FOTOS = 8
CAMPOS = ["categoria", "titulo", "descricao", "tipo_preco", "preco", "unidade_preco", "municipio", "uf", "condicoes", "entrega",
          "especie", "raca", "quantidade", "idade_meses", "sexo", "peso_medio_kg", "documentacao"]
SEXOS_ANUNCIO = {"machos": "Machos", "femeas": "Fêmeas", "misto": "Machos e fêmeas"}


@bp.route("/")
@login_required
def index():
    a = request.args
    q = Anuncio.query.join(User).filter(Anuncio.status == "ativo", User.ativo.is_(True))
    if a.get("categoria") in CATEGORIAS_ANUNCIO:
        q = q.filter(Anuncio.categoria == a["categoria"])
    if a.get("q"):
        termo = f"%{a['q'].strip()}%"
        q = q.filter(db.or_(Anuncio.titulo.ilike(termo), Anuncio.descricao.ilike(termo), Anuncio.raca.ilike(termo)))
    if a.get("uf"):
        q = q.filter(Anuncio.uf == a["uf"].upper()[:2])
    if a.get("municipio"):
        q = q.filter(Anuncio.municipio.ilike(f"%{a['municipio'].strip()}%"))
    for campo, op in (("preco_min", ">="), ("preco_max", "<=")):
        try:
            valor = parse_decimal(a.get(campo))
        except Exception:  # noqa: BLE001
            valor = None
        if valor is not None:
            q = q.filter(Anuncio.tipo_preco != "orcamento", Anuncio.preco >= valor if op == ">=" else Anuncio.preco <= valor)
    ordem = {"menor_preco": Anuncio.preco.asc().nulls_last(), "maior_preco": Anuncio.preco.desc().nulls_last()}.get(
        a.get("ordem"), Anuncio.created_at.desc())
    pag = q.order_by(ordem, Anuncio.id.desc()).paginate(page=pagina(), per_page=24, error_out=False)
    favs = {f.anuncio_id for f in Favorito.query.filter_by(user_id=current_user.id).all()}
    return render_template("marketplace/index.html", pag=pag, categorias=CATEGORIAS_ANUNCIO, icones=ICONES_CATEGORIA_ANUNCIO,
                           args=a, favs=favs, tipos_preco=TIPOS_PRECO)


@bp.route("/<int:aid>")
@login_required
def detalhe(aid):
    an = db.session.get(Anuncio, aid)
    dono = an and an.user_id == current_user.id
    if not an or (an.status == "removido" and not current_user.is_admin and not dono) or (not an.autor.ativo and not dono):
        abort(404)
    if an.status != "ativo" and not dono and not current_user.is_admin:
        abort(404)
    if not dono:
        an.visualizacoes = (an.visualizacoes or 0) + 1
        db.session.commit()
    favorito = Favorito.query.filter_by(user_id=current_user.id, anuncio_id=aid).first() is not None
    empresa = PerfilEmpresa.query.filter_by(user_id=an.user_id, publico=True).first()
    outros = (Anuncio.query.filter(Anuncio.user_id == an.user_id, Anuncio.id != aid, Anuncio.status == "ativo")
              .order_by(Anuncio.created_at.desc()).limit(4).all())
    return render_template("marketplace/detalhe.html", an=an, dono=dono, favorito=favorito, empresa=empresa, outros=outros,
                           tipos_preco=TIPOS_PRECO, especies=ESPECIES, sexos=SEXOS_ANUNCIO, status=STATUS_ANUNCIO)


def _ler(form):
    dados = {
        "categoria": form.choice("categoria", CATEGORIAS_ANUNCIO),
        "titulo": form.text("titulo", required=True, max_len=150, min_len=5),
        "descricao": form.text("descricao", required=True, max_len=6000, min_len=20),
        "tipo_preco": form.choice("tipo_preco", TIPOS_PRECO),
        "preco": form.decimal("preco", min_value=0, max_value=1_000_000_000),
        "unidade_preco": form.text("unidade_preco", max_len=40),
        "municipio": form.text("municipio", required=True, max_len=120),
        "uf": form.uf("uf"),
        "condicoes": form.text("condicoes", max_len=3000),
        "entrega": form.text("entrega", max_len=3000),
        "especie": None, "raca": None, "quantidade": None, "idade_meses": None, "sexo": None, "peso_medio_kg": None, "documentacao": None,
    }
    if dados["tipo_preco"] in ("fixo", "negociavel") and not dados["preco"]:
        form.error("preco", "Informe o preço ou escolha 'Sob orçamento'.")
    if dados["tipo_preco"] == "orcamento":
        dados["preco"] = None
    if dados["categoria"] == "animais":
        dados.update({
            "especie": form.choice("especie", ESPECIES),
            "raca": form.text("raca", max_len=60),
            "quantidade": form.integer("quantidade", required=True, min_value=1, max_value=100000),
            "idade_meses": form.integer("idade_meses", min_value=0, max_value=600),
            "sexo": form.choice("sexo", SEXOS_ANUNCIO, required=False),
            "peso_medio_kg": form.decimal("peso_medio_kg", min_value=0, max_value=5000),
            "documentacao": form.text("documentacao", max_len=2000),
        })
    return dados


def _fotos(form, an):
    arquivos = [f for f in request.files.getlist("fotos") if tem_arquivo(f)]
    if len(an.fotos) + len(arquivos) > MAX_FOTOS:
        form.error("fotos", f"Máximo de {MAX_FOTOS} fotografias por anúncio.")
        return
    salvos = []
    for i, arq in enumerate(arquivos):
        try:
            salvos.append(salvar_upload(arq, "imagem", "anuncios"))
        except UploadError as exc:
            form.error("fotos", f"{arq.filename}: {exc}")
            for s in salvos:
                remover_upload(s)
            return
    base = len(an.fotos)
    for i, s in enumerate(salvos):
        an.fotos.append(AnuncioFoto(arquivo=s, ordem=base + i))


def _ctx():
    return dict(categorias=CATEGORIAS_ANUNCIO, tipos_preco=TIPOS_PRECO, especies=ESPECIES, sexos=SEXOS_ANUNCIO, max_fotos=MAX_FOTOS)


@bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    v = {"categoria": request.args.get("categoria", ""), "tipo_preco": "fixo", "municipio": current_user.municipio or "",
         "uf": current_user.uf or ""}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form)
        an = Anuncio(user_id=current_user.id, status="ativo", **dados)
        if form.ok:
            _fotos(form, an)
        if form.ok:
            db.session.add(an)
            db.session.commit()
            flash("Anúncio publicado.", "success")
            return redirect(url_for("marketplace.detalhe", aid=an.id))
        v, e = form.values, form.errors
    return render_template("marketplace/form.html", v=v, e=e, an=None, **_ctx())


@bp.route("/<int:aid>/editar", methods=["GET", "POST"])
@login_required
def editar(aid):
    an = Anuncio.query.filter_by(id=aid, user_id=current_user.id).first_or_404()
    if an.status == "removido":
        flash("Este anúncio foi removido pela moderação e não pode ser editado.", "warning")
        return redirect(url_for("marketplace.meus"))
    v, e = valores_de(an, CAMPOS), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form)
        if form.ok:
            _fotos(form, an)
        if form.ok:
            for k, val in dados.items():
                setattr(an, k, val)
            db.session.commit()
            flash("Anúncio atualizado.", "success")
            return redirect(url_for("marketplace.detalhe", aid=aid))
        db.session.rollback()
        v, e = form.values, form.errors
    return render_template("marketplace/form.html", v=v, e=e, an=an, **_ctx())


@bp.route("/<int:aid>/fotos/<int:fid>/remover", methods=["POST"])
@login_required
def remover_foto(aid, fid):
    an = Anuncio.query.filter_by(id=aid, user_id=current_user.id).first_or_404()
    foto = AnuncioFoto.query.filter_by(id=fid, anuncio_id=an.id).first_or_404()
    remover_upload(foto.arquivo)
    db.session.delete(foto)
    db.session.commit()
    flash("Foto removida.", "info")
    return redirect(url_for("marketplace.editar", aid=aid))


@bp.route("/<int:aid>/status", methods=["POST"])
@login_required
def status(aid):
    an = Anuncio.query.filter_by(id=aid, user_id=current_user.id).first_or_404()
    novo = request.form.get("status")
    if novo not in ("ativo", "pausado", "vendido") or an.status == "removido":
        abort(400)
    an.status = novo
    db.session.commit()
    flash(f"Anúncio: {STATUS_ANUNCIO[novo]}.", "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for("marketplace.meus"))


@bp.route("/meus")
@login_required
def meus():
    anuncios = Anuncio.query.filter_by(user_id=current_user.id).order_by(Anuncio.created_at.desc()).all()
    return render_template("marketplace/meus.html", anuncios=anuncios, status_opcoes=STATUS_ANUNCIO)


@bp.route("/favoritos")
@login_required
def favoritos():
    favs = (Favorito.query.join(Anuncio).filter(Favorito.user_id == current_user.id, Anuncio.status != "removido")
            .order_by(Favorito.created_at.desc()).all())
    return render_template("marketplace/favoritos.html", favs=favs, tipos_preco=TIPOS_PRECO)


@bp.route("/<int:aid>/favoritar", methods=["POST"])
@login_required
def favoritar(aid):
    an = Anuncio.query.filter_by(id=aid).first_or_404()
    fav = Favorito.query.filter_by(user_id=current_user.id, anuncio_id=an.id).first()
    if fav:
        db.session.delete(fav)
        msg = "Removido dos favoritos."
    else:
        db.session.add(Favorito(user_id=current_user.id, anuncio_id=an.id))
        msg = "Adicionado aos favoritos."
    db.session.commit()
    if request.accept_mimetypes.best == "application/json":
        return {"ok": True, "favorito": not fav}
    flash(msg, "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for("marketplace.detalhe", aid=aid))
