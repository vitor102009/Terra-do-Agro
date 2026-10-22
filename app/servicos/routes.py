from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    Avaliacao, HistoricoSolicitacao, Orcamento, PerfilProfissional, PropriedadeMembro, SolicitacaoServico, utcnow,
)
from app.models.mercado import STATUS_SOLICITACAO, TIPOS_SOLICITACAO
from app.utils.navegacao import pagina
from app.utils.registro import auditar, notificar
from app.utils.uploads import UploadError, caminho_absoluto, salvar_upload, tem_arquivo
from app.utils.validacao import Form

bp = Blueprint("servicos", __name__)

ETAPAS = ["aguardando", "orcamento_enviado", "orcamento_aceito", "agendado", "em_andamento", "concluido"]
FINAIS = {"concluido", "cancelado"}

# Transições permitidas: ação -> (quem pode, status de origem, status de destino)
TRANSICOES = {
    "aceitar_orcamento": ("cliente", {"orcamento_enviado"}, "orcamento_aceito"),
    "recusar_orcamento": ("cliente", {"orcamento_enviado"}, "orcamento_recusado"),
    "agendar": ("profissional", {"aguardando", "orcamento_aceito", "agendado"}, "agendado"),
    "iniciar": ("profissional", {"agendado", "orcamento_aceito"}, "em_andamento"),
    "concluir": ("ambos", {"em_andamento"}, "concluido"),
    "recusar_pedido": ("profissional", {"aguardando", "orcamento_recusado"}, "cancelado"),
    "cancelar": ("cliente", {"aguardando", "orcamento_enviado", "orcamento_aceito", "orcamento_recusado", "agendado"}, "cancelado"),
}


def _papel(sol):
    if sol.cliente_id == current_user.id:
        return "cliente"
    if sol.perfil.user_id == current_user.id:
        return "profissional"
    return None


def _obter(sid):
    sol = db.session.get(SolicitacaoServico, sid)
    if not sol:
        abort(404)
    papel = _papel(sol)
    if not papel and not current_user.is_admin:
        abort(404)
    return sol, papel


def mudar_status(sol, novo, nota=None):
    db.session.add(HistoricoSolicitacao(solicitacao=sol, status_anterior=sol.status, status_novo=novo, user_id=current_user.id, nota=nota))
    sol.status = novo
    if novo == "concluido":
        sol.concluido_em = utcnow()
    outro = sol.perfil.user_id if current_user.id == sol.cliente_id else sol.cliente_id
    notificar(outro, "servico", f"Solicitação \"{sol.titulo}\": {STATUS_SOLICITACAO[novo]}", nota,
              url_for("servicos.detalhe", sid=sol.id))


@bp.route("/")
@login_required
def index():
    papel = request.args.get("papel", "cliente")
    if papel == "profissional":
        q = SolicitacaoServico.query.join(PerfilProfissional).filter(PerfilProfissional.user_id == current_user.id)
    else:
        q = SolicitacaoServico.query.filter_by(cliente_id=current_user.id)
    situacao = request.args.get("situacao", "abertas")
    if situacao == "abertas":
        q = q.filter(SolicitacaoServico.status.notin_(FINAIS))
    elif situacao == "encerradas":
        q = q.filter(SolicitacaoServico.status.in_(FINAIS))
    pag = q.order_by(SolicitacaoServico.updated_at.desc()).paginate(page=pagina(), per_page=20, error_out=False)
    tem_perfil = PerfilProfissional.query.filter_by(user_id=current_user.id).count() > 0
    return render_template("servicos/index.html", pag=pag, papel=papel, situacao=situacao, status=STATUS_SOLICITACAO,
                           tipos=TIPOS_SOLICITACAO, tem_perfil=tem_perfil)


