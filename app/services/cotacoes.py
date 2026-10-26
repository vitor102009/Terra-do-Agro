"""Cotações do agronegócio: coleta automática de fontes oficiais, normalização, estado e variação.

Arquitetura (a tela consulta a cada 3 s; as fontes, não):
1. Cada fonte automática tem um intervalo mínimo de coleta (`INTERVALOS`), registrado em `fontes_cotacao`.
2. A coleta acontece no máximo uma vez por intervalo, por qualquer requisição que encontre a fonte "vencida"
   (reserva atômica no banco, sem duas coletas simultâneas) ou pelo comando `flask cotacoes-atualizar`.
3. O endpoint JSON devolve apenas o que já está no banco. Um valor só muda quando a fonte publica outro.

Fontes:
- bcb: Banco Central do Brasil, API PTAX (Olinda). Dados abertos, sem chave. Boletins de abertura e
  intermediários (~10h, 11h, 12h e 13h) e fechamento, em dias úteis, com horário publicado pelo BCB.
- cepea: widget público do CEPEA/ESALQ. DESATIVADO por padrão (CEPEA_WIDGET_ENABLED): confirme os termos
  de uso com o CEPEA antes de ativar. Informa apenas a data (sem horário) e o nome do indicador.
- manual: cadastro ou importação CSV pelo administrador (fonte e data obrigatórias).
"""
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import or_, update

from app.extensions import db
from app.models import Cotacao, FonteCotacao, utcnow
from app.utils.formatos import datahora_br

USER_AGENT = "TerraDoAgro/1.0 (+cotacoes)"
TIMEOUT = 8

FONTES = {
    "bcb": {"nome": "Banco Central do Brasil — PTAX", "url": "https://www.bcb.gov.br/estabilidadefinanceira/historicocotacoes",
            "frequencia": "Boletins em dias úteis: abertura (~10h), intermediários (~11h e ~12h) e fechamento (~13h)"},
    "cepea": {"nome": "CEPEA/ESALQ-USP", "url": "https://www.cepea.org.br/",
              "frequencia": "Indicadores diários (dias úteis), publicados no fim da tarde"},
    "manual": {"nome": "Registro do administrador", "url": None, "frequencia": "Conforme cadastro"},
}
# Intervalo mínimo entre coletas de cada fonte (minutos).
INTERVALOS = {"bcb": 15, "cepea": 60}
# Idade a partir da qual o dado é exibido como desatualizado (dias corridos; cobre fins de semana e feriados).
IDADE_MAXIMA_DIAS = {"bcb": 4, "cepea": 5, "manual": 10}

MOEDAS_BCB = {"USD": "Dólar comercial", "EUR": "Euro"}
LADOS_PTAX = {"cotacaoVenda": "venda", "cotacaoCompra": "compra"}

CATEGORIAS = {
    "pecuaria": ("Pecuária", ("boi", "bezerr", "vaca", "garrote", "novilh", "arroba")),
    "leite": ("Leite", ("leite",)),
    "graos": ("Grãos e fibras", ("soja", "milho", "trigo", "arroz", "feij", "algod", "sorgo")),
    "cafe_acucar": ("Café, açúcar e etanol", ("café", "cafe", "açúcar", "acucar", "etanol")),
    "proteinas": ("Suínos e aves", ("suín", "suin", "frango", "ovo")),
    "insumos": ("Fertilizantes e insumos", ("ureia", "fertiliz", "kcl", "map", "dap", "potáss", "calcário", "diesel")),
    "moedas": ("Moedas", ("dólar", "dolar", "euro", "ptax")),
}


class FonteIndisponivel(Exception):
    pass


def categoria_de(produto, origem=None):
    if origem == "bcb":
        return "moedas"
    nome = (produto or "").lower()
    for chave, (_rotulo, termos) in CATEGORIAS.items():
        if any(t in nome for t in termos):
            return chave
    return "outros"


def rotulo_categoria(chave):
    return CATEGORIAS.get(chave, ("Outros", ()))[0]


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/javascript"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 - URLs fixas de fontes oficiais
        return resp.read().decode("utf-8", errors="replace")


# ------------------------------------------------------------------ adaptadores (retornam itens normalizados)

