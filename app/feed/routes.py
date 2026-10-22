from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Comentario, Grupo, GrupoMembro, PerfilProfissional, Post, Reacao, User
from app.models.social import TIPOS_POST
from app.utils.navegacao import destino_seguro, pagina
from app.utils.registro import notificar
from app.utils.uploads import UploadError, remover_upload, salvar_upload, tem_arquivo
from app.utils.validacao import Form

bp = Blueprint("feed", __name__)


def membro_grupo(grupo_id, user_id=None):
    return GrupoMembro.query.filter_by(grupo_id=grupo_id, user_id=user_id or current_user.id).first()


def pode_moderar(post):
    if current_user.is_admin:
        return True
    if post.grupo_id:
        mb = membro_grupo(post.grupo_id)
        return bool(mb and mb.papel in ("admin", "moderador"))
    return False


def visivel(post):
    return post and not post.removido and post.autor.ativo


def _base():
    return (Post.query.join(User, Post.autor_id == User.id)
            .filter(Post.removido.is_(False), User.ativo.is_(True)))


@bp.app_context_processor
def ajudantes_feed():
    return {"pode_moderar_post": pode_moderar}


@bp.route("/")
@login_required
def index():
    q = _base().filter(Post.grupo_id.is_(None))
    if request.args.get("tipo") in TIPOS_POST:
        q = q.filter(Post.tipo == request.args["tipo"])
    pag = q.order_by(Post.created_at.desc()).paginate(page=pagina(), per_page=15, error_out=False)
    curtidos = {r.post_id for r in Reacao.query.filter(Reacao.user_id == current_user.id,
                                                       Reacao.post_id.in_([p.id for p in pag.items])).all()} if pag.items else set()
    meus_grupos = (Grupo.query.join(GrupoMembro).filter(GrupoMembro.user_id == current_user.id, Grupo.ativo.is_(True))
                   .order_by(Grupo.nome).limit(8).all())
    return render_template("feed/index.html", pag=pag, tipos=TIPOS_POST, curtidos=curtidos, meus_grupos=meus_grupos,
                           args=request.args, grupo=None)


@bp.route("/publicar", methods=["POST"])
@login_required
def publicar():
    grupo_id = request.form.get("grupo_id", type=int)
    destino = url_for("comunidade.grupo", gid=grupo_id) if grupo_id else url_for("feed.index")
    if grupo_id:
        grupo = Grupo.query.filter_by(id=grupo_id, ativo=True).first_or_404()
        if not membro_grupo(grupo.id):
            flash("Entre no grupo para publicar.", "warning")
            return redirect(destino)
    form = Form(request.form)
    texto = form.text("texto", max_len=5000)
    tipo = form.choice("tipo", TIPOS_POST, required=False, default="geral")
    arq = request.files.get("midia")
    midia, midia_tipo = None, None
    if tem_arquivo(arq):
        ext = (arq.filename or "").rsplit(".", 1)[-1].lower()
        midia_tipo = "video" if ext in ("mp4", "webm", "mov") else "imagem"
        try:
            midia = salvar_upload(arq, midia_tipo, "feed")
        except UploadError as exc:
            form.error("midia", str(exc))
    if not texto and not midia and form.ok:
        form.error("texto", "Escreva algo ou anexe uma foto ou vídeo.")
    if not form.ok:
        remover_upload(midia)
        flash("Não foi possível publicar: " + "; ".join(form.errors.values()), "danger")
        return redirect(destino)
    db.session.add(Post(autor_id=current_user.id, grupo_id=grupo_id, tipo=tipo, texto=texto, midia=midia, midia_tipo=midia_tipo))
    db.session.commit()
    flash("Publicado.", "success")
    return redirect(destino)


@bp.route("/<int:pid>")
@login_required
def post(pid):
    p = db.session.get(Post, pid)
    if not visivel(p) and not (p and current_user.is_admin):
        abort(404)
    curtidos = {pid} if Reacao.query.filter_by(post_id=pid, user_id=current_user.id).first() else set()
    return render_template("feed/post.html", p=p, curtidos=curtidos, tipos=TIPOS_POST)


@bp.route("/<int:pid>/editar", methods=["GET", "POST"])
@login_required
def editar(pid):
    p = Post.query.filter_by(id=pid, autor_id=current_user.id, removido=False).first_or_404()
    v, e = {"texto": p.texto or "", "tipo": p.tipo}, {}
    if request.method == "POST":
        form = Form(request.form)
        texto = form.text("texto", max_len=5000)
        tipo = form.choice("tipo", TIPOS_POST)
        if not texto and not p.midia:
            form.error("texto", "A publicação não pode ficar vazia.")
        if form.ok:
            p.texto, p.tipo, p.editado = texto, tipo, True
            db.session.commit()
            flash("Publicação editada.", "success")
            return redirect(url_for("feed.post", pid=pid))
        v, e = form.values, form.errors
    return render_template("feed/editar.html", p=p, v=v, e=e, tipos=TIPOS_POST)


