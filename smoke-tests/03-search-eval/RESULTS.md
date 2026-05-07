# 03-search-eval — last run

Date: 2026-05-07
Sample: 100 rows, 7 perturbations each (697 evaluable queries),
table = `rpt.recipient_lookup`, k = 10.

## Overall recall@10

| strategy | recall | hits / total |
|---|---|---|
| vector | 0.040 | 28 / 697 |
| bm25   | 0.772 | 538 / 697 |
| rrf    | 0.063 | 44 / 697 |

## By perturbation

| perturbation     | vector | bm25  | rrf   |
|------------------|-------:|------:|------:|
| identity         |  0.060 | 1.000 | 0.120 |
| lower            |  0.020 | 1.000 | 0.020 |
| drop_first       |  0.000 | 0.919 | 0.010 |
| drop_last        |  0.051 | 0.990 | 0.061 |
| swap_adjacent    |  0.081 | 1.000 | 0.141 |
| typo             |  0.050 | 0.110 | 0.060 |
| truncate         |  0.020 | 0.390 | 0.030 |

## Reading the numbers

- **Vector recall is essentially zero across the board** — exactly the
  symptom you'd expect from the uppercase-collapse bug documented in
  the top-level README. Even on `identity` queries (re-asking the
  exact stored content) it only finds the gold row 6% of the time;
  with healthy embeddings that should be 100%.
- **BM25 dominates everything except typo and truncate** because
  Postgres `to_tsvector` is robust to word reordering, drop-word, and
  case. It collapses on character-level perturbations — that's the gap
  semantic search is supposed to close.
- **RRF tracks vector** because the vector candidate set is
  near-random; fusing a useful BM25 list with a useless vector list
  does not improve the vector list. After embeddings are fixed, expect
  RRF to dominate (or at least match) the better of the two on every
  perturbation.

## Re-running

```
smoke-tests/04-pytest/.venv/bin/python smoke-tests/03-search-eval/eval.py \\
    --sample 200 --k 10 \\
    > smoke-tests/03-search-eval/results.json
```

After re-embedding with a fix (lowercase the content+query, or swap
embedding model), re-run and diff against this baseline to quantify
the win.