def _data_hora_bcb(texto):
    """'2026-10-20 13:09:36.24278' -> datetime. O BCB envia de 1 a 6 dígitos de fração de segundo."""
    base, _, frac = str(texto).strip().partition(".")
    quando = datetime.strptime(base, "%Y-%m-%d %H:%M:%S")
    if frac:
        if not frac.isdigit():
            raise ValueError("fração de segundo inválida")
        quando = quando.replace(microsecond=int(frac[:6].ljust(6, "0")))
    return quando


def _bcb(hoje=None, buscar=_get):
    hoje = hoje or date.today()
    inicio = hoje - timedelta(days=10)
    itens = []
    for moeda, produto in MOEDAS_BCB.items():
        url = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
               "CotacaoMoedaPeriodo(moeda=@moeda,dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?"
               + urllib.parse.urlencode({"@moeda": f"'{moeda}'", "@dataInicial": f"'{inicio:%m-%d-%Y}'",
                                         "@dataFinalCotacao": f"'{hoje:%m-%d-%Y}'", "$format": "json",
                                         "$select": "cotacaoCompra,cotacaoVenda,dataHoraCotacao,tipoBoletim"}))
        try:
            linhas = json.loads(buscar(url)).get("value")
        except ValueError as exc:
            raise FonteIndisponivel("Resposta inválida do Banco Central.") from exc
        if not isinstance(linhas, list):
            raise FonteIndisponivel("Resposta inesperada do Banco Central.")
        for linha in linhas:
            try:
                quando = _data_hora_bcb(linha["dataHoraCotacao"])
            except (KeyError, ValueError):
                continue
            boletim = str(linha.get("tipoBoletim") or "")[:20]
            for campo, lado in LADOS_PTAX.items():
                try:
                    valor = Decimal(str(linha[campo]))
                except (KeyError, InvalidOperation):
                    continue
                if not valor.is_finite() or valor <= 0:
                    continue
                # A venda mantém o identificador original ("bcb:USD") para preservar o histórico já gravado.
                serie = f"bcb:{moeda}" if lado == "venda" else f"bcb:{moeda}:compra"
                itens.append({"serie": serie, "ref_externa": f"{quando.isoformat()}|{boletim}",
                              "produto": f"{produto} (PTAX {lado})",
                              "praca": f"Brasil — taxa PTAX ({boletim.lower() or 'boletim'})", "uf": None, "valor": valor,
                              "unidade": f"R$ por 1 {moeda}", "moeda": "BRL", "data_referencia": quando.date(),
                              "publicado_em": quando})
    return itens


_CEPEA_LINHA = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*R\$\s*\|\s*([\d.]+,\d+)")


def _cepea_parse(js):
    texto = re.sub(r"<style.*?</style>", " ", js, flags=re.S)
    texto = html.unescape(re.sub(r"<[^>]+>", " | ", texto))
    texto = re.sub(r"(\s*\|\s*)+", " | ", texto)
    itens = []
    for dia, nome, unidade, valor in _CEPEA_LINHA.findall(texto):
        nome = nome.strip()
        uf = None
        m = re.search(r"\s-\s([A-Z]{2})$", nome)
        if m:
            uf = m.group(1)
        dt = datetime.strptime(dia, "%d/%m/%Y").date()
        itens.append({"serie": f"cepea:{nome.lower()}", "ref_externa": dt.isoformat(), "produto": f"{nome} (indicador CEPEA)",
                      # Sem UF no nome, a praça segue a metodologia do indicador: não presumimos abrangência nacional.
                      "praca": f"Estado de {uf}" if uf else "Praça definida na metodologia do indicador CEPEA",
                      "uf": uf, "valor": Decimal(valor.replace(".", "").replace(",", ".")),
                      "unidade": f"R$/{unidade.strip()}", "moeda": "BRL", "data_referencia": dt, "publicado_em": None})
    return itens


def _cepea(hoje=None, buscar=_get):
    ids = [i.strip() for i in (current_app.config.get("CEPEA_INDICADORES") or "").split(",") if i.strip().isdigit()]
    if not ids:
        return []
    url = "https://www.cepea.org.br/br/widgetproduto.js.php?" + urllib.parse.urlencode([("id_indicador[]", i) for i in ids])
    itens = _cepea_parse(buscar(url))
    if not itens:
        raise FonteIndisponivel("O widget do CEPEA não retornou indicadores reconhecíveis.")
    return itens


