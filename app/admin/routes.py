import csv
import io
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from app.extensions import db
from app.main.routes import ALVOS_MODELOS
from app.models import (
    Animal, Anuncio, AuditLog, Avaliacao, CategoriaFinanceira, CategoriaProfissional, ChamadoSuporte, Comentario, Configuracao,
    Cotacao, Denuncia, Grupo, Lancamento, Mensagem, PerfilProfissional, Post, Propriedade, PropriedadeMembro,
    SolicitacaoVerificacao, User, UserRole, Vaga, utcnow,
)
from app.models.mercado import GRUPOS_PROFISSIONAIS
from app.models.sistema import ALVOS_DENUNCIA, MOTIVOS_DENUNCIA, TIPOS_CHAMADO
from app.models.user import PAPEIS_USUARIO
from app.servicos.routes import atualizar_nota
from app.utils.acesso import admin_required
from app.utils.email import email_configurado
from app.utils.navegacao import pagina
from app.utils.registro import auditar, notificar
from app.utils.uploads import caminho_absoluto
from app.utils.validacao import Form, valores_de

bp = Blueprint("admin", __name__)


def registrar(acao, descricao, link=None):
    auditar(f"admin.{acao}", descricao, link, propriedade_id=None, administrativa=True)


def voltar(endpoint, **kw):
    ref = request.referrer
    if ref and ref.startswith(request.host_url) and "/admin" in ref:
        return redirect(ref)
    return redirect(url_for(endpoint, **kw))


@bp.route("/")
@admin_required
def index():
    semana = utcnow() - timedelta(days=7)
    ind = {
        "usuarios": User.query.count(), "usuarios_7d": User.query.filter(User.created_at >= semana).count(),
        "suspensos": User.query.filter_by(ativo=False).count(),
        "propriedades": Propriedade.query.filter_by(arquivada=False).count(), "animais": Animal.query.filter_by(status="ativo").count(),
        "anuncios": Anuncio.query.filter_by(status="ativo").count(), "vagas": Vaga.query.filter_by(status="aberta").count(),
        "perfis": PerfilProfissional.query.count(), "posts_7d": Post.query.filter(Post.created_at >= semana).count(),
        "mensagens_7d": Mensagem.query.filter(Mensagem.created_at >= semana).count(),
        "denuncias": Denuncia.query.filter_by(status="aberta").count(),
        "verificacoes": SolicitacaoVerificacao.query.filter_by(status="pendente").count(),
        "chamados": ChamadoSuporte.query.filter_by(status="aberto").count(),
    }
    por_papel = dict(db.session.query(UserRole.papel, db.func.count(UserRole.id)).group_by(UserRole.papel).all())
    acoes = AuditLog.query.filter_by(administrativa=True).order_by(AuditLog.created_at.desc()).limit(12).all()
    return render_template("admin/index.html", ind=ind, por_papel=por_papel, papeis=PAPEIS_USUARIO, acoes=acoes)


# ------------------------------------------------------------------ Usuários

@bp.route("/usuarios")
@admin_required
def usuarios():
    q = User.query
    termo = (request.args.get("q") or "").strip()
    if termo:
        q = q.filter(db.or_(User.nome.ilike(f"%{termo}%"), User.email.ilike(f"%{termo}%")))
    if request.args.get("situacao") == "suspensos":
        q = q.filter_by(ativo=False)
    elif request.args.get("situacao") == "admins":
        q = q.filter_by(is_admin=True)
    pag = q.order_by(User.created_at.desc()).paginate(page=pagina(), per_page=30, error_out=False)
    return render_template("admin/usuarios.html", pag=pag, args=request.args)


