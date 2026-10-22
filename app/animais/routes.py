import csv
import io
from datetime import date

from flask import Blueprint, Response, flash, g, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Animal, CategoriaAnimal, EventoReprodutivo, EventoSanitario, Lancamento, MovimentacaoAnimal, Pagamento, Pesagem,
)
from app.models.rebanho import (
    CATEGORIAS_PADRAO, ESPECIES, ORIGENS, SEXOS, SITUACOES_PRODUTIVAS, SITUACOES_REPRODUTIVAS, SITUACOES_SANITARIAS,
    STATUS_ANIMAL, TIPOS_MOVIMENTACAO,
)
from app.services import rebanho as svc
from app.services.indicadores import gmd
from app.utils.acesso import obter_da_propriedade, pode, propriedade_requerida
from app.utils.navegacao import pagina
from app.utils.registro import auditar
from app.utils.uploads import UploadError, remover_upload, salvar_upload, tem_arquivo
from app.utils.validacao import Form, valores_de

bp = Blueprint("animais", __name__)

CAMPOS = ["brinco", "nome", "especie", "raca", "sexo", "data_nascimento", "nascimento_estimado", "categoria", "lote_id",
          "situacao_produtiva", "situacao_reprodutiva", "situacao_sanitaria", "origem", "data_entrada", "pai_descricao",
          "mae_id", "observacoes"]


def _filtrar(q, args):
    if args.get("q"):
        termo = f"%{args['q'].strip()}%"
        q = q.filter(db.or_(Animal.brinco.ilike(termo), Animal.nome.ilike(termo)))
    status = args.get("status", "ativo")
    if status in STATUS_ANIMAL:
        q = q.filter(Animal.status == status)
    if args.get("lote", type=int):
        q = q.filter(Animal.lote_id == args.get("lote", type=int))
    elif args.get("lote") == "sem":
        q = q.filter(Animal.lote_id.is_(None))
    for campo, coluna, opcoes in (("sexo", Animal.sexo, SEXOS), ("especie", Animal.especie, ESPECIES),
                                  ("reprodutiva", Animal.situacao_reprodutiva, SITUACOES_REPRODUTIVAS),
                                  ("sanitaria", Animal.situacao_sanitaria, SITUACOES_SANITARIAS)):
        if args.get(campo) in opcoes:
            q = q.filter(coluna == args[campo])
    if args.get("raca"):
        q = q.filter(Animal.raca.ilike(f"%{args['raca'].strip()}%"))
    if args.get("categoria"):
        q = q.filter(Animal.categoria == args["categoria"])
    return q


ORDENS = {"brinco": Animal.brinco, "entrada": Animal.data_entrada.desc(), "peso": Animal.peso_atual.desc(),
          "nascimento": Animal.data_nascimento}


@bp.route("/")
@propriedade_requerida("ver")
def index():
    pid = g.propriedade.id
    q = _filtrar(Animal.query.filter(Animal.propriedade_id == pid), request.args)
    ordem = ORDENS.get(request.args.get("ordem"), Animal.brinco)
    pag = q.order_by(ordem, Animal.id).paginate(page=pagina(), per_page=30, error_out=False)
    return render_template("animais/index.html", pag=pag, lotes=svc.lotes_da_propriedade(pid, False),
                           categorias=svc.categorias_disponiveis(pid), sexos=SEXOS, especies=ESPECIES,
                           status_opcoes=STATUS_ANIMAL, reprodutivas=SITUACOES_REPRODUTIVAS,
                           sanitarias=SITUACOES_SANITARIAS, args=request.args)


