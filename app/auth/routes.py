import re
from datetime import timedelta

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.extensions import db
from app.models import LoginAttempt, PAPEIS_USUARIO, User, utcnow
from app.utils.email import email_configurado, enviar_email
from app.utils.navegacao import destino_seguro
from app.utils.registro import auditar
from app.utils.validacao import Form

bp = Blueprint("auth", __name__)


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def validar_senha(form, campo="senha", confirmar="confirmar"):
    senha = form.data.get(campo) or ""
    if len(senha) < 8:
        form.error(campo, "A senha deve ter pelo menos 8 caracteres.")
    elif not re.search(r"[A-Za-z]", senha) or not re.search(r"\d", senha):
        form.error(campo, "Use letras e números na senha.")
    elif len(senha) > 128:
        form.error(campo, "A senha deve ter no máximo 128 caracteres.")
    if confirmar and senha != (form.data.get(confirmar) or ""):
        form.error(confirmar, "As senhas não conferem.")
    return senha


def _bloqueado(email, ip):
    cfg = current_app.config
    limite = utcnow() - timedelta(minutes=cfg["LOGIN_WINDOW_MINUTES"])
    falhas = LoginAttempt.query.filter(
        LoginAttempt.sucesso.is_(False), LoginAttempt.created_at >= limite,
        db.or_(LoginAttempt.email == email, LoginAttempt.ip == ip),
    ).count()
    return falhas >= cfg["LOGIN_MAX_ATTEMPTS"]


def _enviar_verificacao(user):
    token = _serializer().dumps({"uid": user.id, "email": user.email}, salt="verificar-email")
    link = current_app.config["APP_BASE_URL"].rstrip("/") + url_for("auth.verificar_email", token=token)
    return enviar_email(user.email, "Confirme seu e-mail · Terra do Agro",
                        f"Olá, {user.primeiro_nome}.\n\nConfirme seu e-mail acessando o link abaixo (válido por 3 dias):\n{link}\n\n"
                        "Se você não criou uma conta no Terra do Agro, ignore esta mensagem.")