@bp.route("/usuarios/<int:uid>", methods=["GET", "POST"])
@admin_required
def usuario(uid):
    u = db.session.get(User, uid) or abort(404)
    if request.method == "POST":
        acao = request.form.get("acao")
        motivo = (request.form.get("motivo") or "").strip()[:255]
        if acao == "suspender":
            if u.id == current_user.id:
                flash("Você não pode suspender a própria conta.", "warning")
            elif not motivo:
                flash("Informe o motivo da suspensão.", "warning")
            else:
                u.ativo = False
                u.suspenso_motivo = motivo
                u.session_version += 1  # encerra as sessões abertas
                registrar("usuario_suspenso", f"Conta suspensa: {u.email}. Motivo: {motivo}", url_for("admin.usuario", uid=uid))
                flash("Conta suspensa.", "success")
        elif acao == "reativar":
            u.ativo = True
            u.suspenso_motivo = None
            registrar("usuario_reativado", f"Conta reativada: {u.email}", url_for("admin.usuario", uid=uid))
            flash("Conta reativada.", "success")
        elif acao in ("tornar_admin", "remover_admin"):
            if acao == "remover_admin" and User.query.filter_by(is_admin=True, ativo=True).count() <= 1:
                flash("A plataforma precisa de ao menos um administrador ativo.", "warning")
            elif acao == "remover_admin" and u.id == current_user.id:
                flash("Peça a outro administrador para remover seu acesso.", "warning")
            else:
                u.is_admin = acao == "tornar_admin"
                u.session_version += 1
                registrar("admin_alterado", f"{'Concedido' if u.is_admin else 'Removido'} acesso administrativo: {u.email}")
                flash("Permissão administrativa atualizada.", "success")
        elif acao == "confirmar_email":
            u.email_verificado = True
            registrar("email_confirmado", f"E-mail confirmado manualmente: {u.email}")
            flash("E-mail marcado como confirmado.", "success")
        db.session.commit()
        return redirect(url_for("admin.usuario", uid=uid))
    vinculos = PropriedadeMembro.query.filter_by(user_id=uid).all()
    perfis = PerfilProfissional.query.filter_by(user_id=uid).all()
    denuncias = Denuncia.query.filter_by(alvo_tipo="usuario", alvo_id=uid).order_by(Denuncia.created_at.desc()).all()
    anuncios = Anuncio.query.filter_by(user_id=uid).count()
    posts = Post.query.filter_by(autor_id=uid).count()
    return render_template("admin/usuario.html", u=u, vinculos=vinculos, perfis=perfis, denuncias=denuncias, anuncios=anuncios,
                           posts=posts, papeis=PAPEIS_USUARIO)


# ------------------------------------------------------------------ Verificações e perfis

@bp.route("/verificacoes")
@admin_required
def verificacoes():
    status = request.args.get("status", "pendente")
    q = SolicitacaoVerificacao.query
    if status in ("pendente", "aprovada", "recusada"):
        q = q.filter_by(status=status)
    itens = q.order_by(SolicitacaoVerificacao.created_at).all()
    return render_template("admin/verificacoes.html", itens=itens, status=status)


@bp.route("/verificacoes/<int:vid>", methods=["GET", "POST"])
@admin_required
def verificacao(vid):
    s = db.session.get(SolicitacaoVerificacao, vid) or abort(404)
    if request.method == "POST":
        if s.status != "pendente":
            abort(400)
        decisao = request.form.get("decisao")
        nota = (request.form.get("nota") or "").strip()[:2000] or None
        if decisao not in ("aprovada", "recusada"):
            abort(400)
        if decisao == "recusada" and not nota:
            flash("Explique o motivo da recusa para o profissional.", "warning")
            return redirect(url_for("admin.verificacao", vid=vid))
        s.status, s.nota_admin, s.analisado_por, s.analisado_em = decisao, nota, current_user.id, utcnow()
        p = s.perfil
        if decisao == "aprovada":
            p.verificado, p.verificado_em, p.verificado_por = True, utcnow(), current_user.id
        notificar(p.user_id, "verificacao", "Verificação profissional " + ("aprovada" if decisao == "aprovada" else "recusada"),
                  nota or p.titulo, url_for("profissionais.verificacao", perfil_id=p.id))
        registrar(f"verificacao_{decisao}", f"Verificação {decisao}: {p.user.nome} ({p.categoria.nome}, registro {p.registro_numero or '—'})")
        db.session.commit()
        flash(f"Solicitação {decisao}.", "success")
        return redirect(url_for("admin.verificacoes"))
    return render_template("admin/verificacao.html", s=s)


@bp.route("/verificacoes/<int:vid>/documento")
@admin_required
def documento_verificacao(vid):
    s = db.session.get(SolicitacaoVerificacao, vid) or abort(404)
    if not s.documento:
        abort(404)
    registrar("documento_consultado", f"Documento de verificação consultado (solicitação {vid})")
    db.session.commit()
    resp = send_file(caminho_absoluto(s.documento))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/perfis")
