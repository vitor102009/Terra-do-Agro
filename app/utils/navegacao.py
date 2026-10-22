from flask import redirect, request, url_for


def destino_seguro(alvo, padrao="main.dashboard"):
    """Aceita apenas caminhos relativos do próprio site (evita redirecionamento aberto)."""
    if alvo and alvo.startswith("/") and not alvo.startswith("//") and "\\" not in alvo:
        return alvo
    return url_for(padrao)


def voltar(padrao="main.dashboard"):
    return redirect(destino_seguro(request.form.get("next") or request.args.get("next"), padrao))


def pagina():
    try:
        return max(1, int(request.args.get("page", 1)))
    except ValueError:
        return 1