@bp.route("/entrar", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    v, e = {}, {}
    if request.method == "POST":
        form = Form(request.form)
        email = form.email("email")
        senha = request.form.get("senha") or ""
        v = form.values
        ip = request.remote_addr or "?"
        if not email or not senha:
            e = {"_geral": "Informe e-mail e senha."}
        elif _bloqueado(email, ip):
            e = {"_geral": f"Muitas tentativas sem sucesso. Aguarde {current_app.config['LOGIN_WINDOW_MINUTES']} minutos e tente novamente."}
        else:
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(senha):
                if not user.ativo:
                    e = {"_geral": "Esta conta está suspensa. Entre em contato com o suporte."}
                else:
                    db.session.add(LoginAttempt(email=email, ip=ip, sucesso=True))
                    user.ultimo_login = utcnow()
                    db.session.commit()
                    login_user(user, remember=form.boolean("lembrar"))
                    if not user.papeis:
                        return redirect(url_for("auth.escolher_perfil"))
                    return redirect(destino_seguro(request.args.get("next")))
            else:
                db.session.add(LoginAttempt(email=email, ip=ip, sucesso=False))
                db.session.commit()
                e = {"_geral": "E-mail ou senha incorretos."}
    return render_template("auth/login.html", v=v, e=e)


@bp.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    v, e = {}, {}
    if request.method == "POST":
        form = Form(request.form)
        nome = form.text("nome", required=True, max_len=120, min_len=3)
        email = form.email("email")
        senha = validar_senha(form)
        if not form.boolean("aceite"):
            form.error("aceite", "É necessário aceitar os termos de uso e a política de privacidade.")
        if email and User.query.filter_by(email=email).first():
            form.error("email", "Não foi possível usar este e-mail. Se ele já é seu, entre ou recupere a senha.")
        if form.ok:
            user = User(nome=nome, email=email, aceite_termos_em=utcnow())
            user.set_password(senha)
            db.session.add(user)
            db.session.flush()
            auditar("conta.criada", "Conta criada", user_id=user.id, propriedade_id=None)
            db.session.commit()
            _enviar_verificacao(user)
            login_user(user)
            flash("Conta criada. Agora escolha como você atua no agro.", "success")
            return redirect(url_for("auth.escolher_perfil"))
        v, e = form.values, form.errors
    return render_template("auth/cadastro.html", v=v, e=e)


@bp.route("/perfil-inicial", methods=["GET", "POST"])
@login_required
def escolher_perfil():
    e = {}
    if request.method == "POST":
        papeis = [p for p in request.form.getlist("papeis") if p in PAPEIS_USUARIO]
        if not papeis:
            e = {"papeis": "Escolha pelo menos uma opção."}
        else:
            for p in papeis:
                current_user.add_role(p)
            db.session.commit()
            flash("Perfil atualizado.", "success")
            if "produtor" in papeis and not current_user.propriedade_ativa_id:
                return redirect(url_for("propriedades.nova"))
            if "profissional" in papeis:
                return redirect(url_for("profissionais.editar_perfil"))
            if "trabalhador" in papeis:
                return redirect(url_for("vagas.meu_perfil"))
            if "empresa" in papeis:
                return redirect(url_for("conta.empresa"))
            return redirect(url_for("main.dashboard"))
    return render_template("auth/perfil_inicial.html", papeis=PAPEIS_USUARIO, atuais=current_user.lista_papeis, e=e)


@bp.route("/sair", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("Você saiu da sua conta.", "info")
    return redirect(url_for("auth.login", saiu=1))


@bp.route("/recuperar-senha", methods=["GET", "POST"])
def recuperar():
    v, e = {}, {}
    enviado = False
    if request.method == "POST":
        form = Form(request.form)
        email = form.email("email")
        if form.ok:
            user = User.query.filter_by(email=email).first()
            if user and user.ativo:
                token = _serializer().dumps({"uid": user.id, "v": user.session_version}, salt="redefinir-senha")
                link = current_app.config["APP_BASE_URL"].rstrip("/") + url_for("auth.redefinir", token=token)
                enviar_email(user.email, "Redefinição de senha · Terra do Agro",
                             f"Olá, {user.primeiro_nome}.\n\nRecebemos um pedido para redefinir sua senha. "
                             f"Use o link abaixo (válido por 1 hora e por um único uso):\n{link}\n\n"
                             "Se você não fez este pedido, ignore esta mensagem. Sua senha atual continua válida.")
            # A resposta é a mesma exista ou não a conta, para não revelar e-mails cadastrados.
            enviado = True
        v, e = form.values, form.errors
    return render_template("auth/recuperar.html", v=v, e=e, enviado=enviado, email_ok=email_configurado())


@bp.route("/redefinir-senha/<token>", methods=["GET", "POST"])
def redefinir(token):
    try:
        dados = _serializer().loads(token, salt="redefinir-senha", max_age=3600)
    except (SignatureExpired, BadSignature):
        flash("O link de redefinição é inválido ou expirou. Solicite um novo.", "danger")
        return redirect(url_for("auth.recuperar"))
    user = db.session.get(User, dados.get("uid"))
    if not user or user.session_version != dados.get("v"):
        flash("Este link já foi utilizado ou não é mais válido.", "danger")
        return redirect(url_for("auth.recuperar"))
    e = {}
    if request.method == "POST":
        form = Form(request.form)
        senha = validar_senha(form)
        if form.ok:
            user.set_password(senha)  # incrementa a versão e invalida o link e sessões antigas
            auditar("conta.senha_redefinida", "Senha redefinida por link de recuperação", user_id=user.id, propriedade_id=None)
            db.session.commit()
            flash("Senha alterada. Entre com a nova senha.", "success")
            return redirect(url_for("auth.login"))
        e = form.errors
    return render_template("auth/redefinir.html", e=e, v={})


@bp.route("/verificar-email/<token>")
def verificar_email(token):
    try:
        dados = _serializer().loads(token, salt="verificar-email", max_age=3 * 86400)
    except (SignatureExpired, BadSignature):
        flash("Link de confirmação inválido ou expirado.", "danger")
        return redirect(url_for("main.index"))
    user = db.session.get(User, dados.get("uid"))
    if user and user.email == dados.get("email"):
        user.email_verificado = True
        db.session.commit()
        flash("E-mail confirmado. Obrigado!", "success")
    return redirect(url_for("main.dashboard") if current_user.is_authenticated else url_for("auth.login"))


@bp.route("/reenviar-verificacao", methods=["POST"])
@login_required
def reenviar_verificacao():
    if current_user.email_verificado:
        flash("Seu e-mail já está confirmado.", "info")
    elif _enviar_verificacao(current_user):
        if email_configurado():
            flash("Enviamos um novo link de confirmação para o seu e-mail.", "success")
        else:
            flash("O envio de e-mail não está configurado neste servidor. A mensagem foi registrada na caixa de saída local do administrador.", "warning")
    else:
        flash("Não foi possível enviar o e-mail agora. Tente mais tarde.", "danger")
    return redirect(url_for("main.dashboard"))