@admin_required
def perfis():
    q = PerfilProfissional.query.join(User, PerfilProfissional.user_id == User.id)
    if request.args.get("q"):
        termo = f"%{request.args['q'].strip()}%"
        q = q.filter(db.or_(User.nome.ilike(termo), PerfilProfissional.titulo.ilike(termo), PerfilProfissional.registro_numero.ilike(termo)))
    if request.args.get("verificado") == "1":
        q = q.filter(PerfilProfissional.verificado.is_(True))
    pag = q.order_by(PerfilProfissional.created_at.desc()).paginate(page=pagina(), per_page=30, error_out=False)
    return render_template("admin/perfis.html", pag=pag, args=request.args)


@bp.route("/perfis/<int:pid>/acao", methods=["POST"])
@admin_required
def perfil_acao(pid):
    p = db.session.get(PerfilProfissional, pid) or abort(404)
    acao = request.form.get("acao")
    if acao == "remover_verificacao":
        p.verificado, p.verificado_em = False, None
        notificar(p.user_id, "verificacao", "Selo de verificação removido", "Fale com o suporte para mais informações.")
    elif acao == "ocultar":
        p.publico = False
    elif acao == "publicar":
        p.publico = True
    else:
        abort(400)
    registrar(f"perfil_{acao}", f"Perfil profissional {p.id} ({p.user.nome}): {acao}")
    db.session.commit()
    flash("Perfil atualizado.", "success")
    return voltar("admin.perfis")


# ------------------------------------------------------------------ Categorias

@bp.route("/categorias", methods=["GET", "POST"])
@admin_required
def categorias():
    editar = db.session.get(CategoriaProfissional, request.args.get("editar", type=int) or 0)
    v = valores_de(editar, ["nome", "slug", "grupo", "descricao", "registro_label", "aviso_habilitacao", "ativa", "ordem"]) if editar else {"ativa": True, "ordem": 100}
    e = {}
    if request.method == "POST":
        if request.form.get("fin_toggle"):
            cat = db.session.get(CategoriaFinanceira, request.form.get("fin_toggle", type=int)) or abort(404)
            cat.custo_producao = not cat.custo_producao
            registrar("categoria_financeira", f"Categoria financeira '{cat.nome}': custo de produção = {cat.custo_producao}")
            db.session.commit()
            return redirect(url_for("admin.categorias"))
        form = Form(request.form)
        dados = {
            "nome": form.text("nome", required=True, max_len=100),
            "slug": (form.text("slug", max_len=60) or "").lower() or None,
            "grupo": form.choice("grupo", GRUPOS_PROFISSIONAIS),
            "descricao": form.text("descricao", max_len=2000),
            "registro_label": form.text("registro_label", max_len=40),
            "aviso_habilitacao": form.text("aviso_habilitacao", max_len=2000),
            "ativa": form.boolean("ativa"),
            "ordem": form.integer("ordem", min_value=0, max_value=10000) or 100,
        }
        if not dados["slug"] and dados["nome"]:
            dados["slug"] = re.sub(r"[^a-z0-9]+", "-", _ascii(dados["nome"]).lower()).strip("-")[:60]
        dup = CategoriaProfissional.query.filter(db.or_(CategoriaProfissional.nome == dados["nome"], CategoriaProfissional.slug == dados["slug"]))
        if editar:
            dup = dup.filter(CategoriaProfissional.id != editar.id)
        if dup.first():
            form.error("nome", "Já existe uma categoria com este nome ou identificador.")
        if form.ok:
            cat = editar or CategoriaProfissional()
            for k, val in dados.items():
                setattr(cat, k, val)
            db.session.add(cat)
            registrar("categoria_profissional", f"Categoria profissional {'editada' if editar else 'criada'}: {cat.nome}")
            db.session.commit()
            flash("Categoria salva.", "success")
            return redirect(url_for("admin.categorias"))
        v, e = form.values, form.errors
    cats = CategoriaProfissional.query.order_by(CategoriaProfissional.grupo, CategoriaProfissional.ordem, CategoriaProfissional.nome).all()
    uso = dict(db.session.query(PerfilProfissional.categoria_id, db.func.count()).group_by(PerfilProfissional.categoria_id).all())
    fin = CategoriaFinanceira.query.filter_by(propriedade_id=None).order_by(CategoriaFinanceira.tipo, CategoriaFinanceira.nome).all()
    return render_template("admin/categorias.html", cats=cats, uso=uso, grupos=GRUPOS_PROFISSIONAIS, v=v, e=e, editar=editar, fin=fin)