@bp.route("/exportar.csv")
@propriedade_requerida("ver")
def exportar():
    pid = g.propriedade.id
    animais = _filtrar(Animal.query.filter(Animal.propriedade_id == pid), request.args).order_by(Animal.brinco).all()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Brinco", "Nome", "Espécie", "Raça", "Sexo", "Nascimento", "Categoria", "Lote", "Situação produtiva",
                "Situação reprodutiva", "Situação sanitária", "Peso atual (kg)", "Data do peso", "Origem", "Entrada", "Status"])
    for a in animais:
        w.writerow([a.brinco or "", a.nome or "", ESPECIES.get(a.especie, a.especie), a.raca or "", SEXOS.get(a.sexo, a.sexo),
                    a.data_nascimento.strftime("%d/%m/%Y") if a.data_nascimento else "", a.categoria or "",
                    a.lote.nome if a.lote else "", SITUACOES_PRODUTIVAS.get(a.situacao_produtiva, ""),
                    SITUACOES_REPRODUTIVAS.get(a.situacao_reprodutiva, ""), SITUACOES_SANITARIAS.get(a.situacao_sanitaria, ""),
                    str(a.peso_atual).replace(".", ",") if a.peso_atual is not None else "",
                    a.data_peso_atual.strftime("%d/%m/%Y") if a.data_peso_atual else "", ORIGENS.get(a.origem, a.origem),
                    a.data_entrada.strftime("%d/%m/%Y"), STATUS_ANIMAL.get(a.status, a.status)])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=rebanho-{date.today().isoformat()}.csv"})


def _ler(form, pid, animal=None):
    lotes = {l.id for l in svc.lotes_da_propriedade(pid, False)}
    femeas = {a.id for a in svc.animais_opcoes(pid, "F") if not animal or a.id != animal.id}
    if animal and animal.mae_id:
        femeas.add(animal.mae_id)
    dados = {
        "brinco": form.text("brinco", max_len=40),
        "nome": form.text("nome", max_len=80),
        "especie": form.choice("especie", ESPECIES),
        "raca": form.text("raca", max_len=60),
        "sexo": form.choice("sexo", SEXOS),
        "data_nascimento": form.date("data_nascimento", not_future=True),
        "nascimento_estimado": form.boolean("nascimento_estimado"),
        "categoria": form.text("categoria", max_len=60),
        "lote_id": form.id_in("lote_id", lotes),
        "situacao_produtiva": form.choice("situacao_produtiva", SITUACOES_PRODUTIVAS, required=False),
        "situacao_reprodutiva": form.choice("situacao_reprodutiva", SITUACOES_REPRODUTIVAS, required=False, default="nao_aplicavel"),
        "situacao_sanitaria": form.choice("situacao_sanitaria", SITUACOES_SANITARIAS, required=False, default="saudavel"),
        "origem": form.choice("origem", ORIGENS),
        "data_entrada": form.date("data_entrada", required=True, not_future=True),
        "pai_descricao": form.text("pai_descricao", max_len=120),
        "mae_id": form.id_in("mae_id", femeas),
        "observacoes": form.text("observacoes", max_len=4000),
    }
    if dados["brinco"] and svc.brinco_em_uso(pid, dados["brinco"], animal.id if animal else None):
        form.error("brinco", "Já existe um animal com esta identificação nesta propriedade.")
    if dados["sexo"] == "M" and dados["situacao_reprodutiva"] not in (None, "nao_aplicavel"):
        form.error("situacao_reprodutiva", "Situação reprodutiva de fêmea não se aplica a machos.")
    if dados["data_nascimento"] and dados["data_entrada"] and dados["data_entrada"] < dados["data_nascimento"]:
        form.error("data_entrada", "A data de entrada não pode ser anterior ao nascimento.")
    return dados


def _foto(form, animal):
    arq = request.files.get("foto")
    if tem_arquivo(arq):
        try:
            novo = salvar_upload(arq, "imagem", "animais", privado=True)
        except UploadError as exc:
            form.error("foto", str(exc))
            return
        remover_upload(animal.foto)
        animal.foto = novo


def _form_ctx(pid, animal=None):
    return dict(especies=ESPECIES, sexos=SEXOS, origens=ORIGENS, produtivas=SITUACOES_PRODUTIVAS,
                reprodutivas=SITUACOES_REPRODUTIVAS, sanitarias=SITUACOES_SANITARIAS,
                lotes={l.id: l.nome for l in svc.lotes_da_propriedade(pid, False)},
                maes={a.id: a.identificacao for a in svc.animais_opcoes(pid, "F") if not animal or a.id != animal.id},
                categorias=svc.categorias_disponiveis(pid), animal=animal)


