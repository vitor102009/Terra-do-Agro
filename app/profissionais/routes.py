from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    Avaliacao, CategoriaProfissional, PerfilEmpresa, PerfilProfissional, ServicoProfissional, SolicitacaoVerificacao, User,
)
from app.models.mercado import GRUPOS_PROFISSIONAIS, MODALIDADES, TIPOS_EMPRESA
from app.utils.navegacao import pagina
from app.utils.registro import auditar
from app.utils.uploads import UploadError, salvar_upload, tem_arquivo
from app.utils.validacao import Form, valores_de

bp = Blueprint("profissionais", __name__)


def categorias_ativas():
    return CategoriaProfissional.query.filter_by(ativa=True).order_by(CategoriaProfissional.ordem, CategoriaProfissional.nome).all()


@bp.route("/")
@login_required
def index():
    a = request.args
    q = (PerfilProfissional.query.join(User, PerfilProfissional.user_id == User.id)
         .join(CategoriaProfissional, PerfilProfissional.categoria_id == CategoriaProfissional.id)
         .filter(PerfilProfissional.publico.is_(True), User.ativo.is_(True), CategoriaProfissional.ativa.is_(True)))
    if a.get("grupo") in GRUPOS_PROFISSIONAIS:
        q = q.filter(CategoriaProfissional.grupo == a["grupo"])
    if a.get("categoria", type=int):
        q = q.filter(PerfilProfissional.categoria_id == a.get("categoria", type=int))
    if a.get("q"):
        termo = f"%{a['q'].strip()}%"
        q = q.filter(db.or_(PerfilProfissional.titulo.ilike(termo), PerfilProfissional.especialidades.ilike(termo),
                            PerfilProfissional.descricao.ilike(termo), User.nome.ilike(termo)))
    if a.get("uf"):
        q = q.filter(PerfilProfissional.uf == a["uf"].upper()[:2])
    if a.get("municipio"):
        termo = f"%{a['municipio'].strip()}%"
        q = q.filter(db.or_(PerfilProfissional.municipio.ilike(termo), PerfilProfissional.regioes_atendidas.ilike(termo)))
    if a.get("modalidade") in MODALIDADES:
        q = q.filter(PerfilProfissional.modalidades.ilike(f"%{a['modalidade']}%"))
    if a.get("disponivel") == "1":
        q = q.filter(PerfilProfissional.disponivel.is_(True))
    if a.get("verificado") == "1":
        q = q.filter(PerfilProfissional.verificado.is_(True))
    if a.get("preco_max"):
        try:
            q = q.filter(PerfilProfissional.preco_minimo.isnot(None), PerfilProfissional.preco_minimo <= float(a["preco_max"].replace(",", ".")))
        except ValueError:
            pass
    if a.get("nota_min", type=int):
        q = q.filter(PerfilProfissional.nota_media >= a.get("nota_min", type=int))
    ordem = {"nota": PerfilProfissional.nota_media.desc().nulls_last(), "recentes": PerfilProfissional.created_at.desc()}.get(
        a.get("ordem"), PerfilProfissional.verificado.desc())
    pag = q.order_by(ordem, PerfilProfissional.total_avaliacoes.desc(), PerfilProfissional.id).paginate(page=pagina(), per_page=18, error_out=False)
    return render_template("profissionais/index.html", pag=pag, categorias=categorias_ativas(), grupos=GRUPOS_PROFISSIONAIS,
                           modalidades=MODALIDADES, args=a)


@bp.route("/perfil/<int:perfil_id>")
@login_required
def perfil(perfil_id):
    p = db.session.get(PerfilProfissional, perfil_id)
    if not p or not p.user.ativo or (not p.publico and p.user_id != current_user.id and not current_user.is_admin):
        abort(404)
    avaliacoes = (Avaliacao.query.filter_by(perfil_id=p.id, oculta=False).order_by(Avaliacao.created_at.desc()).limit(30).all())
    outros = PerfilProfissional.query.filter(PerfilProfissional.user_id == p.user_id, PerfilProfissional.id != p.id,
                                             PerfilProfissional.publico.is_(True)).all()
    return render_template("profissionais/perfil.html", p=p, avaliacoes=avaliacoes, outros=outros, modalidades=MODALIDADES)


@bp.route("/meus-perfis")
@login_required
def meus():
    perfis = PerfilProfissional.query.filter_by(user_id=current_user.id).all()
    verificacoes = {v.perfil_id: v for v in SolicitacaoVerificacao.query.join(PerfilProfissional)
                    .filter(PerfilProfissional.user_id == current_user.id).order_by(SolicitacaoVerificacao.created_at).all()}
    return render_template("profissionais/meus.html", perfis=perfis, verificacoes=verificacoes)