def _ascii(s):
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------ Moderação de conteúdo

@bp.route("/anuncios")
@admin_required
def anuncios():
    q = Anuncio.query
    if request.args.get("status"):
        q = q.filter_by(status=request.args["status"])
    if request.args.get("q"):
        q = q.filter(Anuncio.titulo.ilike(f"%{request.args['q'].strip()}%"))
    pag = q.order_by(Anuncio.created_at.desc()).paginate(page=pagina(), per_page=30, error_out=False)
    return render_template("admin/anuncios.html", pag=pag, args=request.args)


@bp.route("/conteudo/<tipo>/<int:oid>", methods=["POST"])
@admin_required
def moderar(tipo, oid):
    """Remove ou restaura conteúdo: anúncio, vaga, publicação, comentário, avaliação ou grupo."""
    acao = request.form.get("acao")
    motivo = (request.form.get("motivo") or "").strip()[:255] or "Violação das regras da plataforma"
    if acao not in ("remover", "restaurar"):
        abort(400)
    remover = acao == "remover"
    if tipo == "anuncio":
        o = db.session.get(Anuncio, oid) or abort(404)
        o.status = "removido" if remover else "pausado"
        autor, titulo = o.user_id, o.titulo
    elif tipo == "vaga":
        o = db.session.get(Vaga, oid) or abort(404)
        o.status = "removida" if remover else "encerrada"
        autor, titulo = o.user_id, o.cargo
    elif tipo == "post":
        o = db.session.get(Post, oid) or abort(404)
        o.removido, o.removido_motivo = remover, (motivo if remover else None)
        autor, titulo = o.autor_id, (o.texto or "publicação")[:60]
    elif tipo == "comentario":
        o = db.session.get(Comentario, oid) or abort(404)
        o.removido = remover
        autor, titulo = o.autor_id, o.texto[:60]
    elif tipo == "avaliacao":
        o = db.session.get(Avaliacao, oid) or abort(404)
        o.oculta = remover
        db.session.flush()
        atualizar_nota(o.perfil)
        autor, titulo = o.autor_id, f"avaliação de {o.perfil.user.nome}"
    elif tipo == "grupo":
        o = db.session.get(Grupo, oid) or abort(404)
        o.ativo = not remover
        autor, titulo = o.criado_por, o.nome
    else:
        abort(404)
    if autor and remover:
        notificar(autor, "moderacao", "Conteúdo removido pela moderação", f"{titulo} · Motivo: {motivo}")
    registrar(f"moderacao_{acao}", f"{tipo} {oid} ({titulo}) {'removido' if remover else 'restaurado'}. Motivo: {motivo if remover else '—'}")
    db.session.commit()
    flash("Conteúdo " + ("removido." if remover else "restaurado."), "success")
    return voltar("admin.index")


@bp.route("/denuncias")
@admin_required
def denuncias():
    status = request.args.get("status", "aberta")
    q = Denuncia.query
    if status in ("aberta", "procedente", "improcedente"):
        q = q.filter_by(status=status)
    itens = q.order_by(Denuncia.created_at.desc()).limit(200).all()
    return render_template("admin/denuncias.html", itens=itens, status=status, alvos=ALVOS_DENUNCIA, motivos=MOTIVOS_DENUNCIA)


def _alvo_info(d):
    modelo = ALVOS_MODELOS.get(d.alvo_tipo)
    o = db.session.get(modelo, d.alvo_id) if modelo else None
    if o is None:
        return {"existe": False}
    info = {"existe": True, "obj": o}
    if d.alvo_tipo == "anuncio":
        info.update(resumo=o.titulo, link=url_for("marketplace.detalhe", aid=o.id), autor=o.autor, removido=o.status == "removido")
    elif d.alvo_tipo == "post":
        info.update(resumo=o.texto or "(mídia)", link=url_for("feed.post", pid=o.id), autor=o.autor, removido=o.removido)
    elif d.alvo_tipo == "comentario":
        info.update(resumo=o.texto, link=url_for("feed.post", pid=o.post_id), autor=o.autor, removido=o.removido)
    elif d.alvo_tipo == "usuario":
        info.update(resumo=o.nome, link=url_for("admin.usuario", uid=o.id), autor=o, removido=not o.ativo)
    elif d.alvo_tipo == "grupo":
        info.update(resumo=o.nome, link=url_for("comunidade.grupo", gid=o.id), autor=None, removido=not o.ativo)
    elif d.alvo_tipo == "perfil":
        info.update(resumo=o.titulo, link=url_for("profissionais.perfil", perfil_id=o.id), autor=o.user, removido=not o.publico)
    elif d.alvo_tipo == "vaga":
        info.update(resumo=o.cargo, link=url_for("vagas.detalhe", vid=o.id), autor=o.autor, removido=o.status == "removida")
    elif d.alvo_tipo == "mensagem":
        # Apenas a mensagem denunciada é exibida ao moderador, não a conversa inteira.
        info.update(resumo=o.texto, link=None, autor=o.remetente, removido=False)
    elif d.alvo_tipo == "avaliacao":
        info.update(resumo=f"{o.nota} estrela(s): {o.comentario or ''}", link=url_for("profissionais.perfil", perfil_id=o.perfil_id), autor=o.autor, removido=o.oculta)
    return info


