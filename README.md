# Radar do Studa

Site que todo dia de manhã acha os vídeos que mais viralizaram no nicho do Studa no **Instagram**, no **TikTok**, no **YouTube** e nos **Shorts**, e mostra tudo ordenado pelo **multiplicador** (views ÷ seguidores de quem postou).

- Site: `https://joaomauricioporto.github.io/radar-studa/` (dá para pôr na tela inicial do celular).
- A busca roda sozinha todo dia por volta das 8h (GitHub Actions, grátis). Para buscar na hora: aba **Actions** → **Radar** → **Run workflow**.
- Temas, período e mínimo de views ficam em `radar/config.json`. Dá para editar pelo próprio GitHub.

## Ligar as buscas (uma vez só)

Os dois segredos ficam em **Settings → Secrets and variables → Actions**.

| Segredo | Para quê | Onde pegar |
|---|---|---|
| `YOUTUBE_API_KEY` | YouTube e Shorts | Google Cloud: ative a "YouTube Data API v3" e crie uma chave de API (grátis) |
| `APIFY_TOKEN` | TikTok e Instagram | apify.com, conta grátis (US$ 5 de crédito por mês) → Settings → API & Integrations |

## Quanto custa

- **YouTube e Shorts:** nada. A busca diária usa uns 42 dos 100 pedidos de busca que o Google dá por dia.
- **TikTok e Instagram:** nenhum dos dois abre esses dados. O radar usa a Apify (`clockworks~tiktok-scraper`, `apify~instagram-hashtag-scraper` e `apify~instagram-profile-scraper`), que cobra por item coletado e vai contra os termos das duas redes. O padrão (16 vídeos do TikTok, 16 Reels e 8 perfis por dia) foi pensado para caber nos US$ 5 grátis da Apify; confira o gasto em console.apify.com → Billing antes de aumentar `por_busca` ou `por_hashtag`.
- Os vídeos achados ficam no radar pelos 14 dias do período, então a lista cresce dia a dia mesmo com poucas buscas.

## Como funciona

`radar/buscar.py` (só Python padrão) busca, calcula multiplicador, views por dia, engajamento e quanto cada vídeo ganhou desde a busca anterior, e grava `docs/dados.json`. O workflow publica a pasta `docs/` no GitHub Pages.
