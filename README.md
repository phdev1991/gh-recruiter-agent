# gh-recruiter-agent

Agente que audita um perfil público do GitHub e diz o que um recrutador
técnico enxerga nele — e o que fazer quando a resposta é "quase nada".

Só stdlib. Sem `pip install`. Apenas leitura da API pública: nada é escrito
no GitHub.

## Uso

```bash
python gh_recruiter_agent.py SEU_USUARIO
python gh_recruiter_agent.py SEU_USUARIO --token GITHUB_xxx   # evita rate limit
python gh_recruiter_agent.py SEU_USUARIO --json              # saída machine-readable
python gh_recruiter_agent.py SEU_USUARIO --out relatorio.md
```

O relatório sai no console e é gravado em `gh_report_<user>.md`.

## Como pontua

Nota geral de 0 a 100, média ponderada de sete dimensões. As faixas:

| Nota | Leitura |
|---|---|
| ≥ 80 | PRONTO — recrutador te encontra e entende em 10 segundos |
| ≥ 65 | QUASE — bom perfil, com ganhos rápidos de uma tarde |
| ≥ 45 | TRABALHAR — a base existe mas o perfil não prova competência |
| < 45 | CORRIGIR — passa despercebido por recruiters |

| Dimensão | Peso | O que mede |
|---|---:|---|
| `projetos` | 0.25 | repos próprios com descrição, topics e licença. Forks não contam como autoria. |
| `readme` | 0.18 | repo de perfil existe e tem conteúdo substancial |
| `perfil` | 0.15 | bio, nome, hireable, empresa, site |
| `tecnologias` | 0.15 | diversidade de stack; penaliza dependência de linguagem de altíssimo volume |
| `atividade` | 0.12 | consistência de pushes |
| `prova_social` | 0.10 | stars, forks, seguidores |
| `higiene` | 0.05 | LICENSE, `.gitignore`, alerta de P0 para possíveis segredos commitados |

Os achados saem priorizados em P0 (bloqueia) / P1 (alto retorno) / P2
(refino), cada um com o motivo, o ganho estimado em pontos e — quando dá
para automatizar — o comando ou o bloco de configuração pronto.

## O alerta de segredos

`higiene` varre os nomes de arquivo dos repos e levanta P0 quando algo com
cara de credencial está versionado (`.env`, `id_rsa`, `*.pem`, `secrets.yml`).

Ele olha **só o caminho**, nunca o conteúdo — é um sinal para ir investigar,
não uma confirmação. Falsos positivos são filtrados por allowlist de
diretórios de teste/fixture, sufixos (`.env.example`, `.env.sample`) e
extensões que não são segredo (`*.pub`, `*.crt`, `*.lock`).

Se aparecer, o caminho real é: rotacione a chave primeiro, limpe o histórico
depois. O inverso não funciona.

## Limitações

- **A nota é heurística.** Os pesos refletem o que recrutadores leem primeiro
  (último push, projeto real, documentação), mas são uma afirmação sobre o
  processo, não algo validado contra dados de contratação. Os pesos estão no
  dicionário `WEIGHTS`, no topo do arquivo.
- **Só dados públicos.** Repos privados, Issues fechadas e dados de
  contratação não entram na análise.
- **Rate limit.** Sem token são 60 req/hora, insuficiente até para uma
  varredura. O token só precisa de leitura pública, sem escopos.

## Licença

MIT