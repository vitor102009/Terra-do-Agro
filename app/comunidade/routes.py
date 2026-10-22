from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.feed.routes import membro_grupo
from app.models import Grupo, GrupoMembro, Post, Reacao, User
from app.models.social import PAPEIS_GRUPO, TEMAS_GRUPO, TIPOS_POST
from app.utils.navegacao import pagina
from app.utils.uploads import UploadError, remover_upload, salvar_upload, tem_arquivo
from app.utils.validacao import Form, valores_de

bp = Blueprint("comunidade", __name__)


def _grupo_ativo(gid):
    g = Grupo.query.filter_by(id=gid, ativo=True).first()
    if not g:
        abort(404)
    return g


def _admin_grupo(grupo):
    mb = membro_grupo(grupo.id)
    if not (current_user.is_admin or (mb and mb.papel == "admin")):
        abort(403)
    return mb


@bp.route("/")
@login_required
def index():
    a = request.args
    q = Grupo.query.filter_by(ativo=True)
    if a.get("tema") in TEMAS_GRUPO:
        q = q.filter_by(tema=a["tema"])
    if a.get("uf"):
        q = q.filter(Grupo.uf == a["uf"].upper()[:2])
    if a.get("q"):
        termo = f"%{a['q'].strip()}%"
        q = q.filter(db.or_(Grupo.nome.ilike(termo), Grupo.descricao.ilike(termo), Grupo.municipio.ilike(termo)))
    grupos = q.order_by(Grupo.nome).all()
    contagem = dict(db.session.query(GrupoMembro.grupo_id, db.func.count(GrupoMembro.id)).group_by(GrupoMembro.grupo_id).all())
    meus = {m.grupo_id: m.papel for m in GrupoMembro.query.filter_by(user_id=current_user.id).all()}
    return render_template("comunidade/index.html", grupos=grupos, contagem=contagem, meus=meus, temas=TEMAS_GRUPO, args=a)


def _ler(form, grupo=None):
    dados = {
        "nome": form.text("nome", required=True, max_len=120, min_len=3),
        "descricao": form.text("descricao", required=True, max_len=3000, min_len=10),
        "tema": form.choice("tema", TEMAS_GRUPO),
        "regras": form.text("regras", max_len=4000),
        "municipio": form.text("municipio", max_len=120),
        "uf": form.uf("uf", required=False),
    }
    q = Grupo.query.filter(db.func.lower(Grupo.nome) == (dados["nome"] or "").lower())
    if grupo:
        q = q.filter(Grupo.id != grupo.id)
    if dados["nome"] and q.first():
        form.error("nome", "Já existe um grupo com este nome.")
    return dados


def _capa(form, grupo):
    arq = request.files.get("capa")
    if tem_arquivo(arq):
        try:
            nova = salvar_upload(arq, "imagem", "grupos")
            remover_upload(grupo.capa)
            grupo.capa = nova
        except UploadError as exc:
            form.error("capa", str(exc))


@bp.route("/novo", methods=["GET", "POST"])
@login_required
def novo():
    v, e = {"tema": request.args.get("tema", "")}, {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form)
        grupo = Grupo(criado_por=current_user.id, **dados)
        if form.ok:
            _capa(form, grupo)
        if form.ok:
            db.session.add(grupo)
            db.session.flush()
            db.session.add(GrupoMembro(grupo_id=grupo.id, user_id=current_user.id, papel="admin"))
            db.session.commit()
            flash("Grupo criado. Você é o administrador.", "success")
            return redirect(url_for("comunidade.grupo", gid=grupo.id))
        v, e = form.values, form.errors
    return render_template("comunidade/form.html", v=v, e=e, grupo=None, temas=TEMAS_GRUPO)


@bp.route("/<int:gid>/editar", methods=["GET", "POST"])
@login_required
def editar(gid):
    grupo = _grupo_ativo(gid)
    _admin_grupo(grupo)
    v, e = valores_de(grupo, ["nome", "descricao", "tema", "regras", "municipio", "uf"]), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, grupo)
        if form.ok:
            _capa(form, grupo)
        if form.ok:
            for k, val in dados.items():
                setattr(grupo, k, val)
            db.session.commit()
            flash("Grupo atualizado.", "success")
            return redirect(url_for("comunidade.grupo", gid=gid))
        v, e = form.values, form.errors
    return render_template("comunidade/form.html", v=v, e=e, grupo=grupo, temas=TEMAS_GRUPO)


