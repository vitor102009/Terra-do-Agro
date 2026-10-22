"""API interna usada pelo aplicativo (PWA) para o modo offline.

- /api/offline/snapshot: cópia mínima dos dados operacionais das propriedades do usuário (sem dados financeiros).
- /api/sync: aplica UMA operação registrada offline. É idempotente: o mesmo UUID nunca gera dois registros.
  Respostas: aplicada | duplicada | conflito | rejeitada.
"""
import json
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, url_for
from flask_login import current_user, login_required
from flask_wtf.csrf import generate_csrf
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Animal, EventoSanitario, Lote, OperacaoOffline, Pesagem, Propriedade, PropriedadeMembro, Tarefa, utcnow,
)
from app.models.rebanho import SITUACOES_REPRODUTIVAS, TIPOS_SANITARIOS
from app.models.sistema import TIPOS_TAREFA
from app.saude.routes import resolver_pendentes
from app.services import rebanho as svc
from app.utils.offline import chave_offline
from app.utils.registro import auditar

bp = Blueprint("api", __name__)

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LIMITE_ANIMAIS = 5000
TIPOS_OPERACAO = ("pesagem", "sanitario", "tarefa")


def _nocache(resp):
    resp.headers["Cache-Control"] = "no-store, private"
    return resp


@bp.route("/csrf")
@login_required
def csrf():
    # 'chave' permite ao aplicativo confirmar que a sessão é do mesmo usuário dono da fila offline.
    return _nocache(jsonify(token=generate_csrf(), usuario=current_user.primeiro_nome, chave=chave_offline(current_user.id)))


@bp.route("/sessao")
def sessao():
    """Informa se há sessão ativa (usado pelo modo offline ao reconectar)."""
    if not current_user.is_authenticated:
        return _nocache(jsonify(autenticado=False))
    return _nocache(jsonify(autenticado=True, nome=current_user.primeiro_nome))


@bp.route("/offline/snapshot")
@login_required
def snapshot():
    hoje = date.today()
    membros = (PropriedadeMembro.query.join(Propriedade)
               .filter(PropriedadeMembro.user_id == current_user.id, Propriedade.arquivada.is_(False)).all())
    props = []
    for mb in membros:
        p = mb.propriedade
        lotes = {l.id: l.nome for l in Lote.query.filter_by(propriedade_id=p.id).all()}
        animais = (Animal.query.filter_by(propriedade_id=p.id, status="ativo").order_by(Animal.brinco).limit(LIMITE_ANIMAIS).all())
        pend = (EventoSanitario.query.filter(EventoSanitario.propriedade_id == p.id, EventoSanitario.proxima_data.isnot(None),
                                             EventoSanitario.proxima_resolvida.is_(False),
                                             EventoSanitario.proxima_data <= hoje + timedelta(days=60))
                .order_by(EventoSanitario.proxima_data).limit(300).all())
        tarefas = Tarefa.query.filter_by(propriedade_id=p.id, status="pendente").order_by(Tarefa.data).limit(300).all()
        props.append({
            "id": p.id, "nome": p.nome, "municipio": p.municipio, "uf": p.uf, "papel": mb.papel_label,
            "pode_editar": "editar" in mb.perms,
            "lotes": [{"id": k, "nome": v} for k, v in sorted(lotes.items(), key=lambda x: x[1])],
            "animais": [{"id": a.id, "brinco": a.identificacao, "nome": a.nome, "especie": a.especie, "sexo": a.sexo,
                         "categoria": a.categoria, "lote_id": a.lote_id, "lote": lotes.get(a.lote_id),
                         "peso_atual": float(a.peso_atual) if a.peso_atual is not None else None,
                         "data_peso": a.data_peso_atual.isoformat() if a.data_peso_atual else None,
                         "reprodutiva": SITUACOES_REPRODUTIVAS.get(a.situacao_reprodutiva),
                         "nascimento": a.data_nascimento.isoformat() if a.data_nascimento else None} for a in animais],
            "agenda_sanitaria": [{"produto": e.produto, "alvo": e.alvo, "data": e.proxima_data.isoformat(), "tipo": e.tipo_label} for e in pend],
            "tarefas": [{"titulo": t.titulo, "data": t.data.isoformat(), "tipo": TIPOS_TAREFA.get(t.tipo)} for t in tarefas],
        })
    return _nocache(jsonify(gerado_em=utcnow().isoformat() + "Z", usuario=current_user.primeiro_nome, propriedades=props,
                            tipos_sanitarios=TIPOS_SANITARIOS, tipos_tarefa=TIPOS_TAREFA))


def _resposta(status, mensagem, http=200, **extra):
    return _nocache(jsonify(status=status, mensagem=mensagem, **extra)), http


def _data(valor, campo):
    try:
        d = date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        raise ValueError(f"Data inválida em '{campo}'.")
    if d > date.today() + timedelta(days=1):  # tolera diferença de fuso/relógio do aparelho
        raise ValueError(f"A data em '{campo}' está no futuro.")
    return d


