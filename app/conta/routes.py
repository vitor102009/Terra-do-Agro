import json

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user

from app.auth.routes import validar_senha
from app.extensions import db
from app.models import (
    Anuncio, Candidatura, ChamadoSuporte, Comentario, PerfilEmpresa, PerfilProfissional, PerfilTrabalhador, Post,
    PropriedadeMembro, Vaga, utcnow,
)
from app.models.mercado import TIPOS_EMPRESA
from app.models.sistema import TIPOS_CHAMADO
from app.models.user import PAPEIS_USUARIO
from app.utils.registro import auditar
from app.utils.uploads import UploadError, remover_upload, salvar_upload, tem_arquivo
from app.utils.validacao import Form, valores_de

bp = Blueprint("conta", __name__)


@bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    v, e = valores_de(current_user, ["nome", "telefone", "municipio", "uf", "bio"]), {}
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "papeis":
            novos = [p for p in request.form.getlist("papeis") if p in PAPEIS_USUARIO]
            for p in novos:
                current_user.add_role(p)
            db.session.commit()
            flash("Perfis de atuação atualizados.", "success")
            return redirect(url_for("conta.index"))
        form = Form(request.form)
        dados = {
            "nome": form.text("nome", required=True, max_len=120, min_len=3),
            "telefone": form.text("telefone", max_len=30),
            "municipio": form.text("municipio", max_len=120),
            "uf": form.uf("uf", required=False),
            "bio": form.text("bio", max_len=1000),
        }
        arq = request.files.get("foto")
        if form.ok and tem_arquivo(arq):
            try:
                nova = salvar_upload(arq, "imagem", "perfis")
                remover_upload(current_user.foto)
                current_user.foto = nova
            except UploadError as exc:
                form.error("foto", str(exc))
        if form.ok:
            for k, val in dados.items():
                setattr(current_user, k, val)
            db.session.commit()
            flash("Dados atualizados.", "success")
            return redirect(url_for("conta.index"))
        v, e = form.values, form.errors
    contagens = {
        "profissional": PerfilProfissional.query.filter_by(user_id=current_user.id).count(),
        "trabalhador": PerfilTrabalhador.query.filter_by(user_id=current_user.id).count(),
        "empresa": PerfilEmpresa.query.filter_by(user_id=current_user.id).count(),
        "propriedades": PropriedadeMembro.query.filter_by(user_id=current_user.id).count(),
    }
    return render_template("conta/index.html", v=v, e=e, papeis=PAPEIS_USUARIO, contagens=contagens)


@bp.route("/senha", methods=["GET", "POST"])
@login_required
def senha():
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        if not current_user.check_password(request.form.get("atual") or ""):
            form.error("atual", "Senha atual incorreta.")
        nova = validar_senha(form)
        if form.ok:
            current_user.set_password(nova)
            auditar("conta.senha_alterada", "Senha alterada pelo usuário", propriedade_id=None)
            db.session.commit()
            # Renova a sessão atual com a nova versão; as demais sessões deixam de valer.
            login_user(current_user._get_current_object())
            flash("Senha alterada. Sessões em outros aparelhos foram encerradas.", "success")
            return redirect(url_for("conta.index"))
        e = form.errors
    return render_template("conta/senha.html", e=e, v={})


CAMPOS_EMPRESA = ["nome_fantasia", "tipo", "descricao", "municipio", "uf", "regiao_atendida", "condicoes_entrega",
                  "canais_atendimento", "cnpj", "publico"]


@bp.route("/empresa", methods=["GET", "POST"])
@login_required
def empresa():
    emp = PerfilEmpresa.query.filter_by(user_id=current_user.id).first()
    v = valores_de(emp, CAMPOS_EMPRESA) if emp else {"municipio": current_user.municipio or "", "uf": current_user.uf or "", "publico": True}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = {
            "nome_fantasia": form.text("nome_fantasia", required=True, max_len=150),
            "tipo": form.choice("tipo", TIPOS_EMPRESA),
            "descricao": form.text("descricao", max_len=4000),
            "municipio": form.text("municipio", required=True, max_len=120),
            "uf": form.uf("uf"),
            "regiao_atendida": form.text("regiao_atendida", max_len=2000),
            "condicoes_entrega": form.text("condicoes_entrega", max_len=2000),
            "canais_atendimento": form.text("canais_atendimento", max_len=1000),
            "cnpj": form.text("cnpj", max_len=20),
            "publico": form.boolean("publico"),
        }
        if form.ok:
            novo = emp is None
            if novo:
                emp = PerfilEmpresa(user_id=current_user.id)
            for k, val in dados.items():
                setattr(emp, k, val)
            if novo:
                db.session.add(emp)
                current_user.add_role("empresa")
            db.session.commit()
            flash("Perfil da empresa salvo.", "success")
            return redirect(url_for("profissionais.empresa", eid=emp.id))
        v, e = form.values, form.errors
    return render_template("conta/empresa.html", v=v, e=e, emp=emp, tipos=TIPOS_EMPRESA)


