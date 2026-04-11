# Empirical Test: Maven `-fae` (--fail-at-end) impact on dubbo cascade evaluation

**Date:** 2026-04-10
**Status:** Measurement experiment, NOT a permanent guardrail
**Conclusion:** `-fae` recovers some test runs but does not materially change `score_reliable` because the formula's precision penalty already clamps cascade-poisoned milestones to ~0.

## Method

1. Added an opt-in env var `EVOCLAW_MAVEN_FAE` to `harness/test_runner/core/test_executor.py:build_test_cmd()`. When set to `1`, the helper inserts `-fae` after the first `mvn ... test ` token, only if no fail-at-end / fail-never flag is already present. Off by default (0 lines of behavioral change otherwise).
2. Re-evaluated 4 cascade-prone milestones in parallel via `python -m harness.e2e.evaluator`, redirecting `--output` to `/tmp/fae_experiment/` so existing trial data was untouched. Each run used the same `source_snapshot.tar` as the original eval, so the only variable was `-fae`.
3. Reverted `test_executor.py` after the experiment.

## Per-milestone results

| Milestone | Cascade root | P2P_achieved | P2P_missing | F2P | N2P | F1 (score_reliable) |
|---|---|---|---|---|---|---|
| **M016.1** (backup) | JarScanner JarURLConnection wrap (SRS-induced) | 1136→**1952** (+816) | 5779→**4963** (-816) | 0/0→**0/0** | 0/6→0/6 | 0.0000→**0.0000** |
| **M003.2** (backup) | Inherited dubbo-native compile fail | 1141→**1957** (+816) | 5779→**4963** (-816) | 0/0→**0/0** | 0/5→0/5 | 0.0000→**0.0000** |
| **M006** (backup) | ParameterMeta.isStreamType visibility (capability) | 4873→**4985** (+112) | 2041→**1923** (-118) | 0/2→**0/2** | 0/6→0/6 | 0.0000→**0.0000** |
| **M004** (current) | Http1SseServerChannelObserver constructor (capability) | 4861→**4971** (+110) | 2041→**1925** (-116) | 0/2→**2/2** | 1/17→1/17 | 0.0019→**0.0041** |

## Reactor module status (with `-fae`)

| Milestone | SUCCESS | FAILURE | SKIPPED | Failed module |
|---|---|---|---|---|
| **M016.1** (backup) | 25 | 1 | 72 | `dubbo-native` |
| **M003.2** (backup) | 25 | 1 | 73 | `dubbo-native` |
| **M006** (backup) | 61 | 1 | 47 | `dubbo-rpc-triple` |
| **M004** (current) | 57 | 1 | 40 | `dubbo-rpc-triple` |

## Trial-level score impact

Computed by substituting `-fae` evaluation results into the trial summary and re-averaging F1 across all 12 dubbo milestones.

**Current trial (with FR10 SRS fix already applied):**

- Baseline (no `-fae`): **33.03%**
- With `-fae` substituted for M004: **33.05%**
- **Δ = +0.02 percentage points** (statistically meaningless)

**Backup trial (original `InputStream` SRS, three cascade milestones substituted):**

- Baseline: **0.01%**
- With `-fae` for M016.1, M003.2, M006: **0.01%**
- **Δ = +0.00 percentage points**

## Three findings

### 1. Maven `-fae` recovers far less than naive expectation

Hypothesis going in: `-fae` would let most modules build, eliminating ~80% of cascade-induced P2P_missing.

Reality: when `dubbo-native` (a low-level plugin) breaks, **72/73 modules still SKIP** even with `-fae`. The reactor only continues with modules whose **direct** dependencies are SUCCESS. Transitive dependents of `dubbo-native` are still blocked.

- `dubbo-native` failure: 25 modules SUCCESS, 73 SKIPPED → only ~12% of P2P recovered (-816 of ~5779)
- `dubbo-rpc-triple` failure: 57-61 modules SUCCESS, 40-47 SKIPPED → only ~5% of P2P recovered (-115 of ~2041)

**The cascade is mostly genuine dependency damage, not Maven reactor amplification.**

### 2. `score_reliable` already clamps cascaded milestones to ~0

The `score_reliable` formula uses precision with epsilon smoothing:

```
precision = (n_fixed + 1) / (n_fixed + n_broken + 1)
```

When `n_broken` (= P2P_failed + P2P_missing) is in the thousands, precision is dominated by `1 / n_broken`. Reducing `n_broken` from 2041 to 1925 changes precision from `2/2043 ≈ 0.001` to `4/1929 ≈ 0.002` — a doubling, but in absolute terms F1 still rounds to 0.004. The formula already handles cascade noise correctly.

### 3. Cascade can hide real F2P signal — but rarely materially

**M004 current was the only milestone where `-fae` flipped a real signal**: F2P went from `0/2` (cascade hid the test module) to `2/2` (test module ran successfully under `-fae`). The agent had actually implemented the F2P functionality correctly, but the eval reported `0/2` because the test module was SKIPPED.

This is a **false negative caused by cascade**, not capability subsidy. However:
- The score impact is still only +0.0022 in F1 (precision penalty drowns it)
- Across 12 dubbo milestones, this is the **only** case in our sample (1/4 = 25% if you generalize, but the cascade root must specifically host the F2P test module to trigger it)

## Decision: do NOT add `-fae` as a permanent guardrail

Empirical justification:

1. **It does not materially change `score_reliable`** (+0.02 pp on the strongest case, +0.00 pp aggregate). The score formula is already cascade-resistant.
2. **Most cascade is real dependency damage**, not measurement artifact. Removing it would understate the engineering cost of broken commits.
3. **The one false-negative case (M004 F2P 0/2 → 2/2) is too rare** to justify a class-level harness change.

This validates the principle: **EvoClaw should fix dataset bugs (e.g. M016.1 FR10 wording) but should not add measurement harness changes that mask agent capability shortfalls.**

## Reproduction

```bash
# Add the env var gate (already designed, see git history of test_executor.py for the snippet)
# then run the four evaluators in parallel:
for spec in 'M016.1:backup' 'M003.2:backup' 'M006:backup' 'M004:current'; do
  ...
done
```

Raw evaluation results are at `/tmp/fae_experiment/{M016.1,M003.2,M006,M004}_{backup,current}/evaluation_result.json` (cleaned up after the report was written). Reactor logs in `artifacts/*/eval_default.log`.
