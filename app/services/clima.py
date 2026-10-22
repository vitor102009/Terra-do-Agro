"""Previsão do tempo via Open-Meteo (https://open-meteo.com): serviço público, gratuito e sem chave de API.

Os dados são mantidos em cache no banco (tabela clima_cache). Quando a consulta falha, devolvemos o último
dado válido com a data de obtenção. Nunca geramos previsões próprias.
"""
import json
import unicodedata
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from flask import current_app

from app.extensions import db
from app.models import ClimaCache, utcnow
from app.utils.formatos import datahora_br

FONTE = "Open-Meteo (open-meteo.com)"
UF_NOMES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
    "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco", "PI": "Piauí",
    "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
    "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins",
}

# Códigos meteorológicos WMO usados pelo Open-Meteo.
CODIGOS = {
    0: ("Céu limpo", "bi-sun"), 1: ("Predominantemente limpo", "bi-sun"), 2: ("Parcialmente nublado", "bi-cloud-sun"),
    3: ("Nublado", "bi-cloud"), 45: ("Neblina", "bi-cloud-fog"), 48: ("Neblina com geada", "bi-cloud-fog"),
    51: ("Garoa fraca", "bi-cloud-drizzle"), 53: ("Garoa", "bi-cloud-drizzle"), 55: ("Garoa forte", "bi-cloud-drizzle"),
    61: ("Chuva fraca", "bi-cloud-rain"), 63: ("Chuva", "bi-cloud-rain"), 65: ("Chuva forte", "bi-cloud-rain-heavy"),
    66: ("Chuva congelante", "bi-cloud-sleet"), 67: ("Chuva congelante forte", "bi-cloud-sleet"),
    71: ("Neve fraca", "bi-cloud-snow"), 73: ("Neve", "bi-cloud-snow"), 75: ("Neve forte", "bi-cloud-snow"),
    80: ("Pancadas de chuva", "bi-cloud-rain"), 81: ("Pancadas de chuva", "bi-cloud-rain-heavy"),
    82: ("Pancadas fortes de chuva", "bi-cloud-rain-heavy"), 95: ("Trovoadas", "bi-cloud-lightning-rain"),
    96: ("Trovoadas com granizo", "bi-cloud-hail"), 99: ("Trovoadas com granizo", "bi-cloud-hail"),
}
DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


class ClimaIndisponivel(Exception):
    pass


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TerraDoAgro/1.0"})
    with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310 - URL fixa de serviço público
        return json.loads(resp.read().decode("utf-8"))


def _cache_get(chave):
    return ClimaCache.query.filter_by(chave=chave).first()


def _cache_set(chave, payload):
    c = _cache_get(chave) or ClimaCache(chave=chave)
    c.payload = json.dumps(payload)
    c.fonte = FONTE
    c.obtido_em = utcnow()
    db.session.add(c)
    db.session.commit()
    return c


def geocodificar(municipio, uf):
    chave = f"geo:{_sem_acento(municipio)}:{uf}"
    c = _cache_get(chave)
    if c:
        return json.loads(c.payload)
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": municipio, "count": 10, "language": "pt", "format": "json", "countryCode": "BR"})
    dados = _get_json(url)
    estado = _sem_acento(UF_NOMES.get(uf, ""))
    for r in dados.get("results", []) or []:
        if _sem_acento(r.get("admin1")) == estado and _sem_acento(r.get("name")) == _sem_acento(municipio):
            loc = {"lat": r["latitude"], "lon": r["longitude"], "nome": f"{r['name']}/{uf}"}
            _cache_set(chave, loc)
            return loc
    raise ClimaIndisponivel(f"Município {municipio}/{uf} não encontrado no serviço de geolocalização.")


def _montar(bruto, local, obtido_em, cache_antigo):
    atual = bruto.get("current", {})
    desc, icone = CODIGOS.get(atual.get("weather_code"), ("Condição não informada", "bi-cloud"))
    d = bruto.get("daily", {})
    dias = []
    for i, dia in enumerate(d.get("time", [])):
        dt = date.fromisoformat(dia)
        ddesc, dicone = CODIGOS.get(d["weather_code"][i], ("—", "bi-cloud"))
        dias.append({"data": dia, "rotulo": "Hoje" if dt == date.today() else f"{DIAS[dt.weekday()]} {dt.day:02d}/{dt.month:02d}",
                     "max": d["temperature_2m_max"][i], "min": d["temperature_2m_min"][i],
                     "chuva_mm": d["precipitation_sum"][i] or 0, "prob": (d.get("precipitation_probability_max") or [None] * 99)[i],
                     "descricao": ddesc, "icone": dicone})
    return {"ok": True, "local": local, "fonte": FONTE, "atualizado_em": datahora_br(obtido_em), "cache_antigo": cache_antigo,
            "atual": {"temperatura": atual.get("temperature_2m"), "umidade": atual.get("relative_humidity_2m"),
                      "vento": atual.get("wind_speed_10m") or 0, "precipitacao": atual.get("precipitation"),
                      "descricao": desc, "icone": icone},
            "dias": dias}


def previsao(prop=None, municipio=None, uf=None):
    if not current_app.config.get("WEATHER_ENABLED"):
        return {"ok": False, "mensagem": "A previsão do tempo está desativada neste servidor (WEATHER_ENABLED=false)."}
    if prop:
        municipio, uf = prop.municipio, prop.uf
    if not municipio or not uf:
        return {"ok": False, "mensagem": "Informe o município da propriedade ou do seu perfil para ver a previsão."}
    chave = f"prev:{prop.id}" if prop and prop.latitude is not None else f"prev:{_sem_acento(municipio)}:{uf}"
    cache = _cache_get(chave)
    validade = timedelta(minutes=current_app.config.get("WEATHER_CACHE_MINUTES", 60))
    if cache and utcnow() - cache.obtido_em < validade:
        dados = json.loads(cache.payload)
        return _montar(dados["bruto"], dados["local"], cache.obtido_em, False)
    try:
        if prop and prop.latitude is not None and prop.longitude is not None:
            loc = {"lat": prop.latitude, "lon": prop.longitude, "nome": f"{prop.nome} ({municipio}/{uf})"}
        else:
            loc = geocodificar(municipio, uf)
        url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
            "latitude": loc["lat"], "longitude": loc["lon"],
            "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
            "timezone": "America/Sao_Paulo", "forecast_days": 7,
        })
        bruto = _get_json(url)
        if "current" not in bruto or "daily" not in bruto:
            raise ClimaIndisponivel("Resposta incompleta do serviço de previsão.")
        c = _cache_set(chave, {"bruto": bruto, "local": loc["nome"]})
        return _montar(bruto, loc["nome"], c.obtido_em, False)
    except ClimaIndisponivel as exc:
        motivo = str(exc)
    except Exception as exc:  # noqa: BLE001 - rede, tempo esgotado, JSON inválido
        current_app.logger.warning("Falha ao consultar previsão do tempo: %s", exc)
        motivo = "Não foi possível consultar o serviço de previsão agora (sem conexão ou serviço indisponível)."
    db.session.rollback()
    if cache:
        dados = json.loads(cache.payload)
        return _montar(dados["bruto"], dados["local"], cache.obtido_em, True)
    return {"ok": False, "mensagem": motivo}


def _parse_dt(s):
    return datetime.fromisoformat(s)
