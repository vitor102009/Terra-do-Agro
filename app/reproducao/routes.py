from datetime import date

from flask import Blueprint, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Animal, EventoReprodutivo, MovimentacaoAnimal, Pesagem
from app.models.rebanho import RESULTADOS_DIAGNOSTICO, SEXOS, SITUACOES_REPRODUTIVAS, TIPOS_REPRODUTIVOS
from app.saude.routes import profissionais_vinculados
from app.services import rebanho as svc
from app.services.indicadores import reprodutivo
from app.utils.acesso import obter_da_propriedade, propriedade_requerida
from app.utils.navegacao import pagina
from app.utils.registro import auditar
from app.utils.validacao import Form

bp = Blueprint("reproducao", __name__)


def intervalos_entre_partos(pid):
    """Dias entre partos consecutivos de cada fêmea (indicador só existe a partir do 2º parto)."""
    partos = (EventoReprodutivo.query.filter_by(propriedade_id=pid, tipo="parto")
              .order_by(EventoReprodutivo.animal_id, EventoReprodutivo.data).all())
    por_femea, intervalos = {}, []
    for p in partos:
        por_femea.setdefault(p.animal_id, []).append(p.data)
    for datas in por_femea.values():
        for a, b in zip(datas, datas[1:]):
            intervalos.append((b - a).days)
    return intervalos


def recalcular_situacao(animal):
    """Reaplica todos os eventos em ordem cronológica (usado após exclusão de um evento)."""
    eventos = EventoReprodutivo.query.filter_by(animal_id=animal.id).order_by(EventoReprodutivo.data, EventoReprodutivo.id).all()
    animal.situacao_reprodutiva = "vazia" if eventos else "nao_aplicavel"
    for ev in eventos:
        svc.aplicar_evento_reprodutivo(animal, ev)


@bp.route("/")
@propriedade_requerida("ver")
def index():
    pid = g.propriedade.id
    aba = request.args.get("aba", "situacao")
    ctx = {"aba": aba, "repro": reprodutivo(pid), "tipos": TIPOS_REPRODUTIVOS, "situacoes": SITUACOES_REPRODUTIVAS}
    intervalos = intervalos_entre_partos(pid)
    ctx["iep_medio"] = (sum(intervalos) / len(intervalos)) if intervalos else None
    ctx["iep_n"] = len(intervalos)
    if aba == "eventos":
        q = EventoReprodutivo.query.filter_by(propriedade_id=pid)
        if request.args.get("tipo") in TIPOS_REPRODUTIVOS:
            q = q.filter_by(tipo=request.args["tipo"])
        ctx["pag"] = q.order_by(EventoReprodutivo.data.desc(), EventoReprodutivo.id.desc()).paginate(page=pagina(), per_page=30, error_out=False)
    else:
        femeas = (Animal.query.filter(Animal.propriedade_id == pid, Animal.status == "ativo", Animal.sexo == "F",
                                      Animal.situacao_reprodutiva != "nao_aplicavel").order_by(Animal.brinco).all())
        ultimos = {}
        for ev in (EventoReprodutivo.query.filter(EventoReprodutivo.animal_id.in_([f.id for f in femeas]))
                   .order_by(EventoReprodutivo.data).all() if femeas else []):
            ultimos[ev.animal_id] = ev
            if ev.previsao_parto:
                ultimos[("prev", ev.animal_id)] = ev.previsao_parto
        ctx.update(femeas=femeas, ultimos=ultimos, hoje=date.today())
    ctx["args"] = request.args
    return render_template("reproducao/index.html", **ctx)


