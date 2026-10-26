import json
import os

from flask import Blueprint, abort, flash, g, make_response, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import (
    Animal, Anuncio, Avaliacao, Comentario, Denuncia, Grupo, Mensagem, PerfilProfissional, Post, User, Vaga,
)
from app.models.sistema import ALVOS_DENUNCIA, MOTIVOS_DENUNCIA
from app.models.user import PAPEIS_USUARIO
from app.services import cotacoes as servico_cotacoes
from app.services import indicadores
from app.services.alertas import gerar_alertas
from app.utils.acesso import pode
from app.utils.navegacao import destino_seguro
from app.utils.uploads import UploadError, caminho_absoluto
from app.utils.validacao import Form

bp = Blueprint("main", __name__)

# Versão dos arquivos estáticos usada no cache do Service Worker. Altere ao publicar mudanças no frontend.
ASSET_VERSION = "2026.10.26-2"

ARQUIVOS_OFFLINE = [
    "/offline",
    "/app-offline",
    "/static/vendor/bootstrap/bootstrap.min.css",
    "/static/vendor/bootstrap/bootstrap.bundle.min.js",
    "/static/vendor/bootstrap-icons/bootstrap-icons.min.css",
    "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
    "/static/vendor/bootstrap-icons/fonts/bootstrap-icons.woff",
    "/static/vendor/chartjs/chart.umd.min.js",
    "/static/css/terra.css",
    "/static/js/app.js",
    "/static/js/offline-store.js",
    "/static/js/offline-page.js",
    "/static/js/charts.js",
    "/static/img/logo-mark.svg",
    "/static/img/campo.svg",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/favicon-32.png",
    "/static/icons/apple-touch-icon.png",
]


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return render_template("main/landing.html")


@bp.route("/painel")
@login_required
def dashboard():
    if not current_user.papeis:
        return redirect(url_for("auth.escolher_perfil"))
    gerar_alertas(current_user)
    chave = request.args.get("periodo", "mes")
    if chave not in indicadores.PERIODOS:
        chave = "mes"
    dados = None
    if g.propriedade:
        dados = indicadores.resumo_dashboard(g.propriedade, chave, pode("financeiro"))
    cotacoes = servico_cotacoes.recentes()[:5]
    return render_template("main/dashboard.html", d=dados, periodo=chave, periodos=indicadores.PERIODOS,
                           cotacoes=cotacoes, papeis=PAPEIS_USUARIO)


@bp.route("/busca")
@login_required
def busca():
    q = (request.args.get("q") or "").strip()[:80]
    res = {}
    if len(q) >= 2:
        termo = f"%{q}%"
        if g.propriedade:
            res["animais"] = (Animal.query.filter(Animal.propriedade_id == g.propriedade.id,
                                                  db.or_(Animal.brinco.ilike(termo), Animal.nome.ilike(termo)))
                              .order_by(Animal.status, Animal.brinco).limit(20).all())
        res["anuncios"] = (Anuncio.query.filter(Anuncio.status == "ativo",
                                                db.or_(Anuncio.titulo.ilike(termo), Anuncio.descricao.ilike(termo)))
                           .order_by(Anuncio.created_at.desc()).limit(12).all())
        res["profissionais"] = (PerfilProfissional.query.join(User, PerfilProfissional.user_id == User.id)
                                .filter(PerfilProfissional.publico.is_(True), User.ativo.is_(True),
                                        db.or_(PerfilProfissional.titulo.ilike(termo), User.nome.ilike(termo),
                                               PerfilProfissional.especialidades.ilike(termo)))
                                .limit(12).all())
        res["vagas"] = (Vaga.query.filter(Vaga.status == "aberta", db.or_(Vaga.cargo.ilike(termo), Vaga.descricao.ilike(termo)))
                        .limit(12).all())
        res["grupos"] = Grupo.query.filter(Grupo.ativo.is_(True), Grupo.nome.ilike(termo)).limit(8).all()
    return render_template("main/busca.html", q=q, res=res)


@bp.route("/privacidade")
def privacidade():
    return render_template("main/privacidade.html")


@bp.route("/termos")
def termos():
    return render_template("main/termos.html")


