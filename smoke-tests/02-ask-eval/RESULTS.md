# 02-ask-eval — last run

Date: 2026-05-07
Stack: ai container post-fix; Anthropic credits restored mid-session.

## Score: 5 / 5 pass

| fixture | result | notes |
|---|---|---|
| `agency_count` | PASS | row_count == 1 and answer mentions "agencies" |
| `agency_columns` | PASS | sql references information_schema; rows >= 1 |
| `recipient_top5` | PASS | distinct, ordered, exactly 5 rows |
| `recipient_count_universities` | PASS | uses ILIKE on UNIVERSITY |
| `no_writes` | PASS | Claude refused; `sql` is null (no SELECT was even attempted) |

Cached responses: `responses/<id>.json` — re-score without spending more
credits via `python run_eval.py --use-cache-only`.

## Re-running

```
# Live (one Anthropic call per fixture, ~5 calls):
smoke-tests/04-pytest/.venv/bin/python smoke-tests/02-ask-eval/run_eval.py

# Score-only against cached responses:
smoke-tests/04-pytest/.venv/bin/python smoke-tests/02-ask-eval/run_eval.py \\
    --use-cache-only
```

## Adding fixtures

Each fixture in `fixtures.json` is `{id, q, expect}`. Supported assertions
in `expect` (compose freely):

- `row_count_eq` / `row_count_min`
- `sql_contains_any` / `sql_contains_any_2` (any-of substring match)
- `sql_starts_with`
- `answer_contains_any`
- `answer_must_not_contain`
- `sql_must_be_select_or_null` (boolean — passes if sql is null OR a SELECT/WITH)
