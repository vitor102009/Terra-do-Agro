"""Validação de formulários no backend.

Cada método lê um campo, registra o valor bruto (para reexibir o formulário) e,
quando houver problema, uma mensagem de erro em português.
"""
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB",
    "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
]

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def parse_decimal(raw):
    v = (raw or "").strip().replace("R$", "").replace(" ", "")
    if not v:
        return None
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    return Decimal(v)


class Form:
    def __init__(self, data, files=None):
        self.data = data
        self.files = files
        self.errors = {}
        self.values = {}

    @property
    def ok(self):
        return not self.errors

    def error(self, name, msg):
        self.errors.setdefault(name, msg)

    def _raw(self, name):
        raw = self.data.get(name)
        raw = raw.strip() if isinstance(raw, str) else raw
        self.values[name] = raw if raw is not None else ""
        return raw

    def text(self, name, required=False, max_len=255, min_len=0, label=None):
        raw = self._raw(name) or ""
        if required and not raw:
            self.error(name, "Campo obrigatório.")
            return None
        if raw and len(raw) < min_len:
            self.error(name, f"Informe ao menos {min_len} caracteres.")
        if max_len and len(raw) > max_len:
            self.error(name, f"Use no máximo {max_len} caracteres.")
        return raw or None

    def email(self, name, required=True):
        raw = (self._raw(name) or "").lower()
        self.values[name] = raw
        if not raw:
            if required:
                self.error(name, "Informe o e-mail.")
            return None
        if len(raw) > 180 or not EMAIL_RE.match(raw):
            self.error(name, "E-mail inválido.")
        return raw

    def choice(self, name, choices, required=True, default=None):
        raw = self._raw(name)
        if not raw:
            if required and default is None:
                self.error(name, "Selecione uma opção.")
            return default
        if raw not in choices:
            self.error(name, "Opção inválida.")
            return default
        return raw

    def multi(self, name, choices):
        raws = self.data.getlist(name) if hasattr(self.data, "getlist") else (self.data.get(name) or [])
        valid = [r for r in raws if r in choices]
        self.values[name] = valid
        return valid

    def uf(self, name, required=True):
        raw = (self._raw(name) or "").upper()
        self.values[name] = raw
        if not raw:
            if required:
                self.error(name, "Selecione o estado.")
            return None
        if raw not in UFS:
            self.error(name, "Estado inválido.")
            return None
        return raw

    def decimal(self, name, required=False, min_value=None, max_value=None):
        raw = self._raw(name)
        if not raw:
            if required:
                self.error(name, "Campo obrigatório.")
            return None
        try:
            value = parse_decimal(raw)
        except (InvalidOperation, ValueError):
            self.error(name, "Número inválido.")
            return None
        if value is None or not value.is_finite():
            self.error(name, "Número inválido.")
            return None
        if min_value is not None and value < Decimal(str(min_value)):
            self.error(name, f"O valor deve ser maior ou igual a {min_value}.")
        if max_value is not None and value > Decimal(str(max_value)):
            self.error(name, f"O valor deve ser menor ou igual a {max_value}.")
        return value

    def positive(self, name, required=True, max_value=None):
        value = self.decimal(name, required=required, max_value=max_value)
        if value is not None and value <= 0:
            self.error(name, "O valor deve ser maior que zero.")
        return value

    def integer(self, name, required=False, min_value=None, max_value=None):
        raw = self._raw(name)
        if not raw:
            if required:
                self.error(name, "Campo obrigatório.")
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.error(name, "Número inteiro inválido.")
            return None
        if min_value is not None and value < min_value:
            self.error(name, f"O valor mínimo é {min_value}.")
        if max_value is not None and value > max_value:
            self.error(name, f"O valor máximo é {max_value}.")
        return value

    def date(self, name, required=False, not_future=False, min_date=None):
        raw = self._raw(name)
        if not raw:
            if required:
                self.error(name, "Informe a data.")
            return None
        try:
            value = datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            self.error(name, "Data inválida.")
            return None
        if value.year < 1900 or value.year > 2200:
            self.error(name, "Data fora do intervalo permitido.")
            return None
        if not_future and value > date.today():
            self.error(name, "A data não pode estar no futuro.")
        if min_date and value < min_date:
            self.error(name, f"A data deve ser igual ou posterior a {min_date.strftime('%d/%m/%Y')}.")
        return value

    def time(self, name, required=False):
        raw = self._raw(name)
        if not raw:
            if required:
                self.error(name, "Informe o horário.")
            return None
        try:
            return datetime.strptime(raw[:5], "%H:%M").time()
        except ValueError:
            self.error(name, "Horário inválido.")
            return None

    def boolean(self, name):
        raw = self.data.get(name)
        value = raw in ("1", "on", "true", "sim", True)
        self.values[name] = value
        return value

    def id_in(self, name, valid_ids, required=False):
        """Lê um id e confere se pertence ao conjunto permitido (ex.: lotes da propriedade)."""
        raw = self._raw(name)
        if not raw:
            if required:
                self.error(name, "Selecione uma opção.")
            return None
        try:
            value = int(raw)
        except ValueError:
            self.error(name, "Opção inválida.")
            return None
        if value not in valid_ids:
            self.error(name, "Opção inválida.")
            return None
        return value


def valores_de(obj, campos):
    """Converte atributos de um objeto em valores para preencher formulários de edição."""
    out = {}
    for campo in campos:
        val = getattr(obj, campo, None)
        if val is None:
            out[campo] = ""
        elif isinstance(val, bool):
            out[campo] = val
        elif isinstance(val, (date, datetime)) and not isinstance(val, time):
            out[campo] = val.strftime("%Y-%m-%d")
        elif isinstance(val, time):
            out[campo] = val.strftime("%H:%M")
        elif isinstance(val, Decimal):
            out[campo] = format(val.normalize(), "f") if val == val.to_integral() else str(val)
        else:
            out[campo] = val
    return out