@bp.route("/nova", methods=["GET", "POST"])
@login_required
def nova():
    perfil = db.session.get(PerfilProfissional, request.values.get("perfil", type=int) or 0)
    if not perfil or not perfil.publico or not perfil.user.ativo:
        abort(404)
    if perfil.user_id == current_user.id:
        flash("Você não pode contratar o seu próprio perfil.", "warning")
        return redirect(url_for("profissionais.perfil", perfil_id=perfil.id))
    tipo = request.values.get("tipo", "orcamento")
    if tipo not in TIPOS_SOLICITACAO:
        tipo = "orcamento"
    props = {mb.propriedade_id: f"{mb.propriedade.nome} ({mb.propriedade.municipio}/{mb.propriedade.uf})"
             for mb in PropriedadeMembro.query.filter_by(user_id=current_user.id).all() if not mb.propriedade.arquivada}
    v = {"tipo": tipo, "municipio": current_user.municipio or "", "uf": current_user.uf or ""}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = {
            "tipo": form.choice("tipo", TIPOS_SOLICITACAO),
            "titulo": form.text("titulo", required=True, max_len=150),
            "descricao": form.text("descricao", required=True, max_len=5000, min_len=10),
            "data_desejada": form.date("data_desejada", min_date=date.today()),
            "propriedade_id": form.id_in("propriedade_id", set(props)),
            "municipio": form.text("municipio", max_len=120),
            "uf": form.uf("uf", required=False),
            "observacoes": form.text("observacoes", max_len=2000),
        }
        if not dados["propriedade_id"] and not dados["municipio"]:
            form.error("municipio", "Informe a propriedade ou o município do atendimento.")
        sol = SolicitacaoServico(cliente_id=current_user.id, perfil_id=perfil.id, status="aguardando", **dados)
        arq = request.files.get("anexo")
        if form.ok and tem_arquivo(arq):
            try:
                sol.anexo = salvar_upload(arq, "documento", "solicitacoes", privado=True)
            except UploadError as exc:
                form.error("anexo", str(exc))
        if form.ok:
            if sol.propriedade_id and not sol.municipio:
                p = next(mb.propriedade for mb in PropriedadeMembro.query.filter_by(user_id=current_user.id, propriedade_id=sol.propriedade_id))
                sol.municipio, sol.uf = p.municipio, p.uf
            db.session.add(sol)
            db.session.flush()
            db.session.add(HistoricoSolicitacao(solicitacao=sol, status_anterior=None, status_novo="criada", user_id=current_user.id))
            db.session.add(HistoricoSolicitacao(solicitacao=sol, status_anterior="criada", status_novo="aguardando", user_id=current_user.id))
            notificar(perfil.user_id, "servico", f"Nova solicitação de {TIPOS_SOLICITACAO[sol.tipo].lower().replace('solicitar ', '')}",
                      f"{current_user.nome}: {sol.titulo}", url_for("servicos.detalhe", sid=sol.id))
            if sol.propriedade_id:
                auditar("servico.solicitado", f"Serviço solicitado a {perfil.user.nome}: {sol.titulo}",
                        url_for("servicos.detalhe", sid=sol.id), propriedade_id=sol.propriedade_id)
            db.session.commit()
            flash("Solicitação enviada. Acompanhe as respostas por aqui e pelas notificações.", "success")
            return redirect(url_for("servicos.detalhe", sid=sol.id))
        v, e = form.values, form.errors
    return render_template("servicos/nova.html", perfil=perfil, v=v, e=e, tipos=TIPOS_SOLICITACAO, props=props)


@bp.route("/<int:sid>")
@login_required
def detalhe(sid):
    sol, papel = _obter(sid)
    acoes = [k for k, (quem, origem, _) in TRANSICOES.items() if sol.status in origem and (quem == papel or (quem == "ambos" and papel))]
    pode_orcar = papel == "profissional" and sol.status in ("aguardando", "orcamento_recusado", "orcamento_enviado")
    pode_avaliar = papel == "cliente" and sol.status == "concluido" and not sol.avaliacao
    etapa = ETAPAS.index(sol.status) if sol.status in ETAPAS else None
    return render_template("servicos/detalhe.html", sol=sol, papel=papel, acoes=acoes, pode_orcar=pode_orcar, pode_avaliar=pode_avaliar,
                           status=STATUS_SOLICITACAO, etapas=ETAPAS, etapa=etapa, tipos=TIPOS_SOLICITACAO, v={}, e={})


