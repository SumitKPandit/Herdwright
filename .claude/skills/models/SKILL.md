---
name: models
description: Keep the model ranking that picks each worker's model and reasoning effort up to date, and explain the choice. Use when `bin/zed runtime status` / `models show` says the ranking is missing or stale, when the user asks which model workers use, or when they change their model policy.
---

# Models

Every `spawn` picks a model and effort per the profile's `config/runtimes.json` → `model_policy`:
- `default`: no flags; each CLI uses its own default.
- `attractive-quadrant` (needs a ranking): from `config/model-ranking.json`, take the models in
  the "most attractive quadrant" of Intelligence vs Cost per task, meaning at least the median
  intelligence and at most the median cost of all ranked models. Pick the most intelligent one
  the runtime offers (cheaper wins a tie). If none of the runtime's models is in that quadrant,
  the same rule is applied to the runtime's own models. If that's empty too, take the cheapest
  of its models with at least their median intelligence.
- `runtimes.<rt>.model` / `effort` in `runtimes.json` override the policy for that runtime.
- `spawn ... model= effort=` overrides it for one session, only when the user asks.

`bin/zed models show` prints the ranking's age and what each runtime would get, with the reason.
Optional `model_policy.max_effort` (e.g. `"high"`) caps the reasoning effort the ranking picks, to
save tokens; `spawn ... effort=` still overrides it for a single session.

## Refresh the ranking
When it's missing or older than `model_policy.max_age_days`:
1. Look up the ranking the policy names (`model_policy.source`, e.g. Artificial Analysis
  "Intelligence Index vs Cost per task"). Read the current numbers from the source itself. Never
  fill numbers from memory, and say if you couldn't get them.
2. Write a JSON file in your scratch space:
   ```json
   {"source": "<what it is>", "url": "<where you read it>", "as_of": "<date on the source>",
    "entries": [{"name": "<as the source names it>", "model": "<model id, e.g. claude-opus-5.5>",
                 "effort": "<reasoning variant if the source ranks one, else omit>",
                 "intelligence": 0, "cost_per_task": 0}]}
   ```
   One entry per model and reasoning variant. `model` uses the vendor's id with dots or dashes;
   Zed maps it to each runtime (Copilot: the ids in `copilot help config`; Claude Code: any
   `claude-*` model). `effort` must be one the runtime accepts, otherwise it is dropped.
3. `bin/zed models import <file>`, then check `bin/zed models show` looks sensible. Commit the profile.
4. Tell the user in one line what changed (e.g. "workers now use X at high effort; was Y").