CAMPOS = ["categoria_id", "titulo", "descricao", "especialidades", "experiencia", "qualificacoes", "registro_numero", "registro_uf",
          "municipio", "uf", "regioes_atendidas", "raio_km", "disponibilidade", "disponivel", "preco_info", "preco_minimo",
          "equipamentos", "portfolio", "publico"]


@bp.route("/meus-perfis/editar", methods=["GET", "POST"])
@login_required
def editar_perfil():
    perfil_id = request.args.get("id", type=int)
    p = None
    if perfil_id:
        p = PerfilProfissional.query.filter_by(id=perfil_id, user_id=current_user.id).first_or_404()
    cats = categorias_ativas()
    if p:
        v = valores_de(p, CAMPOS)
        v["modalidades"] = p.lista_modalidades
    else:
        v = {"municipio": current_user.municipio or "", "uf": current_user.uf or "", "disponivel": True, "publico": True,
             "modalidades": ["presencial"], "categoria_id": request.args.get("categoria", "")}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = {
            "categoria_id": form.id_in("categoria_id", {c.id for c in cats} | ({p.categoria_id} if p else set()), required=True),
            "titulo": form.text("titulo", required=True, max_len=150),
            "descricao": form.text("descricao", required=True, max_len=5000, min_len=30),
            "especialidades": form.text("especialidades", max_len=1000),
            "experiencia": form.text("experiencia", max_len=5000),
            "qualificacoes": form.text("qualificacoes", max_len=3000),
            "registro_numero": form.text("registro_numero", max_len=40),
            "registro_uf": form.uf("registro_uf", required=False),
            "municipio": form.text("municipio", required=True, max_len=120),
            "uf": form.uf("uf"),
            "regioes_atendidas": form.text("regioes_atendidas", max_len=2000),
            "raio_km": form.integer("raio_km", min_value=0, max_value=5000),
            "disponibilidade": form.text("disponibilidade", max_len=150),
            "disponivel": form.boolean("disponivel"),
            "preco_info": form.text("preco_info", max_len=200),
            "preco_minimo": form.decimal("preco_minimo", min_value=0),
            "equipamentos": form.text("equipamentos", max_len=3000),
            "portfolio": form.text("portfolio", max_len=5000),
            "publico": form.boolean("publico"),
        }
        dados["modalidades"] = ",".join(form.multi("modalidades", MODALIDADES))
        if not dados["modalidades"]:
            form.error("modalidades", "Escolha ao menos uma modalidade de atendimento.")
        cat = db.session.get(CategoriaProfissional, dados["categoria_id"]) if dados["categoria_id"] else None
        if cat and cat.registro_label and dados["registro_numero"] and not dados["registro_uf"]:
            form.error("registro_uf", "Informe a UF do registro profissional.")
        dup = PerfilProfissional.query.filter_by(user_id=current_user.id, categoria_id=dados["categoria_id"]).first()
        if dup and (not p or dup.id != p.id):
            form.error("categoria_id", "Você já tem um perfil nesta categoria. Edite o perfil existente.")
        if form.ok:
            novo = p is None
            if novo:
                p = PerfilProfissional(user_id=current_user.id)
            mudou_registro = not novo and (p.registro_numero != dados["registro_numero"] or p.categoria_id != dados["categoria_id"])
            for k, val in dados.items():
                setattr(p, k, val)
            if novo:
                db.session.add(p)
                current_user.add_role("profissional")
            if mudou_registro and p.verificado:
                # Alterar o registro ou a profissão exige nova verificação.
                p.verificado = False
                p.verificado_em = None
                flash("Como o registro ou a categoria mudou, a verificação precisará ser feita novamente.", "warning")
            db.session.commit()
            flash("Perfil profissional salvo.", "success")
            return redirect(url_for("profissionais.servicos", perfil_id=p.id) if request.form.get("ir_servicos") else url_for("profissionais.perfil", perfil_id=p.id))
        v, e = form.values, form.errors
    return render_template("profissionais/form.html", v=v, e=e, p=p, cats=cats, modalidades=MODALIDADES, grupos=GRUPOS_PROFISSIONAIS)