@bp.route("/novo", methods=["GET", "POST"])
@propriedade_requerida("editar")
def novo():
    pid = g.propriedade.id
    femeas = svc.animais_opcoes(pid, "F")
    machos = svc.animais_opcoes(pid, "M")
    profs = profissionais_vinculados()
    v = {"data": date.today().isoformat(), "animal_id": request.args.get("animal", ""), "tipo": request.args.get("tipo", "inseminacao"),
         "responsavel": "", "cria_sexo": ""}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        aid = form.id_in("animal_id", {a.id for a in femeas}, required=True)
        tipo = form.choice("tipo", TIPOS_REPRODUTIVOS)
        data_ev = form.date("data", required=True, not_future=True)
        dados = {
            "reprodutor_id": form.id_in("reprodutor_id", {a.id for a in machos}) if tipo in ("cobertura", "inseminacao") else None,
            "reprodutor_descricao": form.text("reprodutor_descricao", max_len=150) if tipo in ("cobertura", "inseminacao") else None,
            "diagnostico_resultado": form.choice("diagnostico_resultado", RESULTADOS_DIAGNOSTICO, required=(tipo == "diagnostico")) if tipo == "diagnostico" else None,
            "metodo_diagnostico": form.text("metodo_diagnostico", max_len=60) if tipo == "diagnostico" else None,
            "responsavel": form.text("responsavel", required=(tipo == "diagnostico"), max_len=120),
            "profissional_id": form.id_in("profissional_id", {p.id for p in profs}),
            "observacoes": form.text("observacoes", max_len=4000),
        }
        animal = db.session.get(Animal, aid) if aid else None
        if animal and data_ev and data_ev < animal.data_entrada and tipo != "parto":
            form.error("data", "A data é anterior à entrada do animal na propriedade.")
        cria = None
        if tipo == "parto" and form.boolean("registrar_cria"):
            cria_brinco = form.text("cria_brinco", max_len=40)
            cria_sexo = form.choice("cria_sexo", SEXOS)
            cria_peso = form.positive("cria_peso", required=False, max_value=200)
            if cria_brinco and svc.brinco_em_uso(pid, cria_brinco):
                form.error("cria_brinco", "Já existe um animal com esta identificação.")
            if form.ok and animal:
                cria = Animal(propriedade_id=pid, brinco=cria_brinco, especie=animal.especie, raca=animal.raca, sexo=cria_sexo,
                              data_nascimento=data_ev, origem="nascido", data_entrada=data_ev, mae_id=animal.id,
                              lote_id=animal.lote_id, categoria=("Bezerra" if cria_sexo == "F" else "Bezerro") if animal.especie == "bovino" else None,
                              situacao_produtiva="cria", criado_por=current_user.id)
        if form.ok:
            ev = EventoReprodutivo(propriedade_id=pid, animal_id=aid, tipo=tipo, data=data_ev, registrado_por=current_user.id, **dados)
            try:
                db.session.add(ev)
                if cria:
                    db.session.add(cria)
                    db.session.flush()
                    ev.cria_id = cria.id
                    db.session.add(MovimentacaoAnimal(animal=cria, tipo="entrada", data=data_ev, lote_destino_id=cria.lote_id,
                                                      observacao=f"Nascimento (mãe {animal.identificacao})", registrado_por=current_user.id))
                    if cria_peso:
                        db.session.add(Pesagem(propriedade_id=pid, animal_id=cria.id, data=data_ev, peso_kg=cria_peso,
                                               observacoes="Peso ao nascer", registrado_por=current_user.id))
                        db.session.flush()
                        svc.atualizar_peso_atual(cria)
                db.session.flush()
                svc.aplicar_evento_reprodutivo(animal, ev)
                auditar("reproducao.registrado", f"{TIPOS_REPRODUTIVOS[tipo]}: {animal.identificacao}", url_for("animais.ficha", aid=aid))
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.error("cria_brinco", "Já existe um animal com esta identificação.")
            else:
                msg = "Evento reprodutivo registrado."
                if ev.previsao_parto:
                    msg += f" Previsão estimada de parto: {ev.previsao_parto.strftime('%d/%m/%Y')}."
                flash(msg, "success")
                return redirect(url_for("animais.ficha", aid=aid))
        v, e = form.values, form.errors
    return render_template("reproducao/form.html", v=v, e=e, tipos=TIPOS_REPRODUTIVOS, resultados=RESULTADOS_DIAGNOSTICO,
                           femeas={a.id: a.identificacao for a in femeas}, machos={a.id: a.identificacao for a in machos},
                           profs={p.id: p.nome for p in profs}, sexos=SEXOS)


@bp.route("/<int:evid>/excluir", methods=["POST"])
@propriedade_requerida("editar")
def excluir(evid):
    ev = obter_da_propriedade(EventoReprodutivo, evid)
    animal = ev.animal
    ultimo = EventoReprodutivo.query.filter_by(animal_id=animal.id).order_by(EventoReprodutivo.data.desc(), EventoReprodutivo.id.desc()).first()
    if ultimo.id != ev.id:
        flash("Somente o evento mais recente da fêmea pode ser excluído, para manter o histórico consistente.", "warning")
        return redirect(url_for("reproducao.index", aba="eventos"))
    descricao = f"Evento reprodutivo excluído: {ev.tipo_label} de {animal.identificacao} em {ev.data.strftime('%d/%m/%Y')}"
    db.session.delete(ev)
    db.session.flush()
    recalcular_situacao(animal)
    auditar("reproducao.excluido", descricao, url_for("animais.ficha", aid=animal.id))
    db.session.commit()
    flash("Evento excluído e situação reprodutiva recalculada. Uma cria cadastrada no parto permanece no rebanho.", "info")
    return redirect(url_for("reproducao.index", aba="eventos"))
