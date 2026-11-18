#!/usr/bin/env python3
"""Radar do Studa: acha o que mais viralizou no nicho (Instagram, TikTok, YouTube e Shorts) e grava docs/dados.json.

Roda todo dia no GitHub Actions (.github/workflows/radar.yml), só com a biblioteca padrão.
Segredos do repositório:
  YOUTUBE_API_KEY  chave da YouTube Data API v3 (grátis) -> YouTube e Shorts
  APIFY_TOKEN      token da Apify -> TikTok e Instagram (nenhum dos dois abre esses dados)
Fonte sem segredo, ou que falhar, não derruba as outras: os vídeos dela da busca
anterior ficam no site e o motivo aparece num aviso.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RAIZ, "radar", "config.json")
DOCS = os.path.join(RAIZ, "docs")
DADOS = os.path.join(DOCS, "dados.json")
VISTOS = os.path.join(DOCS, "vistos.json")
CAPAS = os.path.join(DOCS, "capas")
YT = "https://www.googleapis.com/youtube/v3/"
APIFY = "https://api.apify.com/v2/acts/"
DIAS_NA_MEMORIA = 60  # um vídeo visto há menos que isso não volta como "novo"
SITE_ANTERIOR = os.environ.get("SITE_ANTERIOR", "")
NAVEGADOR = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"


class FonteFalhou(Exception):
    def __init__(self, status, mensagem):
        super().__init__(mensagem)
        self.status = status


# ---------- utilitários ----------
def ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dados):
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporario, caminho)


def http_json(url, dados=None, timeout=60):
    corpo = json.dumps(dados).encode() if dados is not None else None
    pedido = urllib.request.Request(url, data=corpo, headers={"Content-Type": "application/json", "User-Agent": "radar-studa"})
    with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
        return json.load(resposta)


def motivo_do_erro(erro):
    corpo = erro.read().decode("utf-8", "replace")
    try:
        detalhe = json.loads(corpo).get("error", {})
        if isinstance(detalhe, dict):
            razao = (detalhe.get("errors") or [{}])[0].get("reason", "") or detalhe.get("type", "")
            return razao, detalhe.get("message", corpo[:200])
    except ValueError:
        pass
    return "", corpo[:200]


def segundos(duracao_iso):
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", duracao_iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def em_lotes(itens, tamanho=50):
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


def miniatura(thumbs):
    for chave in ("high", "medium", "standard", "default"):
        if chave in (thumbs or {}):
            return thumbs[chave]["url"]
    return ""


def inteiro(*valores):
    for v in valores:
        if v is None or v == "":
            continue
        try:
            return int(float(v))
        except (TypeError, ValueError):
            continue
    return None


def metricas(views, curtidas, comentarios, seguidores, publicado, agora, cfg):
    dias_no_ar = max((agora - publicado).total_seconds() / 86400, 0.25)
    multiplicador = round(views / max(seguidores, cfg["piso_seguidores"]), 1) if seguidores is not None else None
    return {
        "multiplicador": multiplicador,
        "views_por_dia": round(views / dias_no_ar),
        "engajamento": round((curtidas + comentarios) / views, 4) if views else 0,
    }


# ---------- YouTube e Shorts (API oficial) ----------
def youtube(metodo, chave, **parametros):
    parametros["key"] = chave
    url = YT + metodo + "?" + urllib.parse.urlencode(parametros)
    ultimo = None
    for _ in range(3):
        try:
            return http_json(url, timeout=30)
        except urllib.error.HTTPError as erro:
            razao, mensagem = motivo_do_erro(erro)
            if "quota" in (razao + mensagem).lower():
                raise FonteFalhou("cota", "A cota diária da API do YouTube acabou.")
            if erro.code in (400, 401, 403):
                raise FonteFalhou("chave_invalida", f"{erro.code} {razao}: {mensagem}")
            ultimo = erro
        except (urllib.error.URLError, TimeoutError) as erro:
            ultimo = erro
    raise FonteFalhou("erro", f"O YouTube não respondeu: {ultimo}")


def buscar_youtube(cfg, chave, agora):
    redes = cfg.get("redes", {})
    duracoes = (["short"] if redes.get("shorts", True) else []) + (["medium", "long"] if redes.get("youtube", True) else [])
    if not duracoes:
        return []
    desde = (agora - timedelta(days=cfg["dias"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    temas_por_video = {}
    for tema in cfg["temas"]:
        for duracao in duracoes:
            r = youtube("search", chave, part="id", q=tema, type="video", order="viewCount",
                        publishedAfter=desde, regionCode=cfg["regiao"], relevanceLanguage=cfg["idioma"],
                        videoDuration=duracao, maxResults=min(50, cfg["max_por_tema"]), safeSearch="moderate")
            for item in r.get("items", []):
                vid = item.get("id", {}).get("videoId")
                if vid and tema not in temas_por_video.setdefault(vid, []):
                    temas_por_video[vid].append(tema)

    videos = {}
    for lote in em_lotes(list(temas_por_video)):
        r = youtube("videos", chave, part="snippet,statistics,contentDetails", id=",".join(lote), maxResults=50)
        for v in r.get("items", []):
            videos[v["id"]] = v
    canais = {}
    for lote in em_lotes(sorted({v["snippet"]["channelId"] for v in videos.values()})):
        r = youtube("channels", chave, part="snippet,statistics", id=",".join(lote), maxResults=50)
        for c in r.get("items", []):
            canais[c["id"]] = c

    saida = []
    for vid, v in videos.items():
        sn, st = v["snippet"], v.get("statistics", {})
        idioma = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").lower()
        if idioma and not idioma.startswith(cfg["idioma"]):
            continue
        duracao = segundos(v.get("contentDetails", {}).get("duration"))
        if not duracao:
            continue
        rede = "shorts" if duracao <= cfg["duracao_maxima_short_s"] else "youtube"
        if not redes.get(rede, True):
            continue
        views = inteiro(st.get("viewCount")) or 0
        if views < cfg["views_minimas"]:
            continue
        curtidas = inteiro(st.get("likeCount")) or 0
        comentarios = inteiro(st.get("commentCount")) or 0
        canal = canais.get(sn["channelId"], {})
        cst = canal.get("statistics", {})
        seguidores = None if cst.get("hiddenSubscriberCount") else (inteiro(cst.get("subscriberCount")) or 0)
        publicado = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
        item = {
            "id": vid,
            "codigo": vid,
            "rede": rede,
            "titulo": sn.get("title", ""),
            "canal": sn.get("channelTitle", ""),
            "handle": canal.get("snippet", {}).get("customUrl", ""),
            "avatar": miniatura(canal.get("snippet", {}).get("thumbnails")),
            "seguidores": seguidores,
            "views": views,
            "curtidas": curtidas,
            "comentarios": comentarios,
            "compartilhamentos": None,
            "salvamentos": None,
            "publicado_em": sn["publishedAt"],
            "duracao_s": duracao,
            "thumb": miniatura(sn.get("thumbnails")),
            "url": ("https://www.youtube.com/shorts/" if rede == "shorts" else "https://www.youtube.com/watch?v=") + vid,
            "temas": temas_por_video.get(vid, []),
        }
        item.update(metricas(views, curtidas, comentarios, seguidores, publicado, agora, cfg))
        saida.append(item)
    return saida


# ---------- Apify (TikTok e Instagram) ----------
def apify(ator, entrada, token):
    url = APIFY + ator + "/run-sync-get-dataset-items?" + urllib.parse.urlencode({"token": token, "timeout": 280})
    try:
        itens = http_json(url, entrada, timeout=300)
    except urllib.error.HTTPError as erro:
        razao, mensagem = motivo_do_erro(erro)
        if erro.code in (401, 403):
            raise FonteFalhou("chave_invalida", f"A Apify recusou o token: {mensagem}")
        if erro.code == 402 or "credit" in (razao + mensagem).lower() or "limit" in razao.lower():
            raise FonteFalhou("cota", "Os créditos do mês na Apify acabaram.")
        raise FonteFalhou("erro", f"{erro.code} {razao}: {mensagem}")
    except (urllib.error.URLError, TimeoutError) as erro:
        raise FonteFalhou("erro", f"A Apify não respondeu: {erro}")
    if not isinstance(itens, list):
        raise FonteFalhou("erro", "A Apify devolveu algo inesperado.")
    return itens


def data_do_item(it):
    for campo in ("createTimeISO", "timestamp", "takenAt"):
        valor = it.get(campo)
        if isinstance(valor, str) and valor:
            try:
                return datetime.fromisoformat(valor.replace("Z", "+00:00"))
            except ValueError:
                pass
    numero = inteiro(it.get("createTime"), it.get("takenAtTimestamp"))
    return datetime.fromtimestamp(numero, tz=timezone.utc) if numero else None


# ---------- TikTok ----------
def buscar_tiktok(cfg, token, agora):
    tt = cfg["tiktok"]
    entrada = {
        "searchQueries": tt["buscas"],
        "resultsPerPage": tt["por_busca"],
        "searchSection": "/video",
        "proxyCountryCode": cfg["regiao"],
        "shouldDownloadCovers": False,
        "shouldDownloadVideos": False,
        "shouldDownloadSubtitles": False,
        "shouldDownloadSlideshowImages": False,
    }
    itens = apify(tt["ator"], entrada, token)
    limite = agora - timedelta(days=cfg["dias"])
    saida, vistos_aqui = [], set()
    for it in itens:
        vid = str(it.get("id") or "")
        if not vid or vid in vistos_aqui or it.get("isAd"):
            continue
        vistos_aqui.add(vid)
        autor = it.get("authorMeta") or {}
        meta = it.get("videoMeta") or {}
        publicado = data_do_item(it)
        if not publicado or publicado < limite:
            continue
        views = inteiro(it.get("playCount")) or 0
        if views < cfg["views_minimas"]:
            continue
        curtidas = inteiro(it.get("diggCount")) or 0
        comentarios = inteiro(it.get("commentCount")) or 0
        seguidores = inteiro(autor.get("fans"))
        texto = (it.get("text") or "").strip()
        consulta = it.get("searchQuery") or it.get("input") or ""
        temas = [consulta] if consulta in tt["buscas"] else [b for b in tt["buscas"] if b.lower() in texto.lower()][:2]
        nome = autor.get("name") or ""
        item = {
            "id": "tt" + vid,
            "codigo": vid,
            "rede": "tiktok",
            "titulo": texto or "(sem legenda)",
            "canal": autor.get("nickName") or nome,
            "handle": ("@" + nome) if nome else "",
            "avatar": "",
            "seguidores": seguidores,
            "views": views,
            "curtidas": curtidas,
            "comentarios": comentarios,
            "compartilhamentos": inteiro(it.get("shareCount")),
            "salvamentos": inteiro(it.get("collectCount")),
            "publicado_em": publicado.isoformat().replace("+00:00", "Z"),
            "duracao_s": inteiro(meta.get("duration")) or 0,
            "thumb": meta.get("coverUrl") or meta.get("originalCoverUrl") or "",
            "url": it.get("webVideoUrl") or (f"https://www.tiktok.com/@{nome}/video/{vid}" if nome else ""),
            "temas": temas or ["TikTok"],
        }
        item.update(metricas(views, curtidas, comentarios, seguidores, publicado, agora, cfg))
        saida.append(item)
    return saida


# ---------- Instagram ----------
def buscar_instagram(cfg, token, agora):
    ig = cfg["instagram"]
    itens = apify(ig["ator"], {"hashtags": ig["hashtags"], "resultsType": "reels", "resultsLimit": ig["por_hashtag"]}, token)
    limite = agora - timedelta(days=cfg["dias"])
    saida, vistos_aqui = [], set()
    for it in itens:
        codigo = it.get("shortCode") or it.get("shortcode") or ""
        if not codigo or codigo in vistos_aqui or it.get("isSponsored"):
            continue
        vistos_aqui.add(codigo)
        publicado = data_do_item(it)
        if not publicado or publicado < limite:
            continue
        views = inteiro(it.get("videoPlayCount"), it.get("videoViewCount"), it.get("playCount")) or 0
        if views < cfg["views_minimas"]:
            continue
        curtidas = max(inteiro(it.get("likesCount")) or 0, 0)  # -1 quando o perfil esconde as curtidas
        comentarios = inteiro(it.get("commentsCount")) or 0
        dono = it.get("ownerUsername") or ""
        hashtags_do_post = [h.lower().lstrip("#") for h in (it.get("hashtags") or [])]
        origem = (it.get("hashtag") or it.get("searchTerm") or it.get("inputUrl") or "").lower()
        temas = [h for h in ig["hashtags"] if h.lower() in origem] or [h for h in ig["hashtags"] if h.lower() in hashtags_do_post][:2]
        saida.append({
            "id": "ig" + codigo,
            "codigo": codigo,
            "rede": "instagram",
            "titulo": (it.get("caption") or "").strip() or "(sem legenda)",
            "canal": it.get("ownerFullName") or dono,
            "handle": ("@" + dono) if dono else "",
            "avatar": "",
            "seguidores": None,
            "views": views,
            "curtidas": curtidas,
            "comentarios": comentarios,
            "compartilhamentos": None,
            "salvamentos": None,
            "publicado_em": publicado.isoformat().replace("+00:00", "Z"),
            "duracao_s": int(float(it.get("videoDuration") or 0)),
            "thumb": it.get("displayUrl") or "",
            "url": it.get("url") or f"https://www.instagram.com/reel/{codigo}/",
            "temas": ["#" + t for t in temas] or ["Instagram"],
            "_publicado": publicado,
        })

    # seguidores só dos mais vistos: cada perfil consultado custa crédito
    donos = []
    for v in sorted(saida, key=lambda x: x["views"], reverse=True):
        nome = v["handle"].lstrip("@")
        if nome and nome not in donos:
            donos.append(nome)
        if len(donos) >= ig["perfis_para_multiplicador"]:
            break
    seguidores = {}
    if donos:
        try:
            for p in apify(ig["ator_perfis"], {"usernames": donos}, token):
                nome = (p.get("username") or "").lower()
                if nome:
                    seguidores[nome] = inteiro(p.get("followersCount"))
        except FonteFalhou as erro:
            print(f"instagram: perfis sem seguidores ({erro})")
    for v in saida:
        v["seguidores"] = seguidores.get(v["handle"].lstrip("@").lower())
        v.update(metricas(v["views"], v["curtidas"], v["comentarios"], v["seguidores"], v.pop("_publicado"), agora, cfg))
    return saida


def guardar_capas(videos):
    """As capas do TikTok e do Instagram expiram e não abrem fora deles: baixa para docs/capas/."""
    os.makedirs(CAPAS, exist_ok=True)
    for v in videos:
        if v["rede"] not in ("tiktok", "instagram") or not v["thumb"]:
            continue
        destino = os.path.join(CAPAS, f"{v['id']}.jpg")
        origem = v["thumb"]
        if not origem.startswith("http"):
            # capa de uma busca anterior: vem do site publicado
            if os.path.exists(destino) or not SITE_ANTERIOR:
                continue
            origem = SITE_ANTERIOR.rstrip("/") + "/" + origem
        try:
            pedido = urllib.request.Request(origem, headers={"User-Agent": NAVEGADOR})
            with urllib.request.urlopen(pedido, timeout=20) as r, open(destino, "wb") as f:
                f.write(r.read())
            v["thumb"] = f"capas/{v['id']}.jpg"
        except Exception:  # sem capa o card mostra um fundo liso
            v["thumb"] = ""


# ---------- juntar tudo ----------
def main():
    cfg = ler_json(CONFIG, None)
    if not cfg:
        sys.exit("radar/config.json não encontrado ou inválido")
    agora = datetime.now(timezone.utc).replace(microsecond=0)
    anterior = ler_json(DADOS, {})
    vistos = ler_json(VISTOS, {})
    redes = cfg.get("redes", {})

    fontes = []
    if redes.get("shorts", True) or redes.get("youtube", True):
        fontes.append(("youtube", "YOUTUBE_API_KEY", buscar_youtube, ("shorts", "youtube")))
    if redes.get("tiktok", True):
        fontes.append(("tiktok", "APIFY_TOKEN", buscar_tiktok, ("tiktok",)))
    if redes.get("instagram", True):
        fontes.append(("instagram", "APIFY_TOKEN", buscar_instagram, ("instagram",)))

    videos, avisos = [], []
    for nome, segredo, funcao, redes_da_fonte in fontes:
        valor = os.environ.get(segredo, "").strip()
        try:
            if not valor:
                raise FonteFalhou("sem_chave", f"Falta o segredo {segredo}.")
            achados = funcao(cfg, valor, agora)
            videos += achados
            print(f"{nome}: {len(achados)} vídeos")
        except FonteFalhou as erro:
            print(f"{nome}: não rodou ({erro.status}) {erro}")
            avisos.append({"fonte": nome, "status": erro.status, "mensagem": str(erro)[:300]})

    # o que buscas anteriores acharam continua no radar enquanto estiver dentro do período
    # (os números ficam os da última vez em que o vídeo apareceu)
    achados_hoje = {v["id"] for v in videos}
    inicio_do_periodo = agora - timedelta(days=cfg["dias"])
    for v in anterior.get("videos", []):
        if v.get("id") in achados_hoje or not redes.get(v.get("rede"), True):
            continue
        try:
            publicado = datetime.fromisoformat(v["publicado_em"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if publicado >= inicio_do_periodo:
            videos.append(v)

    hoje = agora.strftime("%Y-%m-%d")
    for v in videos:
        antes = vistos.get(v["id"])
        v["novo"] = antes is None
        v["primeira_vez"] = (antes or {}).get("p", hoje)
        v["ganho"] = None
        if antes and antes.get("v") is not None and antes.get("t"):
            horas = (agora - datetime.fromisoformat(antes["t"])).total_seconds() / 3600
            if horas >= 1:
                v["ganho"] = {"views": v["views"] - antes["v"], "horas": round(horas)}

    videos.sort(key=lambda x: (x["multiplicador"] is not None, x["multiplicador"] or 0, x["views"]), reverse=True)
    videos = videos[: cfg["max_videos_no_site"]]
    guardar_capas(videos)

    limite = (agora - timedelta(days=DIAS_NA_MEMORIA)).strftime("%Y-%m-%d")
    vistos = {k: v for k, v in vistos.items() if v.get("p", "") >= limite}
    for v in videos:
        vistos[v["id"]] = {"p": v["primeira_vez"], "v": v["views"], "t": agora.isoformat()}

    algum_ok = len(avisos) < len(fontes)
    saida = {
        "versao": 3,
        "nome": cfg["nome"],
        "perfil": cfg["perfil"],
        "temas": cfg["temas"],
        "temas_tiktok": cfg.get("tiktok", {}).get("buscas", []),
        "temas_instagram": ["#" + h for h in cfg.get("instagram", {}).get("hashtags", [])],
        "redes": redes,
        "dias": cfg["dias"],
        "hora_da_busca": cfg.get("hora_da_busca", "08:00"),
        "tentativa_em": agora.isoformat(),
        "ultima_busca_ok": agora.isoformat() if algum_ok else anterior.get("ultima_busca_ok"),
        "avisos": avisos,
        "total": len(videos),
        "novos": sum(1 for v in videos if v["novo"]),
        "videos": videos,
    }
    gravar_json(DADOS, saida)
    gravar_json(VISTOS, vistos)
    print(f"Total: {saida['total']} vídeos, {saida['novos']} novos, {len(avisos)} aviso(s).")


if __name__ == "__main__":
    main()