@bp.route("/novo", methods=["GET", "POST"])
@propriedade_requerida("editar")
def novo():
    pid = g.propriedade.id
    v = {"especie": "bovino", "origem": "nascido", "data_entrada": date.today().isoformat(), "situacao_sanitaria": "saudavel",
         "situacao_reprodutiva": "nao_aplicavel", "lote_id": request.args.get("lote", "")}
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, pid)
        peso = form.positive("peso_inicial", required=False, max_value=5000)
        valor_compra = None
        if dados.get("origem") == "compra" and pode("financeiro"):
            valor_compra = form.positive("valor_compra", required=False)
        if form.ok:
            animal = Animal(propriedade_id=pid, criado_por=current_user.id, **dados)
            _foto(form, animal)
        if form.ok:
            try:
                db.session.add(animal)
                db.session.flush()
                mov = MovimentacaoAnimal(animal=animal, tipo="entrada", data=animal.data_entrada, lote_destino_id=animal.lote_id,
                                         observacao=ORIGENS.get(animal.origem), registrado_por=current_user.id)
                db.session.add(mov)
                if peso:
                    db.session.add(Pesagem(propriedade_id=pid, animal_id=animal.id, data=animal.data_entrada, peso_kg=peso,
                                           observacoes="Peso na entrada", registrado_por=current_user.id,
                                           responsavel=current_user.nome))
                    db.session.flush()
                    svc.atualizar_peso_atual(animal)
                if valor_compra:
                    cat = svc.categoria_financeira(pid, "despesa", "compra_animais")
                    lanc = Lancamento(propriedade_id=pid, tipo="despesa", descricao=f"Compra do animal {animal.identificacao}",
                                      categoria_id=cat.id, valor=valor_compra, data=animal.data_entrada, animal_id=animal.id,
                                      lote_id=animal.lote_id, origem="animal", registrado_por=current_user.id)
                    db.session.add(lanc)
                    db.session.flush()
                    mov.valor = valor_compra
                    mov.lancamento_id = lanc.id
                auditar("animal.cadastrado", f"Animal {animal.identificacao} cadastrado", url_for("animais.ficha", aid=animal.id))
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.error("brinco", "Já existe um animal com esta identificação nesta propriedade.")
            else:
                flash(f"Animal {animal.identificacao} cadastrado.", "success")
                if request.form.get("continuar"):
                    return redirect(url_for("animais.novo", lote=animal.lote_id or ""))
                return redirect(url_for("animais.ficha", aid=animal.id))
        v, e = form.values, form.errors
    return render_template("animais/form.html", v=v, e=e, **_form_ctx(pid))


@bp.route("/<int:aid>")
@propriedade_requerida("ver")
def ficha(aid):
    animal = obter_da_propriedade(Animal, aid)
    pesagens = Pesagem.query.filter_by(animal_id=aid).order_by(Pesagem.data, Pesagem.id).all()
    variacoes = []
    anterior = None
    for p in pesagens:
        if anterior:
            dias = (p.data - anterior.data).days
            dif = p.peso_kg - anterior.peso_kg
            variacoes.append((p, dif, dias, (dif / dias) if dias > 0 else None))
        else:
            variacoes.append((p, None, None, None))
        anterior = p
    sanitarios = EventoSanitario.query.filter(
        EventoSanitario.propriedade_id == g.propriedade.id,
        db.or_(EventoSanitario.animal_id == aid,
               db.and_(EventoSanitario.animal_id.is_(None), EventoSanitario.lote_id == animal.lote_id) if animal.lote_id else db.false())
    ).order_by(EventoSanitario.data_aplicacao.desc()).all()
    reprodutivos = EventoReprodutivo.query.filter_by(animal_id=aid).order_by(EventoReprodutivo.data.desc()).all()
    crias = Animal.query.filter_by(mae_id=aid).all()
    financeiros = Lancamento.query.filter_by(animal_id=aid).order_by(Lancamento.data.desc()).all() if pode("financeiro") else []
    grafico = {"type": "line", "unit": "kg", "labels": [p.data.strftime("%d/%m/%y") for p in pesagens],
               "datasets": [{"label": "Peso (kg)", "data": [float(p.peso_kg) for p in pesagens], "color": "#0E5E2A"}]}
    return render_template("animais/ficha.html", a=animal, variacoes=list(reversed(variacoes)), gmd=gmd(pesagens),
                           grafico=grafico, sanitarios=sanitarios, reprodutivos=reprodutivos, crias=crias,
                           financeiros=financeiros, especies=ESPECIES, sexos=SEXOS, origens=ORIGENS,
                           produtivas=SITUACOES_PRODUTIVAS, reprodutivas=SITUACOES_REPRODUTIVAS,
                           sanitarias=SITUACOES_SANITARIAS, status_opcoes=STATUS_ANIMAL, tipos_mov=TIPOS_MOVIMENTACAO,
                           qtd_pesagens=len(pesagens))