@bp.route("/suporte", methods=["GET", "POST"])
@login_required
def suporte():
    v, e = {"tipo": request.args.get("tipo", "duvida")}, {}
    if request.method == "POST":
        form = Form(request.form)
        tipo = form.choice("tipo", TIPOS_CHAMADO)
        assunto = form.text("assunto", required=True, max_len=150)
        mensagem = form.text("mensagem", required=True, max_len=5000, min_len=10)
        if form.ok:
            db.session.add(ChamadoSuporte(user_id=current_user.id, tipo=tipo, assunto=assunto, mensagem=mensagem))
            db.session.commit()
            flash("Chamado aberto. A resposta aparecerá aqui e nas suas notificações.", "success")
            return redirect(url_for("conta.suporte"))
        v, e = form.values, form.errors
    chamados = ChamadoSuporte.query.filter_by(user_id=current_user.id).order_by(ChamadoSuporte.created_at.desc()).all()
    return render_template("conta/suporte.html", v=v, e=e, tipos=TIPOS_CHAMADO, chamados=chamados)


@bp.route("/meus-dados.json")
@login_required
def exportar_dados():
    """Exportação dos dados pessoais do titular (LGPD, art. 18). Inclui dados da conta e conteúdos publicados."""
    u = current_user
    d = lambda x: x.isoformat() if x else None  # noqa: E731
    dados = {
        "gerado_em": d(utcnow()),
        "conta": {"nome": u.nome, "email": u.email, "telefone": u.telefone, "municipio": u.municipio, "uf": u.uf, "bio": u.bio,
                  "criada_em": d(u.created_at), "ultimo_login": d(u.ultimo_login), "papeis": u.lista_papeis,
                  "email_verificado": u.email_verificado, "aceite_termos_em": d(u.aceite_termos_em)},
        "propriedades_com_acesso": [{"propriedade": mb.propriedade.nome, "papel": mb.papel} for mb in
                                    PropriedadeMembro.query.filter_by(user_id=u.id).all()],
        "perfis_profissionais": [{"categoria": p.categoria.nome, "titulo": p.titulo, "descricao": p.descricao,
                                  "registro": p.registro_numero, "registro_uf": p.registro_uf, "municipio": p.municipio, "uf": p.uf}
                                 for p in PerfilProfissional.query.filter_by(user_id=u.id).all()],
        "perfil_trabalhador": [{"titulo": t.titulo, "habilidades": t.habilidades, "experiencias": t.experiencias,
                                "telefone": t.telefone_contato, "compartilhar_contato": t.compartilhar_contato}
                               for t in PerfilTrabalhador.query.filter_by(user_id=u.id).all()],
        "perfil_empresa": [{"nome_fantasia": e.nome_fantasia, "cnpj": e.cnpj, "municipio": e.municipio}
                           for e in PerfilEmpresa.query.filter_by(user_id=u.id).all()],
        "anuncios": [{"titulo": a.titulo, "status": a.status, "criado_em": d(a.created_at)} for a in Anuncio.query.filter_by(user_id=u.id).all()],
        "vagas": [{"cargo": v.cargo, "status": v.status, "criada_em": d(v.created_at)} for v in Vaga.query.filter_by(user_id=u.id).all()],
        "candidaturas": [{"vaga": c.vaga.cargo, "status": c.status, "mensagem": c.mensagem} for c in Candidatura.query.filter_by(user_id=u.id).all()],
        "publicacoes": [{"texto": p.texto, "criada_em": d(p.created_at)} for p in Post.query.filter_by(autor_id=u.id).all()],
        "comentarios": [{"texto": c.texto, "criado_em": d(c.created_at)} for c in Comentario.query.filter_by(autor_id=u.id).all()],
        "observacao": "Dados operacionais das propriedades (rebanho, financeiro) podem ser exportados pelos relatórios em CSV.",
    }
    return Response(json.dumps(dados, ensure_ascii=False, indent=2), mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=meus-dados-terra-do-agro.json",
                             "Cache-Control": "no-store"})


@bp.route("/suporte/<int:cid>")
@login_required
def chamado(cid):
    ch = ChamadoSuporte.query.filter_by(id=cid, user_id=current_user.id).first()
    if not ch:
        abort(404)
    return render_template("conta/chamado.html", ch=ch, tipos=TIPOS_CHAMADO)
