# 01-endpoints — last run

Date: 2026-05-07
Stack: ai container post parameter-fix rebuild.

| Endpoint | Status | Notes |
|---|---|---|
| `GET /health` | 200 | `{"ok":true}` |
| `POST /ask` (×2) | 500 | Anthropic credit balance too low (see container logs) |
| `POST /search` (basic, with table_name) | 200 | Returns `{"results":[]}` for "university of california" — keyword filter eliminates non-matches and embeddings are degenerate (see top-level README finding) |
| `POST /search` (acro, "NASA") | 200 | Returns 5 unrelated names (KATICTRICE BARNES, DAVID LAGUERRA, …) ranked by trivial score 1/(60+r); kw branch has no matches and vec branch is degenerate |
| `POST /agent` | 500 | Same Anthropic credit error |

To re-run after credits / re-embedding:

```
./run.sh                         # writes out/<name>.json + out/<name>.meta
BASE=http://localhost:8000 ./run.sh
```