@bp.route("/<int:aid>/foto")
@propriedade_requerida("ver")
def foto(aid):
    from flask import abort, send_file

    from app.utils.uploads import caminho_absoluto

    animal = obter_da_propriedade(Animal, aid)
    if not animal.foto:
        abort(404)
    resp = send_file(caminho_absoluto(animal.foto))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@bp.route("/<int:aid>/editar", methods=["GET", "POST"])
@propriedade_requerida("editar")
def editar(aid):
    pid = g.propriedade.id
    animal = obter_da_propriedade(Animal, aid)
    v, e = valores_de(animal, CAMPOS), {}
    if request.method == "POST":
        form = Form(request.form)
        dados = _ler(form, pid, animal)
        if form.ok:
            lote_antes = animal.lote_id
            for k, val in dados.items():
                setattr(animal, k, val)
            _foto(form, animal)
            if form.ok:
                if lote_antes != animal.lote_id:
                    db.session.add(MovimentacaoAnimal(animal=animal, tipo="troca_lote", data=date.today(),
                                                      lote_origem_id=lote_antes, lote_destino_id=animal.lote_id,
                                                      registrado_por=current_user.id))
                auditar("animal.editado", f"Ficha do animal {animal.identificacao} atualizada", url_for("animais.ficha", aid=aid))
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    form.error("brinco", "Já existe um animal com esta identificação nesta propriedade.")
                else:
                    flash("Ficha atualizada.", "success")
                    return redirect(url_for("animais.ficha", aid=aid))
        v, e = form.values, form.errors
    return render_template("animais/form.html", v=v, e=e, **_form_ctx(pid, animal))


SAIDAS = {"venda": "vendido", "obito": "morto", "transferencia": "transferido", "arquivamento": "arquivado"}