def _texto(dados, campo, maximo, obrigatorio=False):
    v = (dados.get(campo) or "").strip() if isinstance(dados.get(campo), str) else ""
    if obrigatorio and not v:
        raise ValueError(f"Campo obrigatório: {campo}.")
    return v[:maximo] or None


@bp.route("/sync", methods=["POST"])
@login_required
def sync():
    corpo = request.get_json(silent=True)
    if not isinstance(corpo, dict):
        return _resposta("rejeitada", "Requisição inválida.", 400)
    uid = str(corpo.get("uuid") or "").lower()
    tipo = corpo.get("tipo")
    dados = corpo.get("dados") if isinstance(corpo.get("dados"), dict) else {}
    if not UUID_RE.match(uid) or tipo not in TIPOS_OPERACAO:
        return _resposta("rejeitada", "Operação inválida.", 400)

    anterior = OperacaoOffline.query.filter_by(uuid=uid).first()
    if anterior:
        if anterior.user_id != current_user.id:
            return _resposta("rejeitada", "Identificador já utilizado.", 409)
        res = json.loads(anterior.resultado or "{}")
        res.pop("mensagem", None)
        msg = "Esta operação já tinha sido processada." if anterior.status == "aplicada" else json.loads(anterior.resultado or "{}").get("mensagem", "")
        return _resposta("duplicada" if anterior.status == "aplicada" else anterior.status, msg, **res)

    pid = corpo.get("propriedade_id")
    mb = PropriedadeMembro.query.filter_by(user_id=current_user.id, propriedade_id=pid).first() if isinstance(pid, int) else None
    if not mb or mb.propriedade.arquivada:
        return _registrar_final(uid, tipo, "rejeitada", "Você não tem acesso a esta propriedade.")
    if "editar" not in mb.perms:
        return _registrar_final(uid, tipo, "rejeitada", "Seu acesso a esta propriedade não permite registrar dados.")
    forcar = bool(corpo.get("forcar"))
    try:
        if tipo == "pesagem":
            return _sync_pesagem(uid, mb.propriedade, dados, forcar)
        if tipo == "sanitario":
            return _sync_sanitario(uid, mb.propriedade, dados, forcar)
        return _sync_tarefa(uid, mb.propriedade, dados)
    except ValueError as exc:
        db.session.rollback()
        return _registrar_final(uid, tipo, "rejeitada", str(exc))
    except IntegrityError:
        # Corrida com outra requisição usando o mesmo UUID: a restrição única garantiu um único registro.
        db.session.rollback()
        return _resposta("duplicada", "Esta operação já tinha sido processada.")


def _registrar_final(uid, tipo, status, mensagem, **res):
    db.session.add(OperacaoOffline(uuid=uid, user_id=current_user.id, tipo=tipo, status=status,
                                   resultado=json.dumps({"mensagem": mensagem, **res})))
    db.session.commit()
    return _resposta(status, mensagem, **res)


def _animal(prop, dados):
    try:
        aid = int(dados.get("animal_id"))
    except (TypeError, ValueError):
        raise ValueError("Animal não informado.")
    a = Animal.query.filter_by(id=aid, propriedade_id=prop.id).first()
    if not a:
        raise ValueError("Animal não encontrado nesta propriedade.")
    return a


def _sync_pesagem(uid, prop, dados, forcar):
    animal = _animal(prop, dados)
    data_p = _data(dados.get("data"), "data")
    try:
        peso = Decimal(str(dados.get("peso_kg")).replace(",", "."))
    except (InvalidOperation, TypeError):
        raise ValueError("Peso inválido.")
    if not peso.is_finite() or peso <= 0 or peso > 5000:
        raise ValueError("O peso deve estar entre 0 e 5000 kg.")
    peso = peso.quantize(Decimal("0.01"))
    if not animal.ativo:
        return _resposta("conflito", f"O animal {animal.identificacao} não está mais ativo no rebanho "
                                     f"({animal.status}). Descarte o registro ou ajuste no sistema.", motivo="animal_inativo")
    if data_p < animal.data_entrada:
        raise ValueError("A data é anterior à entrada do animal na propriedade.")
    existente = Pesagem.query.filter_by(animal_id=animal.id, data=data_p).first()
    if existente and not forcar:
        if existente.peso_kg == peso:
            return _registrar_final(uid, "pesagem", "aplicada", "Pesagem idêntica já existia no servidor.", id=existente.id)
        return _resposta("conflito", f"Já existe uma pesagem de {existente.peso_kg} kg para {animal.identificacao} em "
                                     f"{data_p.strftime('%d/%m/%Y')}. Deseja manter as duas?", motivo="pesagem_mesma_data",
                         servidor={"peso_kg": float(existente.peso_kg), "responsavel": existente.responsavel})
    p = Pesagem(propriedade_id=prop.id, animal_id=animal.id, data=data_p, peso_kg=peso,
                observacoes=_texto(dados, "observacoes", 2000), responsavel=_texto(dados, "responsavel", 120) or current_user.nome,
                registrado_por=current_user.id, offline_uuid=uid)
    db.session.add(p)
    db.session.flush()
    svc.atualizar_peso_atual(animal)
    auditar("pesagem.registrada", f"Pesagem de {peso} kg do animal {animal.identificacao} (registrada offline)",
            url_for("animais.ficha", aid=animal.id), propriedade_id=prop.id)
    return _registrar_final(uid, "pesagem", "aplicada", "Pesagem sincronizada.", id=p.id)