@bp.route("/<int:sid>/acao", methods=["POST"])
@login_required
def acao(sid):
    sol, papel = _obter(sid)
    nome = request.form.get("acao")
    regra = TRANSICOES.get(nome)
    if not regra or not papel:
        abort(400)
    quem, origem, destino = regra
    if sol.status not in origem or (quem != "ambos" and quem != papel):
        flash("Esta ação não é permitida na situação atual da solicitação.", "warning")
        return redirect(url_for("servicos.detalhe", sid=sid))
    nota = (request.form.get("nota") or "").strip()[:1000] or None
    if nome == "agendar":
        try:
            quando = datetime.strptime(request.form.get("data_agendada", ""), "%Y-%m-%dT%H:%M")
        except ValueError:
            flash("Informe data e horário válidos para o agendamento.", "danger")
            return redirect(url_for("servicos.detalhe", sid=sid))
        if quando.date() < date.today():
            flash("A data do agendamento não pode estar no passado.", "danger")
            return redirect(url_for("servicos.detalhe", sid=sid))
        # Armazenado em UTC (horário de Brasília + 3h)
        from datetime import timedelta

        sol.data_agendada = quando + timedelta(hours=3)
        nota = f"Agendado para {quando.strftime('%d/%m/%Y %H:%M')}" + (f" · {nota}" if nota else "")
    if nome in ("aceitar_orcamento", "recusar_orcamento"):
        orc = next((o for o in sol.orcamentos if o.status == "enviado"), None)
        if not orc:
            abort(400)
        orc.status = "aceito" if nome == "aceitar_orcamento" else "recusado"
    mudar_status(sol, destino, nota)
    if sol.propriedade_id:
        auditar("servico.status", f"Serviço \"{sol.titulo}\": {STATUS_SOLICITACAO[destino]}", url_for("servicos.detalhe", sid=sid),
                propriedade_id=sol.propriedade_id)
    db.session.commit()
    flash(f"Situação atualizada: {STATUS_SOLICITACAO[destino]}.", "success")
    return redirect(url_for("servicos.detalhe", sid=sid))


@bp.route("/<int:sid>/orcamento", methods=["POST"])
@login_required
def orcamento(sid):
    sol, papel = _obter(sid)
    if papel != "profissional" or sol.status not in ("aguardando", "orcamento_recusado", "orcamento_enviado"):
        abort(403)
    form = Form(request.form)
    valor = form.positive("valor")
    descricao = form.text("descricao", required=True, max_len=4000)
    validade = form.date("validade", min_date=date.today())
    prazo = form.text("prazo_execucao", max_len=120)
    if not form.ok:
        flash("Revise o orçamento: " + "; ".join(form.errors.values()), "danger")
        return redirect(url_for("servicos.detalhe", sid=sid))
    for o in sol.orcamentos:
        if o.status == "enviado":
            o.status = "substituido"
    db.session.add(Orcamento(solicitacao=sol, valor=valor, descricao=descricao, validade=validade, prazo_execucao=prazo))
    mudar_status(sol, "orcamento_enviado", f"Orçamento de R$ {valor:.2f}".replace(".", ","))
    db.session.commit()
    flash("Orçamento enviado ao cliente.", "success")
    return redirect(url_for("servicos.detalhe", sid=sid))


@bp.route("/<int:sid>/avaliar", methods=["POST"])
@login_required
def avaliar(sid):
    sol, papel = _obter(sid)
    if papel != "cliente" or sol.status != "concluido" or sol.avaliacao:
        abort(403)
    form = Form(request.form)
    nota = form.integer("nota", required=True, min_value=1, max_value=5)
    comentario = form.text("comentario", max_len=2000)
    if not form.ok:
        flash("Escolha uma nota de 1 a 5.", "danger")
        return redirect(url_for("servicos.detalhe", sid=sid))
    db.session.add(Avaliacao(solicitacao_id=sol.id, perfil_id=sol.perfil_id, autor_id=current_user.id, nota=nota, comentario=comentario))
    db.session.flush()
    atualizar_nota(sol.perfil)
    notificar(sol.perfil.user_id, "avaliacao", "Você recebeu uma avaliação", f"{nota} estrela(s) em \"{sol.titulo}\"",
              url_for("profissionais.perfil", perfil_id=sol.perfil_id))
    db.session.commit()
    flash("Obrigado pela avaliação.", "success")
    return redirect(url_for("servicos.detalhe", sid=sid))


def atualizar_nota(perfil):
    q = db.session.query(db.func.avg(Avaliacao.nota), db.func.count(Avaliacao.id)).filter(
        Avaliacao.perfil_id == perfil.id, Avaliacao.oculta.is_(False))
    media, total = q.one()
    perfil.nota_media = round(float(media), 2) if media else None
    perfil.total_avaliacoes = total or 0


@bp.route("/<int:sid>/anexo")
@login_required
def anexo(sid):
    sol, _ = _obter(sid)
    if not sol.anexo:
        abort(404)
    resp = send_file(caminho_absoluto(sol.anexo), as_attachment=True, download_name=f"anexo-solicitacao-{sol.id}.{sol.anexo.rsplit('.', 1)[-1]}")
    resp.headers["Cache-Control"] = "private, no-store"
    return resp