@bp.route("/<int:aid>/movimentar", methods=["GET", "POST"])
@propriedade_requerida("editar")
def movimentar(aid):
    pid = g.propriedade.id
    animal = obter_da_propriedade(Animal, aid)
    if animal.ativo:
        opcoes = {k: TIPOS_MOVIMENTACAO[k] for k in ("troca_lote", "venda", "obito", "transferencia", "arquivamento")}
    else:
        opcoes = {"reativacao": TIPOS_MOVIMENTACAO["reativacao"]}
    v = {"tipo": request.args.get("tipo", next(iter(opcoes))), "data": date.today().isoformat()}
    e = {}
    lotes = {l.id: l.nome for l in svc.lotes_da_propriedade(pid)}
    if request.method == "POST":
        form = Form(request.form)
        tipo = form.choice("tipo", opcoes)
        data_mov = form.date("data", required=True, not_future=True, min_date=animal.data_entrada)
        observacao = form.text("observacao", max_len=2000)
        destino = form.text("destino", max_len=150)
        lote_destino = form.id_in("lote_destino_id", set(lotes), required=(tipo == "troca_lote"))
        valor = form.positive("valor", required=False) if tipo == "venda" else None
        gerar_receita = tipo == "venda" and valor and pode("financeiro") and form.boolean("gerar_receita")
        recebido = gerar_receita and form.boolean("recebido")
        if tipo == "troca_lote" and lote_destino == animal.lote_id:
            form.error("lote_destino_id", "O animal já está neste lote.")
        if form.ok:
            mov = MovimentacaoAnimal(animal=animal, tipo=tipo, data=data_mov, lote_origem_id=animal.lote_id,
                                     observacao=observacao, destino=destino, valor=valor, registrado_por=current_user.id)
            if tipo == "troca_lote":
                mov.lote_destino_id = lote_destino
                animal.lote_id = lote_destino
            elif tipo in SAIDAS:
                animal.status = SAIDAS[tipo]
                animal.data_saida = data_mov
            elif tipo == "reativacao":
                animal.status = "ativo"
                animal.data_saida = None
            db.session.add(mov)
            if gerar_receita:
                cat = svc.categoria_financeira(pid, "receita", "venda_animais")
                lanc = Lancamento(propriedade_id=pid, tipo="receita", descricao=f"Venda do animal {animal.identificacao}",
                                  categoria_id=cat.id, valor=valor, data=data_mov, animal_id=animal.id, lote_id=mov.lote_origem_id,
                                  contraparte=destino, origem="animal", registrado_por=current_user.id)
                if recebido:
                    lanc.pagamentos.append(Pagamento(data=data_mov, valor=valor, registrado_por=current_user.id))
                db.session.add(lanc)
                db.session.flush()
                mov.lancamento_id = lanc.id
            auditar("animal.movimentado", f"{TIPOS_MOVIMENTACAO[tipo]}: animal {animal.identificacao}",
                    url_for("animais.ficha", aid=aid))
            db.session.commit()
            flash("Movimentação registrada.", "success")
            return redirect(url_for("animais.ficha", aid=aid))
        v, e = form.values, form.errors
    return render_template("animais/movimentar.html", a=animal, opcoes=opcoes, lotes=lotes, v=v, e=e)


@bp.route("/categorias", methods=["GET", "POST"])
@propriedade_requerida("ver")
def categorias():
    pid = g.propriedade.id
    e, v = {}, {}
    if request.method == "POST":
        if not pode("editar"):
            from flask import abort

            abort(403)
        if request.form.get("remover"):
            cat = CategoriaAnimal.query.filter_by(id=request.form.get("remover", type=int), propriedade_id=pid).first_or_404()
            em_uso = Animal.query.filter_by(propriedade_id=pid, categoria=cat.nome).count()
            if em_uso:
                flash(f"A categoria está em uso por {em_uso} animal(is) e não pode ser removida.", "warning")
            else:
                db.session.delete(cat)
                db.session.commit()
                flash("Categoria removida.", "info")
            return redirect(url_for("animais.categorias"))
        form = Form(request.form)
        especie = form.choice("especie", ESPECIES)
        nome = form.text("nome", required=True, max_len=60)
        if nome and CategoriaAnimal.query.filter_by(propriedade_id=pid, especie=especie, nome=nome).first():
            form.error("nome", "Categoria já cadastrada.")
        if nome and nome in CATEGORIAS_PADRAO.get(especie or "", []):
            form.error("nome", "Esta categoria já faz parte da lista padrão.")
        if form.ok:
            db.session.add(CategoriaAnimal(propriedade_id=pid, especie=especie, nome=nome))
            db.session.commit()
            flash("Categoria adicionada.", "success")
            return redirect(url_for("animais.categorias"))
        v, e = form.values, form.errors
    extras = CategoriaAnimal.query.filter_by(propriedade_id=pid).order_by(CategoriaAnimal.especie, CategoriaAnimal.nome).all()
    return render_template("animais/categorias.html", extras=extras, padrao=CATEGORIAS_PADRAO, especies=ESPECIES, v=v, e=e)