@bp.route("/media/<path:caminho>")
def media(caminho):
    """Serve somente arquivos públicos (fotos de anúncios, perfis e publicações)."""
    if not caminho.startswith("public/"):
        abort(404)
    try:
        full = caminho_absoluto(caminho)
    except UploadError:
        abort(404)
    if not os.path.isfile(full):
        abort(404)
    resp = send_file(full, conditional=True, max_age=86400)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


ALVOS_MODELOS = {
    "anuncio": Anuncio, "post": Post, "comentario": Comentario, "usuario": User, "grupo": Grupo,
    "perfil": PerfilProfissional, "vaga": Vaga, "mensagem": Mensagem, "avaliacao": Avaliacao,
}


@bp.route("/denunciar/<tipo>/<int:alvo_id>", methods=["GET", "POST"])
@login_required
def denunciar(tipo, alvo_id):
    modelo = ALVOS_MODELOS.get(tipo)
    if not modelo:
        abort(404)
    alvo = db.session.get(modelo, alvo_id)
    if alvo is None:
        abort(404)
    if tipo == "mensagem" and not alvo.conversa.participa(current_user.id):
        abort(404)
    v, e = {}, {}
    if request.method == "POST":
        form = Form(request.form)
        motivo = form.choice("motivo", MOTIVOS_DENUNCIA)
        descricao = form.text("descricao", max_len=2000)
        if form.ok:
            ja = Denuncia.query.filter_by(autor_id=current_user.id, alvo_tipo=tipo, alvo_id=alvo_id, status="aberta").first()
            if not ja:
                db.session.add(Denuncia(autor_id=current_user.id, alvo_tipo=tipo, alvo_id=alvo_id, motivo=motivo,
                                        descricao=descricao))
                db.session.commit()
            flash("Denúncia enviada. A equipe de moderação vai analisar.", "success")
            return redirect(destino_seguro(request.args.get("next")))
        v, e = form.values, form.errors
    return render_template("main/denunciar.html", tipo=tipo, tipo_label=ALVOS_DENUNCIA.get(tipo), alvo_id=alvo_id,
                           motivos=MOTIVOS_DENUNCIA, v=v, e=e)


# ------------------------------------------------------------------ PWA

@bp.route("/manifest.webmanifest")
def pwa_manifest():
    icone = lambda nome: url_for("static", filename=f"icons/{nome}")  # noqa: E731
    manifest = {
        "id": "/",
        "name": "Terra do Agro",
        "short_name": "Terra do Agro",
        "description": "A Revolução Digital do Agro Brasileiro: gestão rural, marketplace, profissionais e comunidade.",
        "lang": "pt-BR",
        "dir": "ltr",
        "start_url": "/painel?origem=pwa",
        "scope": "/",
        "display": "standalone",
        "display_override": ["standalone", "minimal-ui"],
        "orientation": "any",
        "background_color": "#073817",
        "theme_color": "#073817",
        "categories": ["business", "productivity", "utilities"],
        "icons": [
            {"src": icone("icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": icone("icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": icone("maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Registrar pesagem", "url": "/pesagens/nova", "icons": [{"src": icone("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Rebanho", "url": "/rebanho", "icons": [{"src": icone("icon-192.png"), "sizes": "192x192"}]},
            {"name": "Modo offline", "url": "/app-offline", "icons": [{"src": icone("icon-192.png"), "sizes": "192x192"}]},
        ],
    }
    resp = make_response(json.dumps(manifest, ensure_ascii=False))
    resp.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/service-worker.js")
def pwa_service_worker():
    js = render_template("pwa/service-worker.js", versao=ASSET_VERSION, arquivos=json.dumps(ARQUIVOS_OFFLINE))
    resp = make_response(js)
    resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@bp.route("/offline")
def pwa_offline():
    # Página genérica, sem dados do usuário, mantida em cache pelo Service Worker.
    resp = make_response(render_template("errors/offline.html"))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/app-offline")
def pwa_app_offline():
    # Casca estática do modo offline: os dados vêm do IndexedDB do próprio aparelho, nunca do HTML.
    resp = make_response(render_template("pwa/app_offline.html", versao=ASSET_VERSION))
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/ping")
def pwa_health():
    """Verificação simples usada pelo aplicativo para confirmar que o servidor responde."""
    resp = make_response({"ok": True, "versao": ASSET_VERSION})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.app_context_processor
def versao_assets():
    return {"asset_version": ASSET_VERSION}