def _sync_sanitario(uid, prop, dados, forcar):
    tipo = dados.get("tipo") if dados.get("tipo") in TIPOS_SANITARIOS else None
    if not tipo:
        raise ValueError("Tipo de registro sanitário inválido.")
    produto = _texto(dados, "produto", 150, obrigatorio=True)
    data_ap = _data(dados.get("data_aplicacao"), "data_aplicacao")
    proxima = None
    if dados.get("proxima_data"):
        try:
            proxima = date.fromisoformat(str(dados["proxima_data"])[:10])
        except ValueError:
            raise ValueError("Data da próxima aplicação inválida.")
        if proxima < data_ap:
            raise ValueError("A próxima aplicação deve ser depois da aplicação.")
    animal_id = lote_id = None
    alvo = dados.get("alvo")
    if alvo == "animal":
        a = _animal(prop, dados)
        if not a.ativo:
            return _resposta("conflito", f"O animal {a.identificacao} não está mais ativo. Descarte o registro ou ajuste no sistema.",
                             motivo="animal_inativo")
        animal_id = a.id
    elif alvo == "lote":
        try:
            lote = Lote.query.filter_by(id=int(dados.get("lote_id")), propriedade_id=prop.id).first()
        except (TypeError, ValueError):
            lote = None
        if not lote:
            raise ValueError("Lote não encontrado nesta propriedade.")
        lote_id = lote.id
    elif alvo != "rebanho":
        raise ValueError("Informe em quem foi feita a aplicação.")
    existente = EventoSanitario.query.filter(
        EventoSanitario.propriedade_id == prop.id, EventoSanitario.data_aplicacao == data_ap,
        db.func.lower(EventoSanitario.produto) == produto.lower(),
        EventoSanitario.animal_id.is_(animal_id) if animal_id is None else EventoSanitario.animal_id == animal_id,
        EventoSanitario.lote_id.is_(lote_id) if lote_id is None else EventoSanitario.lote_id == lote_id).first()
    if existente and not forcar:
        return _resposta("conflito", f"Já existe registro de {produto} para o mesmo alvo em {data_ap.strftime('%d/%m/%Y')}. "
                                     "Deseja registrar novamente?", motivo="sanitario_duplicado")
    ev = EventoSanitario(propriedade_id=prop.id, animal_id=animal_id, lote_id=lote_id, tipo=tipo, produto=produto,
                         lote_produto=_texto(dados, "lote_produto", 60), dose=_texto(dados, "dose", 60), data_aplicacao=data_ap,
                         proxima_data=proxima, responsavel=_texto(dados, "responsavel", 120) or current_user.nome,
                         observacoes=_texto(dados, "observacoes", 4000), registrado_por=current_user.id, offline_uuid=uid)
    db.session.add(ev)
    db.session.flush()
    resolver_pendentes(ev)
    auditar("sanitario.registrado", f"{TIPOS_SANITARIOS[tipo]}: {produto} · {ev.alvo} (registrado offline)",
            url_for("saude.detalhe", eid=ev.id), propriedade_id=prop.id)
    return _registrar_final(uid, "sanitario", "aplicada", "Registro sanitário sincronizado.", id=ev.id)


def _sync_tarefa(uid, prop, dados):
    titulo = _texto(dados, "titulo", 150, obrigatorio=True)
    try:
        data_t = date.fromisoformat(str(dados.get("data"))[:10])
    except ValueError:
        raise ValueError("Data inválida.")
    tipo = dados.get("tipo") if dados.get("tipo") in TIPOS_TAREFA else "tarefa"
    t = Tarefa(user_id=current_user.id, propriedade_id=prop.id, responsavel_id=current_user.id, tipo=tipo, titulo=titulo,
               descricao=_texto(dados, "descricao", 3000), data=data_t, offline_uuid=uid)
    db.session.add(t)
    db.session.flush()
    auditar("tarefa.criada", f"{TIPOS_TAREFA[tipo]} agendada: {titulo} (registrada offline)", url_for("notificacoes.agenda"),
            propriedade_id=prop.id)
    return _registrar_final(uid, "tarefa", "aplicada", "Tarefa sincronizada.", id=t.id)


@bp.route("/sync/status")
@login_required
def sync_status():
    """Consulta o resultado de operações já enviadas (para confirmar persistência antes de limpar a fila local)."""
    uuids = [u.lower() for u in request.args.get("uuids", "").split(",") if UUID_RE.match(u.lower())][:200]
    ops = OperacaoOffline.query.filter(OperacaoOffline.uuid.in_(uuids), OperacaoOffline.user_id == current_user.id).all() if uuids else []
    return _nocache(jsonify({o.uuid: o.status for o in ops}))