ADAPTADORES = {"bcb": _bcb, "cepea": _cepea}


def fontes_ativas():
    cfg = current_app.config
    ativas = []
    if cfg.get("QUOTES_BCB_ENABLED", True):
        ativas.append("bcb")
    if cfg.get("CEPEA_WIDGET_ENABLED"):
        ativas.append("cepea")
    return ativas


# ------------------------------------------------------------------ coleta com controle de frequência

FORCAR_MIN_SEGUNDOS = 60  # "Atualizar agora" consulta a fonte no máximo uma vez por minuto


def _reservar(slug, agora, forcar):
    """Reserva atômica da coleta: só uma requisição por intervalo chega à fonte externa."""
    fonte = db.session.get(FonteCotacao, slug)
    if fonte is None:
        db.session.add(FonteCotacao(slug=slug))
        db.session.commit()
    elif not forcar and fonte.proxima_em and fonte.proxima_em > agora:
        return False  # leitura apenas: a tela consulta a cada 3 s sem gerar escrita no banco
    elif forcar and fonte.ultima_tentativa and (agora - fonte.ultima_tentativa).total_seconds() < FORCAR_MIN_SEGUNDOS:
        return False
    proxima = agora + timedelta(minutes=INTERVALOS.get(slug, 60))
    cond = [FonteCotacao.slug == slug]
    if forcar:
        cond.append(or_(FonteCotacao.ultima_tentativa.is_(None),
                        FonteCotacao.ultima_tentativa <= agora - timedelta(seconds=FORCAR_MIN_SEGUNDOS)))
    else:
        cond.append(or_(FonteCotacao.proxima_em.is_(None), FonteCotacao.proxima_em <= agora))
    res = db.session.execute(update(FonteCotacao).where(*cond).values(proxima_em=proxima, ultima_tentativa=agora))
    db.session.commit()
    return res.rowcount == 1


def gravar(slug, itens, agora):
    novos = 0
    for item in itens:
        if Cotacao.query.filter_by(serie=item["serie"], ref_externa=item["ref_externa"]).first():
            continue
        db.session.add(Cotacao(origem=slug, fonte=FONTES[slug]["nome"], fonte_url=FONTES[slug]["url"],
                               coletado_em=agora, **item))
        novos += 1
    return novos


def coletar(slug, forcar=False, buscar=None):
    """Consulta a fonte se o intervalo mínimo passou. Retorna o número de cotações novas, ou None se não consultou."""
    agora = utcnow()
    if not _reservar(slug, agora, forcar):
        return None
    fonte = db.session.get(FonteCotacao, slug)
    try:
        kwargs = {"buscar": buscar} if buscar else {}
        itens = ADAPTADORES[slug](**kwargs)
        novos = gravar(slug, itens, agora)
        fonte.ultimo_sucesso, fonte.ultimo_erro, fonte.falhas_seguidas = agora, None, 0
        db.session.commit()
        return novos
    except Exception as exc:  # noqa: BLE001 - rede, tempo esgotado, formato alterado
        db.session.rollback()
        fonte = db.session.get(FonteCotacao, slug)
        current_app.logger.warning("Falha ao coletar cotações (%s): %s", slug, exc)
        fonte.falhas_seguidas = (fonte.falhas_seguidas or 0) + 1
        fonte.ultimo_erro = (str(exc) if isinstance(exc, FonteIndisponivel)
                             else "Fonte indisponível no momento (sem conexão ou tempo esgotado).")[:255]
        # Recuo progressivo após falhas seguidas, até 4x o intervalo normal.
        atraso = INTERVALOS.get(slug, 60) * min(4, fonte.falhas_seguidas)
        fonte.proxima_em = agora + timedelta(minutes=atraso)
        db.session.commit()
        return None


def atualizar_vencidas(forcar=False):
    """Coleta as fontes vencidas. Com forcar=True ("Atualizar agora"), no máximo uma vez por minuto por fonte."""
    for slug in fontes_ativas():
        coletar(slug, forcar=forcar)


# ------------------------------------------------------------------ leitura para tela, API e IA

