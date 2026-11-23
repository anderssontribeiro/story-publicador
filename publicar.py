"""
Robô publicador do Story no Piloto.

Publica no Instagram (API oficial da Meta) os stories que estão na pasta "fila".
O nome do arquivo diz em qual conta e quando publicar:
    poloead360__2026-11-23_19-00_story.jpg  -> conta @poloead360, a partir de 23/11/2026 às 19h (Brasília)
    poloead360__agora_story.jpg             -> conta @poloead360, na próxima rodada
Sem o prefixo "conta__", usa a conta padrão (segredos IG_TOKEN e IG_USER_ID).
Aceita .jpg/.jpeg (imagem) e .mp4/.mov (vídeo de 3 a 60 s).

Depois de publicado, o arquivo vai para "publicados" e uma linha entra em publicados/registro.csv.
Se der erro, o arquivo vai para "erros" junto com um .txt explicando o motivo.

Segredos do GitHub, um par por conta (nome da conta em MAIÚSCULAS):
    IG_TOKEN_POLOEAD360 e IG_USER_ID_POLOEAD360, e assim por diante.
"""
import json, os, re, shutil, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
API = os.environ.get("API_BASE") or "https://graph.instagram.com/" + os.environ.get("API_VERSION", "v25.0")
try:
    SEGREDOS = json.loads(os.environ.get("SEGREDOS") or "{}")
except Exception:
    SEGREDOS = {}
for _k in ("IG_TOKEN", "IG_USER_ID"):
    if os.environ.get(_k):
        SEGREDOS.setdefault(_k, os.environ[_k])
TOKEN = ""
IG_ID = ""
REPO = os.environ.get("GITHUB_REPOSITORY", "")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
MEDIA_BASE = os.environ.get("MEDIA_BASE", "").rstrip("/") or f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
IMG = (".jpg", ".jpeg")
VID = (".mp4", ".mov")
PAT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})")


def chamar(metodo, caminho, dados=None):
    dados = dict(dados or {}, access_token=TOKEN)
    url = f"{API}/{caminho}"
    corpo = None
    if metodo == "GET":
        url += "?" + urllib.parse.urlencode(dados)
    else:
        corpo = urllib.parse.urlencode(dados).encode()
    req = urllib.request.Request(url, data=corpo, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            msg = json.loads(txt).get("error", {}).get("message", txt)
        except Exception:
            msg = txt
        raise RuntimeError(f"Instagram respondeu {e.code}: {msg}") from None


def separar(nome):
    if "__" in nome:
        conta, resto = nome.split("__", 1)
        return conta.lower().lstrip("@"), resto
    return "", nome


def credenciais(conta):
    suf = "_" + re.sub(r"[^A-Z0-9]", "_", conta.upper()) if conta else ""
    return (SEGREDOS.get("IG_TOKEN" + suf, "").strip(), SEGREDOS.get("IG_USER_ID" + suf, "").strip(), suf)


def quando(nome):
    if nome.lower().startswith("agora"):
        return datetime(2000, 1, 1, tzinfo=TZ)
    m = PAT.match(nome)
    if not m:
        return None
    a, me, d, h, mi = map(int, m.groups())
    return datetime(a, me, d, h, mi, tzinfo=TZ)


def publicar(nome):
    ext = os.path.splitext(nome)[1].lower()
    url = f"{MEDIA_BASE}/fila/{urllib.parse.quote(nome)}"
    dados = {"media_type": "STORIES"}
    dados["video_url" if ext in VID else "image_url"] = url
    cont = chamar("POST", f"{IG_ID}/media", dados)["id"]
    limite = time.time() + (300 if ext in VID else 90)
    while True:
        st = chamar("GET", cont, {"fields": "status_code,status"}).get("status_code")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"A Meta não conseguiu processar o arquivo (status {st}). Confira formato e duração.")
        if time.time() > limite:
            raise RuntimeError("A Meta demorou demais para processar o arquivo. Ele volta a ser tentado na próxima rodada.")
        time.sleep(5)
    return chamar("POST", f"{IG_ID}/media_publish", {"creation_id": cont})["id"]


def mover(nome, pasta, motivo=None):
    os.makedirs(pasta, exist_ok=True)
    shutil.move(f"fila/{nome}", f"{pasta}/{nome}")
    if motivo:
        open(f"{pasta}/{nome}.txt", "w", encoding="utf-8").write(motivo + "\n")


def main():
    global TOKEN, IG_ID
    agora = datetime.now(TZ)
    os.makedirs("fila", exist_ok=True)
    arquivos = sorted(f for f in os.listdir("fila") if os.path.isfile(f"fila/{f}") and not f.startswith(".") and not f.lower().endswith(".txt"))
    if not arquivos:
        print("Fila vazia.")
        return
    problema_token = []
    for nome in arquivos:
        conta, resto = separar(nome)
        ext = os.path.splitext(nome)[1].lower()
        hora = quando(resto)
        rotulo = f"@{conta}" if conta else "conta padrão"
        if ext not in IMG + VID:
            msg = "Formato não aceito. Use JPG para imagem ou MP4 para vídeo (o Instagram não aceita PNG pela API)."
        elif hora is None:
            msg = "Nome fora do padrão. Use conta__AAAA-MM-DD_HH-MM_nome.jpg ou conta__agora_nome.jpg."
        else:
            msg = None
        if msg:
            mover(nome, "erros", msg)
            print(f"ERRO {nome}: {msg}")
            continue
        if hora > agora:
            print(f"Aguardando {nome} ({rotulo}, publica em {hora:%d/%m %H:%M}).")
            continue
        TOKEN, IG_ID, suf = credenciais(conta)
        if not TOKEN or not IG_ID:
            aviso = f"Faltam os segredos IG_TOKEN{suf} e IG_USER_ID{suf} para {rotulo}. O arquivo continua na fila."
            print(f"AGUARDANDO SEGREDOS {nome}: {aviso}")
            problema_token.append(aviso)
            continue
        try:
            mid = publicar(nome)
        except RuntimeError as e:
            txt = str(e).lower()
            token = "token" in txt or "oauth" in txt or "session" in txt
            print(f"ERRO {nome}: {e}")
            if token:
                problema_token.append(f"O token de {rotulo} está errado ou venceu. Gere outro na Meta e atualize IG_TOKEN{suf}. O arquivo continua na fila.")
                continue
            if "demorou demais" not in txt:
                mover(nome, "erros", str(e))
            continue
        carimbo = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
        mover(nome, "publicados")
        novo = not os.path.exists("publicados/registro.csv")
        with open("publicados/registro.csv", "a", encoding="utf-8") as f:
            if novo:
                f.write("publicado_em;conta;arquivo;id_instagram\n")
            f.write(f"{carimbo};{rotulo};{nome};{mid}\n")
        print(f"PUBLICADO {nome} em {rotulo} -> {mid}")
    if problema_token:
        sys.exit("\n".join(problema_token))


if __name__ == "__main__":
    main()
