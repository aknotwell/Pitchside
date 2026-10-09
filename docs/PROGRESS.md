# Build progress log

A running log of the autonomous build, newest checkpoint last. Each
checkpoint is a commit. Items marked **YOU** need a human.

## Checkpoint 1: Azure foundation (2026-10-08)

**Subscription**: "Azure subscription 1" (`cbb2ef3b-ed42-4e42-9c51-99e5895ece87`),
tenant "Default Directory", owner `vmoose@outlook.com`. Offer `FreeTrial`, with
the spending limit **on**, so it can't bill past the $200 credit.

Done:

- **Local tools.** Installed user-level (no sudo):
  - `az` 2.91.0 via `uv tool`, on Python 3.13
  - `azd` 1.35.1, `bicep` 0.48.1 and `uv` 0.12.24 in `~/.local/bin`
  - `func` 4.15.2 via npm
- **Resource providers registered**: App, ContainerRegistry, EventHub,
  ServiceBus, EventGrid, DocumentDB, SignalRService, Web, Logic,
  CognitiveServices, Storage, KeyVault, OperationalInsights, Insights,
  ManagedIdentity, LoadTestService, AlertsManagement, Chaos,
  MachineLearningServices, PolicyInsights.
- **Resource group**: `rg-matchlens-dev` in `eastus2`.
- **Budgets** (subscription scope, emailing `vmoose@outlook.com`):
  `matchlens-100`, `matchlens-200` and `matchlens-300`. Each alerts at 80%
  actual, 100% actual and 100% forecast.
- **Region check for `eastus2`**:
  - Flex Consumption is available.
  - The model catalogue there has GPT-6 (`gpt-6-sol`/`astra`/`luna`,
    `gpt-6.1-sol`), GPT-5.4–5.6, Claude (Haiku/Sonnet/Opus 4.5–5.5, Fable 5),
    DeepSeek V4, Grok 4.x and Llama.

### Model quota on the free trial (eastus2, thousand TPM)

| Model | Quota |
|---|---|
| `gpt-5-mini` (GlobalStandard) | **500** |
| `gpt-4.1-mini` | 200 |
| `gpt-oss-120b` | 5000 |
| `grok-3`, `grok-3-mini`, `grok-4-fast-*` | 50 |
| `DeepSeek-R1` / `V3-0324` / `V3.2-Speciale` | 20 |
| `text-embedding-3-small` | 1000 |
| **Every GPT-5.4+, GPT-6 and Claude model** | **0** |

**Decision:** build on `gpt-5-mini`, with `gpt-4.1-mini` as the fallback
deployment. Claude needs a paid (non-credit) subscription. GPT-6 needs a quota
request, and free trials may not be able to file one.

### YOU (when back)

1. Try a quota request for `gpt-6-sol` and `gpt-6-astra` (GlobalStandard,
   eastus2). In the portal: Foundry → Quotas, or <https://aka.ms/oai/quotaincrease>.
   If it's blocked on the free trial, upgrade to pay-as-you-go (plan was by
   Oct 20 at the latest) and file it again.
2. Register every teammate before Oct 20, 12:00 PT.