def _estado(c, hoje, fonte=None):
    """(estado, motivo). Só é "atualizada" se o valor for recente E a fonte estiver respondendo."""
    if (hoje - c.data_referencia).days > IDADE_MAXIMA_DIAS.get(c.origem, 10):
        return "desatualizada", f"Último valor publicado em {c.data_referencia:%d/%m/%Y}."
    if fonte is not None and fonte.ultimo_erro:
        return "desatualizada", "Fonte indisponível no momento: este é o último valor válido obtido."
    return "atualizada", None


def _br(dt_utc):
    return datahora_br(dt_utc) if dt_utc else None


def _chave(c):
    return c.serie or f"manual:{c.produto}|{c.praca}|{c.uf or ''}"


def _num(v):
    return float(v) if v is not None else None


def serializar(c, anterior, hoje, fonte=None):
    var_abs = var_pct = None
    if anterior is not None and anterior.unidade == c.unidade and anterior.moeda == c.moeda:
        var_abs = c.valor - anterior.valor
        var_pct = (var_abs / anterior.valor * 100) if anterior.valor else None
    cat = categoria_de(c.produto, c.origem)
    estado, motivo = _estado(c, hoje, fonte)
    return {
        "id": c.id, "chave": _chave(c), "produto": c.produto, "categoria": cat, "categoria_rotulo": rotulo_categoria(cat),
        "praca": c.praca, "uf": c.uf, "valor": _num(c.valor), "unidade": c.unidade, "moeda": c.moeda,
        "fonte": c.fonte, "fonte_url": c.fonte_url, "origem": c.origem,
        "casas": 4 if c.origem == "bcb" else 2,
        "data_referencia": c.data_referencia.isoformat(), "data_br": c.data_referencia.strftime("%d/%m/%Y"),
        "publicado_em": c.publicado_em.isoformat(timespec="seconds") if c.publicado_em else None,
        "publicado_br": c.publicado_em.strftime("%d/%m/%Y %H:%M") if c.publicado_em else None,
        "fuso_publicacao": "Horário de Brasília" if c.origem == "bcb" else None,
        "coletado_em": (c.coletado_em.isoformat(timespec="seconds") + "Z") if c.coletado_em else None,
        "estado": estado, "motivo_estado": motivo,
        # Última consulta bem-sucedida à fonte (Brasília). Registro manual: quando foi cadastrado.
        "consultado_br": _br(fonte.ultimo_sucesso) if fonte is not None else _br(c.created_at),
        "variacao": _num(var_abs), "variacao_pct": _num(var_pct),
        "comparado_com": ({"data_referencia": anterior.data_referencia.isoformat(),
                           "data_br": anterior.data_referencia.strftime("%d/%m/%Y"), "valor": _num(anterior.valor)}
                          if var_abs is not None else None),
    }


def recentes(filtros=None, hoje=None):
    """Última cotação de cada série, com variação em relação ao último valor de um dia anterior."""
    filtros = filtros or {}
    hoje = hoje or date.today()
    q = Cotacao.query
    if filtros.get("fonte"):
        q = q.filter(Cotacao.origem == filtros["fonte"])
    if filtros.get("uf"):
        q = q.filter(Cotacao.uf == filtros["uf"])
    if filtros.get("produto"):
        q = q.filter(Cotacao.produto.ilike(f"%{filtros['produto'][:60]}%"))
    linhas = q.order_by(Cotacao.data_referencia.desc(), Cotacao.publicado_em.desc(), Cotacao.id.desc()).limit(5000).all()
    ultimas, anteriores = {}, {}
    for c in linhas:
        k = _chave(c)
        if k not in ultimas:
            ultimas[k] = c
        elif k not in anteriores and c.data_referencia < ultimas[k].data_referencia:
            anteriores[k] = c
    fontes = {f.slug: f for f in FonteCotacao.query.all()}
    itens = [serializar(c, anteriores.get(k), hoje, fontes.get(c.origem)) for k, c in ultimas.items()]
    if filtros.get("categoria"):
        itens = [i for i in itens if i["categoria"] == filtros["categoria"]]
    return sorted(itens, key=lambda i: (i["categoria_rotulo"], i["produto"], i["praca"]))