@bp.route("/meus-perfis/<int:perfil_id>/servicos", methods=["GET", "POST"])
@login_required
def servicos(perfil_id):
    p = PerfilProfissional.query.filter_by(id=perfil_id, user_id=current_user.id).first_or_404()
    v, e = {}, {}
    if request.method == "POST":
        if request.form.get("remover"):
            s = ServicoProfissional.query.filter_by(id=request.form.get("remover", type=int), perfil_id=p.id).first_or_404()
            db.session.delete(s)
            db.session.commit()
            flash("Serviço removido.", "info")
            return redirect(url_for("profissionais.servicos", perfil_id=p.id))
        form = Form(request.form)
        nome = form.text("nome", required=True, max_len=150)
        descricao = form.text("descricao", max_len=2000)
        preco = form.text("preco_info", max_len=120)
        if form.ok:
            db.session.add(ServicoProfissional(perfil_id=p.id, nome=nome, descricao=descricao, preco_info=preco))
            db.session.commit()
            flash("Serviço adicionado.", "success")
            return redirect(url_for("profissionais.servicos", perfil_id=p.id))
        v, e = form.values, form.errors
    return render_template("profissionais/servicos.html", p=p, v=v, e=e)


@bp.route("/meus-perfis/<int:perfil_id>/verificacao", methods=["GET", "POST"])
@login_required
def verificacao(perfil_id):
    p = PerfilProfissional.query.filter_by(id=perfil_id, user_id=current_user.id).first_or_404()
    pendente = SolicitacaoVerificacao.query.filter_by(perfil_id=p.id, status="pendente").first()
    historico = SolicitacaoVerificacao.query.filter_by(perfil_id=p.id).order_by(SolicitacaoVerificacao.created_at.desc()).all()
    v, e = {}, {}
    if request.method == "POST":
        if pendente or p.verificado:
            flash("Já existe uma solicitação em análise ou o perfil já está verificado.", "info")
            return redirect(url_for("profissionais.verificacao", perfil_id=p.id))
        form = Form(request.form)
        msg = form.text("mensagem", max_len=2000)
        arq = request.files.get("documento")
        if not p.registro_numero and p.categoria.registro_label:
            form.error("_geral", f"Informe o número do {p.categoria.registro_label} no perfil antes de solicitar a verificação.")
        if not tem_arquivo(arq):
            form.error("documento", "Envie um documento comprobatório (registro profissional, diploma ou certificado).")
        doc = None
        if form.ok:
            try:
                doc = salvar_upload(arq, "documento", "verificacoes", privado=True)
            except UploadError as exc:
                form.error("documento", str(exc))
        if form.ok:
            db.session.add(SolicitacaoVerificacao(perfil_id=p.id, documento=doc, mensagem=msg))
            auditar("perfil.verificacao_solicitada", f"Verificação solicitada para o perfil {p.titulo}", propriedade_id=None)
            db.session.commit()
            flash("Solicitação enviada. A equipe vai conferir os dados antes de marcar o perfil como verificado.", "success")
            return redirect(url_for("profissionais.meus"))
        v, e = form.values, form.errors
    return render_template("profissionais/verificacao.html", p=p, pendente=pendente, historico=historico, v=v, e=e)


# ------------------------------------------------------------------ Empresas e lojas

@bp.route("/empresas")
@login_required
def empresas():
    a = request.args
    q = PerfilEmpresa.query.join(User).filter(PerfilEmpresa.publico.is_(True), User.ativo.is_(True))
    if a.get("tipo") in TIPOS_EMPRESA:
        q = q.filter(PerfilEmpresa.tipo == a["tipo"])
    if a.get("uf"):
        q = q.filter(PerfilEmpresa.uf == a["uf"].upper()[:2])
    if a.get("q"):
        termo = f"%{a['q'].strip()}%"
        q = q.filter(db.or_(PerfilEmpresa.nome_fantasia.ilike(termo), PerfilEmpresa.descricao.ilike(termo),
                            PerfilEmpresa.regiao_atendida.ilike(termo), PerfilEmpresa.municipio.ilike(termo)))
    pag = q.order_by(PerfilEmpresa.nome_fantasia).paginate(page=pagina(), per_page=18, error_out=False)
    return render_template("profissionais/empresas.html", pag=pag, tipos=TIPOS_EMPRESA, args=a)


@bp.route("/empresas/<int:eid>")
@login_required
def empresa(eid):
    from app.models import Anuncio

    emp = db.session.get(PerfilEmpresa, eid)
    if not emp or not emp.user.ativo or (not emp.publico and emp.user_id != current_user.id):
        abort(404)
    anuncios = Anuncio.query.filter_by(user_id=emp.user_id, status="ativo").order_by(Anuncio.created_at.desc()).limit(12).all()
    return render_template("profissionais/empresa.html", emp=emp, tipos=TIPOS_EMPRESA, anuncios=anuncios)
