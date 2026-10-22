from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import ATIVIDADES, PAPEIS_PROPRIEDADE, Propriedade, PropriedadeMembro, User
from app.utils.acesso import membro_de
from app.utils.navegacao import destino_seguro
from app.utils.registro import auditar, notificar
from app.utils.validacao import Form, valores_de

bp = Blueprint("propriedades", __name__)

CAMPOS = ["nome", "municipio", "uf", "area_total_ha", "area_pastagem_ha", "area_agricultura_ha", "atividade",
          "tipo_producao", "qtd_animais_aprox", "informacoes", "endereco", "latitude", "longitude"]


def _ler(form):
    dados = {
        "nome": form.text("nome", required=True, max_len=150),
        "municipio": form.text("municipio", required=True, max_len=120),
        "uf": form.uf("uf"),
        "area_total_ha": form.decimal("area_total_ha", min_value=0, max_value=10_000_000),
        "area_pastagem_ha": form.decimal("area_pastagem_ha", min_value=0, max_value=10_000_000),
        "area_agricultura_ha": form.decimal("area_agricultura_ha", min_value=0, max_value=10_000_000),
        "atividade": form.choice("atividade", ATIVIDADES),
        "tipo_producao": form.text("tipo_producao", max_len=150),
        "qtd_animais_aprox": form.integer("qtd_animais_aprox", min_value=0, max_value=10_000_000),
        "informacoes": form.text("informacoes", max_len=4000),
        "endereco": form.text("endereco", max_len=255),
        "latitude": form.decimal("latitude", min_value=-90, max_value=90),
        "longitude": form.decimal("longitude", min_value=-180, max_value=180),
    }
    total = dados["area_total_ha"]
    partes = (dados["area_pastagem_ha"] or 0) + (dados["area_agricultura_ha"] or 0)
    if total is not None and partes > total:
        form.error("area_pastagem_ha", "A soma das áreas de pastagem e agricultura não pode superar a área total.")
    for c in ("latitude", "longitude"):
        if dados[c] is not None:
            dados[c] = float(dados[c])
    return dados


def _membro_ou_404(pid, perm=None):
    mb = membro_de(current_user.id, pid)
    if not mb or mb.propriedade.arquivada:
        abort(404)
    if perm and perm not in mb.perms:
        abort(403)
    return mb


@bp.route("/")
@login_required
def index():
    membros = (PropriedadeMembro.query.join(Propriedade).filter(PropriedadeMembro.user_id == current_user.id)
               .order_by(Propriedade.arquivada, Propriedade.nome).all())
    return render_template("propriedades/index.html", membros=membros)


@bp.route("/nova", methods=["GET", "POST"])
@login_required
def nova():
    v, e = {"atividade": "pecuaria_corte", "uf": current_user.uf or "", "municipio": current_user.municipio or ""}, {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form)
        if form.ok:
            prop = Propriedade(owner_id=current_user.id, **dados)
            db.session.add(prop)
            db.session.flush()
            db.session.add(PropriedadeMembro(propriedade_id=prop.id, user_id=current_user.id, papel="proprietario",
                                             adicionado_por=current_user.id))
            current_user.add_role("produtor")
            current_user.propriedade_ativa_id = prop.id
            auditar("propriedade.criada", f"Propriedade {prop.nome} cadastrada", url_for("propriedades.detalhe", pid=prop.id),
                    propriedade_id=prop.id)
            db.session.commit()
            flash(f"Propriedade {prop.nome} cadastrada e selecionada como ativa.", "success")
            return redirect(destino_seguro(request.args.get("next")))
        v, e = form.values, form.errors
    return render_template("propriedades/form.html", v=v, e=e, atividades=ATIVIDADES, prop=None)


@bp.route("/<int:pid>")
@login_required
def detalhe(pid):
    mb = _membro_ou_404(pid)
    return render_template("propriedades/detalhe.html", prop=mb.propriedade, mb=mb, papeis=PAPEIS_PROPRIEDADE)


@bp.route("/<int:pid>/editar", methods=["GET", "POST"])
@login_required
def editar(pid):
    mb = _membro_ou_404(pid, "membros")
    prop = mb.propriedade
    v, e = valores_de(prop, CAMPOS), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form)
        if form.ok:
            for k, val in dados.items():
                setattr(prop, k, val)
            auditar("propriedade.editada", f"Dados da propriedade {prop.nome} atualizados",
                    url_for("propriedades.detalhe", pid=prop.id), propriedade_id=prop.id)
            db.session.commit()
            flash("Propriedade atualizada.", "success")
            return redirect(url_for("propriedades.detalhe", pid=prop.id))
        v, e = form.values, form.errors
    return render_template("propriedades/form.html", v=v, e=e, atividades=ATIVIDADES, prop=prop)


