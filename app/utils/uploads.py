"""Upload seguro: valida tipo pelo conteúdo (assinatura), extensão e tamanho."""
import os
import uuid

from flask import current_app
from werkzeug.utils import secure_filename

# Assinaturas (magic numbers) aceitas por tipo de arquivo.
ASSINATURAS = {
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "webp": [b"RIFF"],
    "gif": [b"GIF87a", b"GIF89a"],
    "pdf": [b"%PDF-"],
    "mp4": [],  # verificado pelo box 'ftyp'
    "webm": [b"\x1a\x45\xdf\xa3"],
    "mov": [],
}

TIPOS = {
    "imagem": {"ext": {"jpg", "jpeg", "png", "webp", "gif"}, "limite": "MAX_IMAGE_BYTES"},
    "video": {"ext": {"mp4", "webm", "mov"}, "limite": "MAX_VIDEO_BYTES"},
    "documento": {"ext": {"pdf", "jpg", "jpeg", "png"}, "limite": "MAX_DOC_BYTES"},
}


class UploadError(ValueError):
    pass


def _confere_assinatura(ext, cabecalho):
    if ext in ("mp4", "mov"):
        return cabecalho[4:8] == b"ftyp"
    if ext == "webp":
        return cabecalho.startswith(b"RIFF") and cabecalho[8:12] == b"WEBP"
    return any(cabecalho.startswith(sig) for sig in ASSINATURAS.get(ext, []))


def tem_arquivo(storage):
    return storage is not None and getattr(storage, "filename", "")


def salvar_upload(storage, tipo, pasta, privado=False):
    """Salva o arquivo e retorna o caminho relativo (ex.: 'public/anuncios/abc.jpg')."""
    if not tem_arquivo(storage):
        return None
    regra = TIPOS[tipo]
    nome = secure_filename(storage.filename or "")
    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if ext not in regra["ext"]:
        raise UploadError("Formato não permitido. Aceitos: " + ", ".join(sorted(regra["ext"])) + ".")
    storage.stream.seek(0, os.SEEK_END)
    tamanho = storage.stream.tell()
    storage.stream.seek(0)
    limite = current_app.config[regra["limite"]]
    if tamanho == 0:
        raise UploadError("O arquivo está vazio.")
    if tamanho > limite:
        raise UploadError(f"Arquivo muito grande. Limite: {limite // (1024 * 1024)} MB.")
    cabecalho = storage.stream.read(16)
    storage.stream.seek(0)
    if not _confere_assinatura(ext, cabecalho):
        raise UploadError("O conteúdo do arquivo não corresponde ao formato informado.")
    visibilidade = "private" if privado else "public"
    rel_dir = os.path.join(visibilidade, pasta)
    abs_dir = os.path.join(current_app.config["UPLOAD_ROOT"], rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    final = f"{uuid.uuid4().hex}.{ext}"
    storage.save(os.path.join(abs_dir, final))
    return f"{visibilidade}/{pasta}/{final}".replace("\\", "/")


def caminho_absoluto(rel):
    root = os.path.abspath(current_app.config["UPLOAD_ROOT"])
    full = os.path.abspath(os.path.join(root, rel))
    if not full.startswith(root + os.sep):
        raise UploadError("Caminho inválido.")
    return full


def remover_upload(rel):
    if not rel:
        return
    try:
        full = caminho_absoluto(rel)
        if os.path.exists(full):
            os.remove(full)
    except (UploadError, OSError):
        current_app.logger.warning("Falha ao remover arquivo %s", rel)