@bp.route("/<int:gid>")
@login_required
def grupo(gid):
    grupo = _grupo_ativo(gid)
    mb = membro_grupo(gid)
    pag = (Post.query.join(User, Post.autor_id == User.id)
           .filter(Post.grupo_id == gid, Post.removido.is_(False), User.ativo.is_(True))
           .order_by(Post.created_at.desc()).paginate(page=pagina(), per_page=15, error_out=False))
    curtidos = {r.post_id for r in Reacao.query.filter(Reacao.user_id == current_user.id,
                                                       Reacao.post_id.in_([p.id for p in pag.items])).all()} if pag.items else set()
    equipe = GrupoMembro.query.filter(GrupoMembro.grupo_id == gid, GrupoMembro.papel.in_(("admin", "moderador"))).all()
    total = GrupoMembro.query.filter_by(grupo_id=gid).count()
    return render_template("comunidade/grupo.html", grupo=grupo, mb=mb, pag=pag, curtidos=curtidos, equipe=equipe, total=total,
                           tipos=TIPOS_POST, papeis=PAPEIS_GRUPO)


@bp.route("/<int:gid>/entrar", methods=["POST"])
@login_required
def entrar(gid):
    grupo = _grupo_ativo(gid)
    if not membro_grupo(gid):
        db.session.add(GrupoMembro(grupo_id=grupo.id, user_id=current_user.id, papel="membro"))
        db.session.commit()
        flash(f"Você entrou no grupo {grupo.nome}.", "success")
    return redirect(url_for("comunidade.grupo", gid=gid))


@bp.route("/<int:gid>/sair", methods=["POST"])
@login_required
def sair(gid):
    grupo = _grupo_ativo(gid)
    mb = membro_grupo(gid)
    if mb:
        if mb.papel == "admin" and GrupoMembro.query.filter_by(grupo_id=gid, papel="admin").count() == 1 \
                and GrupoMembro.query.filter_by(grupo_id=gid).count() > 1:
            flash("Você é o único administrador. Promova outro participante a administrador antes de sair.", "warning")
            return redirect(url_for("comunidade.membros", gid=gid))
        db.session.delete(mb)
        db.session.commit()
        flash(f"Você saiu do grupo {grupo.nome}.", "info")
    return redirect(url_for("comunidade.index"))


@bp.route("/<int:gid>/membros", methods=["GET", "POST"])
@login_required
def membros(gid):
    grupo = _grupo_ativo(gid)
    meu = membro_grupo(gid)
    eh_admin = current_user.is_admin or (meu and meu.papel == "admin")
    if request.method == "POST":
        if not eh_admin:
            abort(403)
        alvo = GrupoMembro.query.filter_by(id=request.form.get("membro_id", type=int), grupo_id=gid).first_or_404()
        acao = request.form.get("acao")
        if acao == "remover":
            if alvo.user_id == current_user.id:
                flash("Use a opção Sair do grupo.", "warning")
            else:
                db.session.delete(alvo)
                flash("Participante removido.", "info")
        elif acao in PAPEIS_GRUPO:
            if alvo.papel == "admin" and acao != "admin" and GrupoMembro.query.filter_by(grupo_id=gid, papel="admin").count() == 1:
                flash("O grupo precisa de pelo menos um administrador.", "warning")
            else:
                alvo.papel = acao
                flash("Papel atualizado.", "success")
        db.session.commit()
        return redirect(url_for("comunidade.membros", gid=gid))
    lista = (GrupoMembro.query.filter_by(grupo_id=gid).join(User, GrupoMembro.user_id == User.id)
             .order_by(db.case((GrupoMembro.papel == "admin", 0), (GrupoMembro.papel == "moderador", 1), else_=2), User.nome).all())
    return render_template("comunidade/membros.html", grupo=grupo, lista=lista, eh_admin=eh_admin, papeis=PAPEIS_GRUPO)