@bp.route("/denuncias/<int:did>", methods=["GET", "POST"])
@admin_required
def denuncia(did):
    d = db.session.get(Denuncia, did) or abort(404)
    if request.method == "POST":
        decisao = request.form.get("decisao")
        if decisao not in ("procedente", "improcedente"):
            abort(400)
        d.status, d.resolvido_por = decisao, current_user.id
        d.nota_resolucao = (request.form.get("nota") or "").strip()[:2000] or None
        registrar("denuncia_resolvida", f"Denúncia {did} ({d.alvo_tipo} {d.alvo_id}) considerada {decisao}")
        db.session.commit()
        flash("Denúncia resolvida. Se necessário, use as ações de moderação sobre o conteúdo.", "success")
        return redirect(url_for("admin.denuncias"))
    return render_template("admin/denuncia.html", d=d, info=_alvo_info(d), alvos=ALVOS_DENUNCIA, motivos=MOTIVOS_DENUNCIA)


@bp.route("/conteudos")
@admin_required
def conteudos():
    aba = request.args.get("aba", "posts")
    ctx = {"aba": aba}
    if aba == "vagas":
        ctx["itens"] = Vaga.query.order_by(Vaga.created_at.desc()).limit(100).all()
    elif aba == "grupos":
        ctx["itens"] = Grupo.query.order_by(Grupo.nome).all()
    elif aba == "avaliacoes":
        ctx["itens"] = Avaliacao.query.order_by(Avaliacao.created_at.desc()).limit(100).all()
    else:
        ctx["itens"] = Post.query.order_by(Post.created_at.desc()).limit(100).all()
    return render_template("admin/conteudos.html", **ctx)


@bp.route("/propriedades")
@admin_required
def propriedades():
    """Visão administrativa sem dados financeiros nem localização detalhada."""
    q = Propriedade.query
    if request.args.get("q"):
        termo = f"%{request.args['q'].strip()}%"
        q = q.filter(db.or_(Propriedade.nome.ilike(termo), Propriedade.municipio.ilike(termo)))
    pag = q.order_by(Propriedade.created_at.desc()).paginate(page=pagina(), per_page=30, error_out=False)
    animais = dict(db.session.query(Animal.propriedade_id, db.func.count()).filter(Animal.status == "ativo").group_by(Animal.propriedade_id).all())
    membros = dict(db.session.query(PropriedadeMembro.propriedade_id, db.func.count()).group_by(PropriedadeMembro.propriedade_id).all())
    return render_template("admin/propriedades.html", pag=pag, animais=animais, membros=membros, args=request.args)


@bp.route("/propriedades/<int:pid>/restaurar", methods=["POST"])
@admin_required
def restaurar_propriedade(pid):
    p = db.session.get(Propriedade, pid) or abort(404)
    p.arquivada = False
    registrar("propriedade_restaurada", f"Propriedade {p.id} ({p.nome}) restaurada a pedido do responsável")
    db.session.commit()
    flash("Propriedade restaurada.", "success")
    return redirect(url_for("admin.propriedades"))


# ------------------------------------------------------------------ Suporte e comunicação

@bp.route("/suporte")
@admin_required
def suporte():
    status = request.args.get("status", "aberto")
    q = ChamadoSuporte.query
    if status in ("aberto", "respondido", "encerrado"):
        q = q.filter_by(status=status)
    itens = q.order_by(ChamadoSuporte.created_at).all()
    return render_template("admin/suporte.html", itens=itens, status=status, tipos=TIPOS_CHAMADO)