def historico(chave, dias=90, hoje=None):
    """Último valor de cada dia da série, para gráfico e cálculo de variação no período."""
    hoje = hoje or date.today()
    inicio = hoje - timedelta(days=dias)
    if chave.startswith("manual:"):
        produto, praca, uf = (chave[7:].split("|") + ["", ""])[:3]
        q = Cotacao.query.filter_by(origem="manual", produto=produto, praca=praca, uf=uf or None)
    else:
        q = Cotacao.query.filter_by(serie=chave)
    linhas = (q.filter(Cotacao.data_referencia >= inicio)
              .order_by(Cotacao.data_referencia, Cotacao.publicado_em, Cotacao.id).all())
    por_dia = {}
    for c in linhas:
        por_dia[c.data_referencia] = c
    serie = [por_dia[d] for d in sorted(por_dia)]
    resultado = {"chave": chave, "dias": dias, "pontos": [{"data": c.data_referencia.isoformat(), "valor": _num(c.valor)} for c in serie]}
    if len(serie) >= 2 and serie[0].valor:
        resultado["variacao_periodo"] = {"de": serie[0].data_referencia.isoformat(), "ate": serie[-1].data_referencia.isoformat(),
                                         "valor_inicial": _num(serie[0].valor), "valor_final": _num(serie[-1].valor),
                                         "variacao": _num(serie[-1].valor - serie[0].valor),
                                         "variacao_pct": _num((serie[-1].valor - serie[0].valor) / serie[0].valor * 100)}
    if serie:
        resultado.update({"produto": serie[-1].produto, "unidade": serie[-1].unidade, "fonte": serie[-1].fonte})
    return resultado


SEM_FONTE = {
    "pecuaria": "O CEPEA/ESALQ publica boi gordo e bezerro sob licença CC BY-NC 4.0 (uso não comercial). A integração "
                "está pronta, mas desativada até haver autorização do CEPEA para uso nesta plataforma.",
    "leite": "Não há fonte pública e liberada de preço do leite conectada.",
    "graos": "A CONAB publica preços de grãos, mas o portal de dados dela apresenta certificado HTTPS inválido, e os "
             "indicadores do CEPEA exigem autorização para uso comercial. Nenhuma fonte automática está ativa.",
    "cafe_acucar": "Os indicadores do CEPEA exigem autorização para uso comercial, e as bolsas exigem licença.",
    "proteinas": "Não há fonte pública e liberada de suínos e frango conectada.",
    "insumos": "Não há fonte pública e liberada de fertilizantes e insumos conectada.",
    "moedas": "O Banco Central ainda não respondeu. Tente atualizar em instantes.",
}


def mensagem_sem_dados(filtros):
    cat = (filtros or {}).get("categoria")
    if cat in SEM_FONTE:
        extra = "" if cat == "moedas" else " O administrador pode registrar cotações com fonte e data."
        return f"Nenhuma cotação de {rotulo_categoria(cat).lower()} disponível. {SEM_FONTE[cat]}{extra}"
    if filtros:
        return "Nenhuma cotação encontrada para estes filtros."
    return "Nenhuma cotação disponível ainda. As fontes automáticas aparecem assim que responderem."


def estado_fontes():
    ativas = fontes_ativas()
    out = []
    for slug in ("bcb", "cepea"):
        f = db.session.get(FonteCotacao, slug)
        out.append({"slug": slug, "nome": FONTES[slug]["nome"], "ativa": slug in ativas,
                    "frequencia_fonte": FONTES[slug]["frequencia"], "intervalo_coleta_min": INTERVALOS[slug],
                    "ultimo_sucesso": (f.ultimo_sucesso.isoformat(timespec="seconds") + "Z") if f and f.ultimo_sucesso else None,
                    "ultimo_sucesso_br": _br(f.ultimo_sucesso) if f else None,
                    "ultima_tentativa_br": _br(f.ultima_tentativa) if f else None,
                    "observacao": ("Licença CC BY-NC 4.0: uso comercial depende de autorização do CEPEA."
                                   if slug == "cepea" else None),
                    "ultimo_erro": f.ultimo_erro if f else None,
                    "estado": ("desativada" if slug not in ativas else
                               "indisponivel" if f and f.ultimo_erro else
                               "ok" if f and f.ultimo_sucesso else "aguardando")})
    return out