@bp.route("/<int:pid>/excluir", methods=["POST"])
@login_required
def excluir(pid):
    p = Post.query.filter_by(id=pid, removido=False).first_or_404()
    if p.autor_id != current_user.id and not pode_moderar(p):
        abort(403)
    p.removido = True
    p.removido_motivo = "Excluída pelo autor" if p.autor_id == current_user.id else f"Removida pela moderação ({current_user.nome})"
    if p.autor_id != current_user.id:
        notificar(p.autor_id, "moderacao", "Uma publicação sua foi removida pela moderação", (p.texto or "")[:120])
    db.session.commit()
    flash("Publicação removida.", "info")
    return redirect(destino_seguro(request.form.get("next"), "feed.index"))


@bp.route("/<int:pid>/reagir", methods=["POST"])
@login_required
def reagir(pid):
    p = Post.query.filter_by(id=pid, removido=False).first_or_404()
    r = Reacao.query.filter_by(post_id=pid, user_id=current_user.id).first()
    if r:
        db.session.delete(r)
    else:
        db.session.add(Reacao(post_id=pid, user_id=current_user.id))
        if p.autor_id != current_user.id:
            notificar(p.autor_id, "reacao", f"{current_user.primeiro_nome} curtiu sua publicação", (p.texto or "")[:100],
                      url_for("feed.post", pid=pid), chave=f"reacao:{pid}:{current_user.id}")
    db.session.commit()
    total = Reacao.query.filter_by(post_id=pid).count()
    if request.accept_mimetypes.best == "application/json":
        return jsonify(curtido=not r, total=total)
    return redirect(destino_seguro(request.form.get("next"), "feed.index"))


@bp.route("/<int:pid>/comentar", methods=["POST"])
@login_required
def comentar(pid):
    p = Post.query.filter_by(id=pid, removido=False).first_or_404()
    if p.grupo_id and not membro_grupo(p.grupo_id):
        flash("Entre no grupo para comentar.", "warning")
        return redirect(url_for("feed.post", pid=pid))
    texto = (request.form.get("texto") or "").strip()
    if not texto or len(texto) > 2000:
        flash("O comentário deve ter entre 1 e 2000 caracteres.", "danger")
    else:
        db.session.add(Comentario(post_id=pid, autor_id=current_user.id, texto=texto))
        if p.autor_id != current_user.id:
            notificar(p.autor_id, "comentario", f"{current_user.primeiro_nome} comentou sua publicação", texto[:120], url_for("feed.post", pid=pid))
        db.session.commit()
    return redirect(destino_seguro(request.form.get("next"), "feed.index") + "#post-" + str(pid))


@bp.route("/comentarios/<int:cid>/excluir", methods=["POST"])
@login_required
def excluir_comentario(cid):
    c = Comentario.query.filter_by(id=cid, removido=False).first_or_404()
    if c.autor_id != current_user.id and c.post.autor_id != current_user.id and not pode_moderar(c.post):
        abort(403)
    c.removido = True
    db.session.commit()
    flash("Comentário removido.", "info")
    return redirect(destino_seguro(request.form.get("next"), "feed.index"))


@bp.route("/<int:pid>/compartilhar", methods=["POST"])
@login_required
def compartilhar(pid):
    original = Post.query.filter_by(id=pid, removido=False).first_or_404()
    if original.compartilhado_id:
        original = original.compartilhado or original
    texto = (request.form.get("texto") or "").strip()[:2000] or None
    db.session.add(Post(autor_id=current_user.id, tipo="geral", texto=texto, compartilhado_id=original.id))
    db.session.commit()
    flash("Publicação compartilhada no seu feed.", "success")
    return redirect(url_for("feed.index"))


@bp.route("/usuario/<int:uid>")
@login_required
def usuario(uid):
    u = db.session.get(User, uid)
    if not u or not u.ativo:
        abort(404)
    posts = _base().filter(Post.autor_id == uid, Post.grupo_id.is_(None)).order_by(Post.created_at.desc()).limit(30).all()
    perfis = PerfilProfissional.query.filter_by(user_id=uid, publico=True).all()
    curtidos = {r.post_id for r in Reacao.query.filter(Reacao.user_id == current_user.id, Reacao.post_id.in_([p.id for p in posts])).all()} if posts else set()
    return render_template("feed/usuario.html", u=u, posts=posts, perfis=perfis, curtidos=curtidos, tipos=TIPOS_POST)