@bp.route("/<int:pid>/ativar", methods=["POST"])
@login_required
def ativar(pid):
    mb = _membro_ou_404(pid)
    current_user.propriedade_ativa_id = mb.propriedade_id
    db.session.commit()
    flash(f"Propriedade ativa: {mb.propriedade.nome}.", "info")
    destino = destino_seguro(request.form.get("next"))
    # Ao trocar de propriedade, páginas de registros específicos da anterior deixariam de existir.
    if any(destino.startswith(p) for p in ("/rebanho/", "/lotes/", "/insumos/", "/financeiro/", "/pesagens/", "/sanidade/", "/reproducao/")):
        destino = url_for("main.dashboard")
    return redirect(destino)


@bp.route("/<int:pid>/arquivar", methods=["POST"])
@login_required
def arquivar(pid):
    mb = _membro_ou_404(pid, "excluir")
    prop = mb.propriedade
    prop.arquivada = True
    auditar("propriedade.arquivada", f"Propriedade {prop.nome} arquivada", propriedade_id=prop.id)
    db.session.commit()
    flash("Propriedade arquivada. Os registros foram preservados e podem ser recuperados pelo suporte.", "info")
    return redirect(url_for("propriedades.index"))


@bp.route("/<int:pid>/membros", methods=["GET", "POST"])
@login_required
def membros(pid):
    mb = _membro_ou_404(pid, "membros")
    prop = mb.propriedade
    v, e = {"papel": "operador"}, {}
    if request.method == "POST":
        acao = request.form.get("acao")
        if acao == "adicionar":
            form = Form(request.form)
            email = form.email("email")
            papel = form.choice("papel", {k: v for k, v in PAPEIS_PROPRIEDADE.items() if k != "proprietario"})
            alvo = User.query.filter_by(email=email).first() if email else None
            if email and not alvo:
                form.error("email", "Não encontramos um usuário com este e-mail. A pessoa precisa criar uma conta antes.")
            elif alvo and membro_de(alvo.id, prop.id):
                form.error("email", "Esta pessoa já tem acesso à propriedade.")
            if form.ok:
                db.session.add(PropriedadeMembro(propriedade_id=prop.id, user_id=alvo.id, papel=papel, adicionado_por=current_user.id))
                notificar(alvo.id, "acesso", f"Você recebeu acesso à propriedade {prop.nome}",
                          f"Permissão: {PAPEIS_PROPRIEDADE[papel]['label']}", url_for("propriedades.index"))
                auditar("propriedade.membro_adicionado", f"{alvo.nome} adicionado como {PAPEIS_PROPRIEDADE[papel]['label']}",
                        url_for("propriedades.membros", pid=prop.id), propriedade_id=prop.id)
                db.session.commit()
                flash(f"{alvo.nome} agora tem acesso à propriedade.", "success")
                return redirect(url_for("propriedades.membros", pid=prop.id))
            v, e = form.values, form.errors
        elif acao in ("alterar", "remover"):
            alvo_mb = PropriedadeMembro.query.filter_by(id=request.form.get("membro_id", type=int), propriedade_id=prop.id).first_or_404()
            if alvo_mb.papel == "proprietario":
                flash("O acesso do proprietário não pode ser alterado aqui.", "warning")
            elif acao == "remover":
                nome = alvo_mb.user.nome
                db.session.delete(alvo_mb)
                auditar("propriedade.membro_removido", f"Acesso de {nome} removido", propriedade_id=prop.id)
                db.session.commit()
                flash(f"Acesso de {nome} removido.", "info")
            else:
                papel = request.form.get("papel")
                if papel in PAPEIS_PROPRIEDADE and papel != "proprietario":
                    alvo_mb.papel = papel
                    auditar("propriedade.membro_alterado", f"Permissão de {alvo_mb.user.nome} alterada para {PAPEIS_PROPRIEDADE[papel]['label']}",
                            propriedade_id=prop.id)
                    db.session.commit()
                    flash("Permissão atualizada.", "success")
            return redirect(url_for("propriedades.membros", pid=prop.id))
    lista = PropriedadeMembro.query.filter_by(propriedade_id=prop.id).all()
    return render_template("propriedades/membros.html", prop=prop, lista=lista, papeis=PAPEIS_PROPRIEDADE, v=v, e=e)
