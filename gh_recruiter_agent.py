#!/usr/bin/env python3
"""
gh_recruiter_agent.py — Agente que audita um perfil público do GitHub e o
classifica quanto à atratividade para recrutamento técnico.

Saídas:
  1. Nota geral (0-100) + veredito (PRONTO / QUASE / TRABALHAR / CORRIGIR)
  2. Nota por dimensão, com pesos
  3. Achados priorizados (P0/P1/P2) com.patch pronto para aplicar
  4. Relatório Markdown gravado em ./gh_report_<user>.md

Somente stdlib. Somente leitura da API pública. Nada é escrito no GitHub.

Uso:
  python gh_recruiter_agent.py SEU_USUARIO
  python gh_recruiter_agent.py SEU_USUARIO --token GHP_xxx   (evita rate limit)
  python gh_recruiter_agent.py SEU_USUARIO --out relatorio.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

API = "https://api.github.com"
UA = "gh-recruiter-agent/1.0"

# --------------------------------------------------------------------------
# Pesos das dimensões (somam 1.0)
# --------------------------------------------------------------------------
WEIGHTS = {
    "perfil": 0.15,      # bio, nome, avatar, localização, pronouns, empresa
    "readme": 0.18,      # repos de perfil com README substancial
    "projetos": 0.25,    # repos com sinal de projeto real (desc, topics, licences)
    "tecnologias": 0.15,  # linguagens/diversidade e coerência do stack
    "atividade": 0.12,   # consistência de commits
    "prova_social": 0.10, # stars, forks, followers, lista de contributors
    "higiene": 0.05,     # .gitignore, license, pinned, artefatos acidentais
}

LOWERCASE_LANGS = {"javascript", "typescript", "python", "java", "go", "rust", "c",
                   "c++", "c#", "php", "ruby", "swift", "kotlin", "dart", "scala"}


# --------------------------------------------------------------------------
# Cliente HTTP
# --------------------------------------------------------------------------
class GH:
    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.n = 0

    def get(self, path: str, soft404: bool = False) -> Any:
        url = path if path.startswith("http") else API + path
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
        })
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    self.n += 1
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (403, 429) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                if e.code in (403, 429) and not self.token:
                    raise GHError(
                        "Limite de taxa da API anônima (60 req/hora) esgotado. "
                        "Rode com --token GITHUB_... (Personal Access Token, "
                        "sem escopos basta para leitura pública).")
                if e.code == 404:
                    if soft404:
                        return {}
                    raise GHError(f"404 em {path} — perfil/repo inexistente ou privado")
                if e.code == 401:
                    raise GHError("Token inválido. Rode sem --token ou gere um novo.")
                raise GHError(f"HTTP {e.code} em {path}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == 2:
                    raise GHError(f"Falha de rede: {e}")
                time.sleep(1.5)
        raise GHError("inacessível")

    def raw(self, url: str) -> str | None:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "application/vnd.github.raw",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            })
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            return None


class GHError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Modelo de resultado
# --------------------------------------------------------------------------
@dataclass
class Finding:
    prio: str            # P0 | P1 | P2
    dim: str
    title: str
    why: str
    fix: str
    gain: str            # ganho estimado em pontos
    patch: str | None = None


@dataclass
class Report:
    user: str
    dims: dict[str, dict] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    total: float = 0.0
    verdict: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    def band(self) -> str:
        if self.total >= 80:
            return "PRONTO — o recrutador te encontra e entende em 10 segundos"
        if self.total >= 65:
            return "QUASE — bom perfil; há ganhos rápidos de uma tarde de trabalho"
        if self.total >= 45:
            return "TRABALHAR — a base existe mas o perfil não prova competência"
        return "CORRIGIR — hoje o perfil passa despercebido por recruiters"

    def render(self) -> str:
        L: list[str] = []
        A = L.append
        A(f"# Diagnóstico GitHub — {self.user}\n")
        A(f"## {self.total:.0f}/100 · {self.band()}\n")

        A("| Dimensão | Peso | Nota | Contribution |")
        A("|---|---:|---:|---|")
        for k, w in WEIGHTS.items():
            d = self.dims[k]
            bar = "█" * round(d["score"]) + "░" * (20 - round(d["score"]))
            A(f"| {k} | {w:.2f} | {d['score']:.0f} `{bar}` | {d['note']} |")
        A("")

        for prio, label, color in (("P0", "Crítico — bloqueia", "🔴"),
                                   ("P1", "Alto — alto retorno", "🟠"),
                                   ("P2", "Refino", "🟡")):
            items = [f for f in self.findings if f.prio == prio]
            if not items:
                continue
            A(f"## {color} {prio} · {label}\n")
            for f in items:
                A(f"### {f.title}  `+{f.gain}`\n")
                A(f"**Por quê:** {f.why}\n")
                A(f"**Como corrigir:** {f.fix}\n")
                if f.patch:
                    A(f"\n{f.patch}\n")
                A("")

        A("---\n")
        A("### Señais coletadas\n```json")
        A(json.dumps(self.stats, indent=2, ensure_ascii=False))
        A("```")
        return "\n".join(L)


# --------------------------------------------------------------------------
# Coleta
# --------------------------------------------------------------------------
def collect(g: GH, user: str) -> tuple[dict, list[dict]]:
    prof = g.get(f"/users/{user}")
    repos = g.get(f"/users/{user}/repos?per_page=100&sort=updated")
    ev = g.get(f"/users/{user}/events/public?per_page=100")
    if isinstance(repos, dict):          # limite de taxa
        raise GHError("Limite de API atingido. Rode com --token.")
    return prof, (repos if isinstance(repos, list) else [])


def days_since(iso: str | None) -> float:
    """Dias desde uma data ISO-8601 do GitHub (Z). Inf se ausente."""
    if not iso:
        return float("inf")
    try:
        return (time.time() - time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))) / 86400
    except (ValueError, TypeError):
        return float("inf")


# Extensões cujas chaves são públicas por desenho (certs de CA, chaves de
# compilação, fixtures de teste) ou cujo nome é genérico demais.
_SECRET_EXT = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk", ".asc", ".gpg"}
_SECRET_NAME = re.compile(
    r"(?i)^(\.env|env|secrets?|credentials?|id_rsa|id_dsa|id_ecdsa|id_ed25519"
    r"|\.netrc|\.htpasswd|\.npmrc|\.pypirc|\.dockercfg|service[_-]?account.*"
    r"|.*_rsa|.*_ed25519)$")
_ALLOW_DIR = re.compile(r"(?i)(^|/)(test|tests|testing|spec|specs|__tests__|fixtures?"
                        r"|docs?|examples?|samples?|migrations?|vendor|third[_-]?party)(/|$)")
# Não é segredo mesmo com cara de um: certificado público, chave de assinatura
# versionada, lockfile, chave pública.
_NOT_SECRET_EXT = {".pub", ".crt", ".cer", ".csr", ".lock", ".sum", ".mod", ".sha256"}
_NOT_SECRET_NAME = re.compile(r"(?i)\.(md|rst|txt|example|sample|template|dist|in)$")
# Nome-base genérico de teste/exemplo, sem extensão reconhecível de chave.
_TEST_BASENAME = re.compile(
    r"(?i)^(test|tests?|testing|spec|specs?|mock|mocks|fixtures?|examples?|samples?"
    r"|dummy|fake|stub|stubs|placeholder|tmp|temp|backup|bak|old|copy)$")


def looks_like_secret(path: str) -> bool:
    """Heurística de nome de arquivo — só o caminho, nunca o conteúdo.

    Verdadeiros positivos importam: uma chave real commitada precisa de
    rotação imediata, então preferimos relatar e deixar o humano desmentir.
    Falsos positivos, porém, corroem a confiança no alerta de P0.
    """
    base = path.rsplit("/", 1)[-1]
    ext = os.path.splitext(base)[1].lower()
    stem = os.path.splitext(base)[0]

    if ext in _NOT_SECRET_EXT or _NOT_SECRET_NAME.search(base):
        return False
    # .env pode ser .env.example — já coberto acima, mas o sufixo importa.
    if stem.lower().endswith((".example", ".sample", ".template", ".dist")):
        return False
    if _TEST_BASENAME.match(stem):
        return False
    if _ALLOW_DIR.search(path):
        return False
    if ext in _SECRET_EXT:
        return True
    if _SECRET_NAME.match(stem) or _SECRET_NAME.match(base):
        return True
    return False


def is_profile_repo(r: dict, login: str) -> bool:
    return r["name"].lower() == login.lower()


# --------------------------------------------------------------------------
# Dimensões
# --------------------------------------------------------------------------
def score_profile(p: dict, repos: list[dict]) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    s = 0.0
    notes = []

    if p.get("name"):
        s += 12; notes.append("nome ✓")
    else:
        f.append(Finding("P1", "perfil", "Definir nome de exibição",
                         "Recrutadores leem nome, não @handle.",
                         "Settings → Profile → Name.", "12",
                         '- nome: "Seu Nome Completo"  # block de perfil'))
    bio = (p.get("bio") or "").strip()
    if len(bio) >= 40:
        s += 16; notes.append("bio forte ✓")
    elif bio:
        s += 8; notes.append("bio curta")
        f.append(Finding("P1", "perfil", "Bio com 3 linhas",
                         f"Bio tem {len(bio)} chars; o padrão de recruta é 120-200.",
                         "Escreva: [cargo] · [stack] · [domínio] · [disponibilidade].",
                         "8", "bio: |\n  Engenheiro de software — Python/Go + dados.\n  Builds open source, mentoria e projetos de ML aplicado."))
    else:
        f.append(Finding("P0", "perfil", "Bio ausente",
                         "Sem bio o card do perfil é invisível para quem não te conhece.",
                         "Preencha com 3 linhas.", "16",
                         "bio: |\n  Cargo · stack · domínio · o que você procura"))

    if p.get("location"):
        s += 6; notes.append("localização ✓")
    if p.get("company"):
        s += 6; notes.append("empresa ✓")
    else:
        notes.append("empresa —")
    if p.get("twitter_username"):
        s += 4; notes.append("X ✓")
    if p.get("hireable"):
        s += 8; notes.append("aberto a propostas ✓")
    else:
        f.append(Finding("P2", "perfil", "Marcar como Hireable",
                         "Flag aparece para recrutadores na aba Overview.",
                         "Profile → Edit → 'Available for hire'.", "8"))
    if p.get("blog"):
        s += 6; notes.append("site ✓")
    else:
        f.append(Finding("P2", "perfil", "Adicionar site/portfolio",
                         "README renderizado em github.com/USER/USER é a vitrine principal.",
                         "Settings → Profile → Website.", "6"))

    score = min(100.0, s / 62 * 100)
    return {"score": score, "note": ", ".join(notes) or "vazio"}, f


def score_readme(g: GH, p: dict, repos: list[dict], user: str) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    mine = [r for r in repos if is_profile_repo(r, user)]
    if not mine:
        return ({"score": 0.0, "note": "sem repo de perfil"}, [Finding(
            "P0", "readme", "Criar o repositório de perfil",
            "github.com/USER/USER renderiza o README como bio pública. Sem ele, "
            "quem chega pelo Google vê só uma lista de repos.",
            "Crie um repo público chamado exatamente igual ao seu usuário.",
            "18",
            "```bash\ngit init README_PROFILE.md\n# nome do repo DEVE ser == seu login\n```")])
    r = mine[0]
    txt = g.raw(f"{API}/repos/{r['owner']['login']}/{r['name']}/readme")
    size = len(txt.strip()) if txt else 0
    notes = [f"repo '{r['name']}'", f"{size} chars"]
    score = 20.0
    if size >= 800:
        score = 92; notes.append("substancial ✓")
    elif size >= 400:
        score = 65; notes.append("curto")
    else:
        score = 30; notes.append("insuficiente")
        f.append(Finding("P0", "readme", "README de perfil vazio ou placeholder",
                         f"Só {size} chars — abaixo do que um recrutador lê.",
                         "Estrutura: badge de título, 2 linhas de posicionamento, "
                         "stack como ícones, projetos com link, contato,Rodapé com "
                         "o que você quer que apareça primeiro.", "20"))
    if txt and "![Avatar" not in txt and "![" not in txt:
        f.append(Finding("P2", "readme", "Falta avatar/imagem no topo",
                         "O primeiro pixel decide se o leitor para.", "Adicione header.", "4"))
    if txt and not re.search(r"(?i)(gmail|mailto|linkedin|/SEU_USUARIO)", txt):
        f.append(Finding("P1", "readme", "Sem link de contato",
                         "Recrutador que gostou do perfil não tem por onde seguir.",
                         "Rodapé com e-mail e LinkedIn.", "6",
                         "```markdown\n[gmail](mailto:voce@gmail.com) · "
                         "[LinkedIn](https://linkedin.com/in/voce)\n```"))
    return {"score": score, "note": ", ".join(notes)}, f


def score_projects(repos: list[dict], user: str) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    if not repos:
        return {"score": 0.0, "note": "nenhum repo"}, [Finding(
            "P0", "projetos", "Nenhum repositório público",
            "Perfil sem trabalho é invisível.", "Publique 3 projetos.", "25")]

    real, forks, trivial = [], 0, []
    for r in repos:
        if r["fork"]:
            forks += 1; continue
        desc = (r.get("description") or "").strip()
        topics = r.get("topics") or []
        score = r["stargazers_count"] * 2 + len(topics) * 4
        if len(desc) >= 30:
            score += 15
        if r.get("license"):
            score += 6
        if r.get("homepage"):
            score += 4
        if score >= 15 or r["stargazers_count"] >= 1:
            real.append(r)
        else:
            trivial.append(r)

    owned = [r for r in repos if not r["fork"] and not is_profile_repo(r, user)]
    n = len(owned)
    notes = [f"{n} próprios", f"{forks} forks", f"{len(real)} com sinal"]

    if n == 0:
        s = 0.0
        f.append(Finding("P0", "projetos", "Só forks ou nada",
                         "Forks não provam autoria.", "Publique código próprio.", "20"))
    elif n == 1:
        s = 32.0
        f.append(Finding("P1", "projetos", "Um único projeto",
                         "Recrutador quer variação: 2-3 domínios distintos.",
                         "Publique mais 2 projetos.", "15"))
    elif n >= 3:
        s = min(92.0, 55 + n * 8)
        notes.append("quantidade ok ✓")
    else:
        s = 52.0

    if real:
        s = min(100.0, s + 6)
    elif owned:
        f.append(Finding("P0", "projetos", "Projetos sem documentação",
                         f"{len(owned)} repos sem descrição/tópicos/license — "
                         "parecem exercício de curso.",
                         "Cada repo: descrição ≥30 chars, topics, LICENSE.",
                         "14",
                         "gh repo edit NOME --description \"...\" --add-topic x --license mit\n"
                         "gh repo edit NOME --description \"...\" --add-topic y --license mit"))
    if trivial and not f:
        f.append(Finding("P2", "projetos", "Limpar repos frágeis",
                         f"{len(trivial)} repos sem sinal atrapalham a leitura.",
                         "Arquive/delete ou dê descrição e topics.", "5"))
    return {"score": s, "note": ", ".join(notes)}, f


def score_tech(langs: dict, repos: list[dict]) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    if not langs:
        return {"score": 0.0, "note": "sem linguagens"}, [Finding(
            "P2", "tecnologias", "Nenhuma linguagem detectada",
            "Tudo em linguagens de altíssimo volume não distingue você.",
            "Posicione o GitHub numa linguagem do seu mercado-alvo.", "6")]

    total = sum(langs.values())
    top = sorted(langs.items(), key=lambda kv: -kv[1])
    share = top[0][1] / total * 100
    dom = [l for l, _ in top if l.lower() in LOWERCASE_LANGS]
    notes = [", ".join(f"{l} {round(v/total*100)}%" for l, v in top[:3])]

    s = min(100.0, 30 + len(langs) * 9)
    if len(langs) >= 4:
        s = min(100.0, s + 8); notes.append("stack larga ✓")
    else:
        f.append(Finding("P2", "tecnologias", "Stack estreita",
                         f"Apenas {len(langs)} linguagem(ns). Recrutadores esperam "
                         "main + 2-3 adjacentes.",
                         "Adicione um repo usando framework/biblioteca diferente.", "8"))
    if share > 90:
        s -= 18; notes.append("monolinguagem")
        f.append(Finding("P2", "tecnologias", "Perfil 100% monocromático",
                         f"{top[0]} ocupa {share:.0f}% do seu código.",
                         "Um segundo contexto (infra, dados, frontend) reforça senioridade.", "10"))
    if len(dom) == 1 and len(langs) == 1:
        f.append(Finding("P1", "tecnologias", "Linguagem de altíssimo volume",
                         f"Todo o GitHub em {top[0][0]} não diferencia senior de júnior.",
                         "Use também Go/Rust/C#/Java/Swift para marcar o teto técnico.", "12"))
    return {"score": max(0.0, s), "note": " · ".join(notes)}, f


def score_activity(repos: list[dict], ev: list[dict], user: str) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    own = [r for r in repos if not r["fork"] and not is_profile_repo(r, user)]
    recent = sum(1 for r in own if days_since(r["pushed_at"]) < 60)
    pushes = sum(1 for e in ev if e["type"] == "PushEvent" and not e["repo"]["name"].endswith(f"/{user}"))
    notes = [f"{recent} ativos em 60d", f"{pushes} pushes (100 eventos)"]

    if not own:
        s = 0.0
    elif recent == 0:
        s = 22.0
        f.append(Finding("P1", "atividade", "Perfil parado há >60 dias",
                         "Recrutador lê o último push antes de qualquer coisa.",
                         "Continue em projetos reais; commits pequenos contam.", "12"))
    elif recent >= 3 and pushes >= 25:
        s = 92.0; notes.append("constante ✓")
    elif recent >= 2:
        s = 68.0
    else:
        s = 45.0
        f.append(Finding("P2", "atividade", "Atividade irregular",
                         "Ritmo irregular sugere projeto paralelo abandonado.",
                         "Commite com frequência previsível.", "6"))
    return {"score": s, "note": " · ".join(notes)}, f


def score_social(repos: list[dict], p: dict) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    stars = sum(r["stargazers_count"] for r in repos)
    forks = sum(r["forks_count"] for r in repos)
    foll = p.get("followers", 0)
    notes = [f"{stars} stars", f"{forks} forks", f"{foll} followers"]

    s = min(100.0, foll * 1.2 + stars * 3.0 + forks * 2.0)
    if stars == 0 and forks == 0:
        f.append(Finding("P2", "prova_social", "Nenhuma star ou fork",
                         "Sem validação externa nada é remarkable.",
                         "Resolva issues de terceiros, publique ferramenta CLI, "
                         "responda em Issue/Discussion — stars chegam sozinhas.", "8"))
    if foll == 0:
        f.append(Finding("P2", "prova_social", "Zero seguidores",
                         "Perfil de rede social vazio empurra para o fim da lista.",
                         "Siga e comente em projetos do seu stack.", "4"))
    return {"score": s, "note": " · ".join(notes)}, f


def score_hygiene(g: GH, repos: list[dict], p: dict, user: str) -> tuple[dict, list[Finding]]:
    f: list[Finding] = []
    own = [r for r in repos if not r["fork"] and not is_profile_repo(r, user)][:12]
    no_license = [r for r in own if not r.get("license")]
    with_gi = 0
    dirty = []
    for r in own:
        t = g.raw(f"{API}/repos/{r['owner']['login']}/{r['name']}/contents/.gitignore")
        if t:
            with_gi += 1
        tree = g.raw(f"{API}/repos/{r['owner']['login']}/{r['name']}/git/trees/HEAD?recursive=1")
        if tree:
            try:
                paths = [n["path"] for n in json.loads(tree).get("tree", [])
                         if n.get("type") == "blob"]
            except json.JSONDecodeError:
                paths = []
            for p in paths:
                if looks_like_secret(p):
                    dirty.append(f"{r['name']}/{p}")

    notes = [f"{len(own) - len(no_license)}/{len(own)} com LICENSE", f"{with_gi} com .gitignore"]
    s = 100.0
    if no_license:
        s -= min(45, len(no_license) * 15)
        f.append(Finding("P1", "higiene", f"{len(no_license)} repo(s) sem licença",
                         "Sem LICENSE o uso por terceiros é legalmente proibido — "
                         "repositório de empresa também não pode ter código proprietário.",
                         "Adicione LICENSE (MIT/Apache-2.0).", "10",
                         "\n".join(f"gh repo edit {r['name']} --license mit" for r in no_license)))
    if own and with_gi < len(own):
        s -= 10
        f.append(Finding("P2", "higiene", "Falta .gitignore",
                         "Ruído no repo passa amadoridade.", "Adicione .gitignore por linguagem.", "5"))
    if dirty:
        s -= 30
        f.append(Finding("P0", "higiene", "POTENCIALMENTE SEGREDOS COMMITADOS",
                         "Arquivos sensíveis no histórico público: "
                         + ", ".join(dirty[:6]) + ". Revogue/rotacione essas chaves.",
                         "Remova do histórico (git filter-repo / BFG) e rotacione.", "30"))
        notes.append("SEGREDOS !")
    return {"score": max(0.0, s), "note": " · ".join(notes)}, f


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser(description="Agente de auditoria de perfil GitHub para recrutamento")
    ap.add_argument("user")
    ap.add_argument("--token", help="GitHub PAT (opcional, evita rate limit)")
    ap.add_argument("--out", help="caminho do relatório .md")
    ap.add_argument("--json", action="store_true", help="saída JSON")
    args = ap.parse_args()

    g = GH(args.token)
    try:
        prof, repos = collect(g, args.user)
        langs = g.get(f"/users/{args.user}/languages", soft404=True)
        ev = g.get(f"/users/{args.user}/events/public?per_page=100")
    except GHError as e:
        print(f"erro: {e}", file=sys.stderr)
        return 2
    ev = ev if isinstance(ev, list) else []

    r = Report(user=args.user)
    for dim, fn in (
        ("perfil", lambda: score_profile(prof, repos)),
        ("readme", lambda: score_readme(g, prof, repos, args.user)),
        ("projetos", lambda: score_projects(repos, args.user)),
        ("tecnologias", lambda: score_tech(langs if isinstance(langs, dict) else {}, repos)),
        ("atividade", lambda: score_activity(repos, ev, args.user)),
        ("prova_social", lambda: score_social(repos, prof)),
        ("higiene", lambda: score_hygiene(g, repos, prof, args.user)),
    ):
        d, finds = fn()
        r.dims[dim] = d
        r.findings.extend(finds)

    r.total = sum(r.dims[k]["score"] * WEIGHTS[k] for k in WEIGHTS)
    r.verdict = r.band()
    r.stats = {
        "nome": prof.get("name"),
        "followers": prof.get("followers"),
        "public_repos": prof.get("public_repos"),
        "created_at": prof.get("created_at"),
        "linguagens": langs if isinstance(langs, dict) else {},
        "chamadas_api": g.n,
    }

    if args.json:
        print(json.dumps({
            "user": r.user, "nota": round(r.total, 1), "veredito": r.verdict,
            "dimensoes": {k: round(v["score"], 1) for k, v in r.dims.items()},
            "achados": [{"prio": f.prio, "titulo": f.title, "ganho": f.gain} for f in r.findings],
        }, indent=2, ensure_ascii=False))
        return 0

    md = r.render()
    out = args.out or f"gh_report_{args.user}.md"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(md)
    print(f"\n→ relatório gravado em {out}  ({g.n} chamadas à API)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())