@bp.route("/suporte/<int:cid>", methods=["GET", "POST"])
@admin_required
def chamado(cid):
    ch = db.session.get(ChamadoSuporte, cid) or abort(404)
    if request.method == "POST":
        resposta = (request.form.get("resposta") or "").strip()
        encerrar = request.form.get("encerrar") == "1"
        if not resposta and not encerrar:
            flash("Escreva a resposta.", "warning")
            return redirect(url_for("admin.chamado", cid=cid))
        if resposta:
            ch.resposta, ch.respondido_por, ch.respondido_em = resposta[:5000], current_user.id, utcnow()
            ch.status = "respondido"
            notificar(ch.user_id, "suporte", f"Resposta do suporte: {ch.assunto}", resposta[:200], url_for("conta.chamado", cid=ch.id))
        if encerrar:
            ch.status = "encerrado"
        registrar("suporte", f"Chamado {cid} ({TIPOS_CHAMADO.get(ch.tipo)}) {'respondido' if resposta else ''}{' e encerrado' if encerrar else ''}")
        db.session.commit()
        flash("Chamado atualizado.", "success")
        return redirect(url_for("admin.suporte"))
    return render_template("admin/chamado.html", ch=ch, tipos=TIPOS_CHAMADO)


@bp.route("/comunicados", methods=["GET", "POST"])
@admin_required
def comunicados():
    if request.method == "POST":
        form = Form(request.form)
        titulo = form.text("titulo", required=True, max_len=150)
        mensagem = form.text("mensagem", required=True, max_len=500)
        papel = form.choice("papel", PAPEIS_USUARIO, required=False)
        if form.ok:
            q = User.query.filter_by(ativo=True)
            if papel:
                q = q.join(User.papeis).filter_by(papel=papel)
            total = 0
            for u in q.all():
                notificar(u.id, "admin", titulo, mensagem, url_for("notificacoes.index"))
                total += 1
            registrar("comunicado", f"Comunicado enviado a {total} usuário(s): {titulo}")
            db.session.commit()
            flash(f"Comunicado enviado a {total} usuário(s) como notificação interna.", "success")
            return redirect(url_for("admin.comunicados"))
        flash("Preencha título e mensagem.", "danger")
    return render_template("admin/comunicados.html", papeis=PAPEIS_USUARIO, v={}, e={})


# ------------------------------------------------------------------ Cotações

@bp.route("/cotacoes", methods=["GET", "POST"])
@admin_required
def cotacoes():
    v, e = {"data_referencia": date.today().isoformat()}, {}
    if request.method == "POST":
        if request.form.get("remover"):
            c = db.session.get(Cotacao, request.form.get("remover", type=int)) or abort(404)
            db.session.delete(c)
            registrar("cotacao_removida", f"Cotação removida: {c.produto} {c.praca} {c.data_referencia}")
            db.session.commit()
            flash("Cotação removida.", "info")
            return redirect(url_for("admin.cotacoes"))
        arq = request.files.get("csv")
        if arq and arq.filename:
            return _importar_cotacoes(arq)
        form = Form(request.form)
        dados = {
            "produto": form.text("produto", required=True, max_len=80),
            "praca": form.text("praca", required=True, max_len=120),
            "uf": form.uf("uf", required=False),
            "valor": form.positive("valor"),
            "unidade": form.text("unidade", required=True, max_len=40),
            "fonte": form.text("fonte", required=True, max_len=150),
            "fonte_url": form.text("fonte_url", max_len=255),
            "data_referencia": form.date("data_referencia", required=True, not_future=True),
        }
        if dados["fonte_url"] and not dados["fonte_url"].startswith(("http://", "https://")):
            form.error("fonte_url", "Informe um endereço começando com http:// ou https://.")
        if form.ok:
            db.session.add(Cotacao(registrado_por=current_user.id, **dados))
            registrar("cotacao", f"Cotação registrada: {dados['produto']} {dados['praca']} R$ {dados['valor']} ({dados['fonte']}, {dados['data_referencia']})")
            db.session.commit()
            flash("Cotação registrada.", "success")
            return redirect(url_for("admin.cotacoes"))
        v, e = form.values, form.errors
    itens = Cotacao.query.order_by(Cotacao.data_referencia.desc(), Cotacao.id.desc()).limit(200).all()
    return render_template("admin/cotacoes.html", itens=itens, v=v, e=e)


