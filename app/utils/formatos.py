"""Filtros de template para o padrão brasileiro."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from app.models.base import utcnow

# O Brasil (horário de Brasília) não adota horário de verão desde 2019.
FUSO_BRASILIA = timedelta(hours=-3)


def moeda(valor):
    if valor is None:
        return "—"
    v = Decimal(valor).quantize(Decimal("0.01"))
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-R$ " if v < 0 else "R$ ") + s


def numero(valor, casas=0):
    if valor is None:
        return "—"
    s = f"{float(valor):,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def data_br(valor):
    if not valor:
        return "—"
    if isinstance(valor, datetime):
        valor = (valor + FUSO_BRASILIA).date()
    return valor.strftime("%d/%m/%Y")


def datahora_br(valor):
    if not valor:
        return "—"
    return (valor + FUSO_BRASILIA).strftime("%d/%m/%Y %H:%M")


def hora_br(valor):
    if not valor:
        return ""
    return (valor + FUSO_BRASILIA).strftime("%H:%M")


def tempo_relativo(valor):
    if not valor:
        return ""
    delta = utcnow() - valor
    seg = int(delta.total_seconds())
    if seg < 60:
        return "agora"
    if seg < 3600:
        return f"há {seg // 60} min"
    if seg < 86400:
        return f"há {seg // 3600} h"
    if seg < 86400 * 7:
        dias = seg // 86400
        return f"há {dias} dia" + ("s" if dias > 1 else "")
    return data_br(valor)


def hoje_br():
    return (utcnow() + FUSO_BRASILIA).date()


def idade(data_nasc, referencia=None):
    if not data_nasc:
        return "—"
    referencia = referencia or date.today()
    meses = (referencia.year - data_nasc.year) * 12 + referencia.month - data_nasc.month
    if referencia.day < data_nasc.day:
        meses -= 1
    if meses < 0:
        return "—"
    if meses < 24:
        return f"{meses} {'mês' if meses == 1 else 'meses'}"
    anos, resto = divmod(meses, 12)
    return f"{anos} anos" + (f" e {resto} m" if resto else "")


def rotulos(chaves, mapa):
    """Converte uma lista de chaves nos rótulos correspondentes de um dicionário."""
    return [mapa.get(c, c) for c in (chaves or [])]


def registrar_filtros(app):
    app.jinja_env.filters.update(
        moeda=moeda, numero=numero, data_br=data_br, datahora_br=datahora_br, hora_br=hora_br,
        tempo_relativo=tempo_relativo, idade=idade, rotulos=rotulos,
    )