def _importar_cotacoes(arq):
    """CSV (;) com colunas: produto;praca;uf;valor;unidade;fonte;fonte_url;data (AAAA-MM-DD ou DD/MM/AAAA)."""
    try:
        texto = arq.stream.read(2_000_000).decode("utf-8-sig")
    except UnicodeDecodeError:
        flash("O arquivo deve estar em UTF-8.", "danger")
        return redirect(url_for("admin.cotacoes"))
    linhas = list(csv.reader(io.StringIO(texto), delimiter=";"))
    if linhas and linhas[0] and linhas[0][0].strip().lower() == "produto":
        linhas = linhas[1:]
    ok, erros = 0, []
    for n, l in enumerate(linhas, start=1):
        if not any(c.strip() for c in l):
            continue
        try:
            produto, praca, uf, valor, unidade, fonte, fonte_url, data_txt = [c.strip() for c in (l + [""] * 8)[:8]]
            valor_dec = Decimal(valor.replace(".", "").replace(",", ".")) if "," in valor else Decimal(valor)
            data_ref = datetime.strptime(data_txt, "%d/%m/%Y").date() if "/" in data_txt else date.fromisoformat(data_txt)
            if not (produto and praca and unidade and fonte) or valor_dec <= 0 or data_ref > date.today():
                raise ValueError("campos obrigatórios ausentes ou inválidos")
            db.session.add(Cotacao(produto=produto[:80], praca=praca[:120], uf=uf.upper()[:2] or None, valor=valor_dec, unidade=unidade[:40],
                                   fonte=fonte[:150], fonte_url=fonte_url[:255] or None, data_referencia=data_ref, registrado_por=current_user.id))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            erros.append(f"linha {n}: {exc}")
    if erros:
        db.session.rollback()
        flash("Importação cancelada. Nenhuma linha foi gravada. " + "; ".join(erros[:5]), "danger")
    else:
        registrar("cotacao_importada", f"{ok} cotação(ões) importadas por CSV")
        db.session.commit()
        flash(f"{ok} cotação(ões) importadas.", "success")
    return redirect(url_for("admin.cotacoes"))


# ------------------------------------------------------------------ Configurações e auditoria

CONFIGS = {"aviso_global": "Aviso exibido no topo do painel para todos os usuários (deixe vazio para ocultar)"}


@bp.route("/configuracoes", methods=["GET", "POST"])
@admin_required
def configuracoes():
    if request.method == "POST":
        for chave in CONFIGS:
            valor = (request.form.get(chave) or "").strip()[:1000]
            c = db.session.get(Configuracao, chave) or Configuracao(chave=chave)
            c.valor = valor
            db.session.add(c)
        registrar("configuracoes", "Configurações da plataforma atualizadas")
        db.session.commit()
        flash("Configurações salvas.", "success")
        return redirect(url_for("admin.configuracoes"))
    from flask import current_app

    valores = {k: (db.session.get(Configuracao, k).valor if db.session.get(Configuracao, k) else "") for k in CONFIGS}
    ambiente = {
        "Modo": current_app.config.get("ENV_MODE"),
        "Envio de e-mail (SMTP)": "configurado" if email_configurado() else "não configurado (mensagens gravadas em instance/mail_outbox em desenvolvimento)",
        "Previsão do tempo (Open-Meteo)": "ativada" if current_app.config.get("WEATHER_ENABLED") else "desativada",
        "Cookies seguros (HTTPS)": "sim" if current_app.config.get("SESSION_COOKIE_SECURE") else "não",
        "Lançamentos financeiros registrados": Lancamento.query.count(),
    }
    return render_template("admin/configuracoes.html", configs=CONFIGS, valores=valores, ambiente=ambiente)


@bp.route("/auditoria")
@admin_required
def auditoria():
    q = AuditLog.query
    if request.args.get("tipo") != "todas":
        q = q.filter_by(administrativa=True)
    if request.args.get("q"):
        q = q.filter(AuditLog.descricao.ilike(f"%{request.args['q'].strip()}%"))
    pag = q.order_by(AuditLog.created_at.desc()).paginate(page=pagina(), per_page=50, error_out=False)
    return render_template("admin/auditoria.html", pag=pag, args=request.args)
