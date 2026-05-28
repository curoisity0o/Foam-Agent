# input_writer occasionally omits *Final sub-dictionaries in fvSolution

## Summary

The `input_writer` service occasionally generates `fvSolution` files that are missing required `*Final` sub-dictionaries (e.g., `pFinal`, `UFinal`, `kFinal`) for pressure-velocity coupling solvers (PISO/PIMPLE). This causes simulation startup failures, requiring the reviewer loop to fix — wasting 1-2 error correction iterations.

## Background

PISO/PIMPLE algorithms perform multiple inner correction loops per time step (controlled by `nCorrectors`). The `*Final` sub-dictionaries control solver settings for the **final inner loop**, while regular entries control **intermediate loops**.

For example, with `nCorrectors = 3` and pressure field `p`:

```
Pressure corrections within one time step:
  Iteration 1: uses p       { relTol 0.05 }  → loose convergence, fast
  Iteration 2: uses p       { relTol 0.05 }  → loose convergence, fast
  Iteration 3: uses pFinal  { relTol 0 }     → strict convergence, accurate
```

The idea: intermediate iterations use relative tolerance (`relTol`) for speed; the final iteration uses `relTol 0` (absolute tolerance) to ensure full convergence. This applies to `UFinal`, `kFinal`, `epsilonFinal`, etc. as well.

Foundation OpenFOAM v10 **enforces** that these entries exist — missing entries cause an immediate fatal error. Older versions did not check, so most LLM training data and online examples omit `*Final`.

## Error

```
keyword pFinal is undefined in dictionary "system/fvSolution/solvers"
keyword UFinal is undefined in dictionary "system/fvSolution/solvers"
```

## Reproduction

This issue is **non-deterministic** — whether `*Final` entries are omitted depends on LLM behavior in each run. Test results with DeepSeek V4-Pro on OpenFOAM v10:

| Test Case | Initially Missing | Fix Loops |
|-----------|------------------|-----------|
| icoFoam cavity | `pFinal` | known |
| pisoFoam cavity (RAS) | none | 1 (other error) |
| pimpleFoam planarCouette | none | 1 (other error) |

Test scripts: refer to `tests/test_lid_driven_cavity_services.py` for the existing test framework

## Root Cause

1. **No explicit prompt constraint for fvSolution** — `_build_prompts()` in `src/services/input_writer.py` has conditional constraints for `controlDict` (no post-processing), but none for `fvSolution` to ensure `*Final` entries are present.

2. **Non-deterministic LLM behavior** — The RAG database contains 66 `pFinal` and 14 `UFinal` references, but whether the LLM includes them depends on attention allocation during each generation, making the issue intermittent.

## Suggested Fix

Add a conditional constraint in `_build_prompts()` when generating `fvSolution`:

```python
if file_name == "fvSolution":
    code_user_prompt += (
        "CRITICAL: For pressure-velocity coupling solvers (PISO/PIMPLE), "
        "the solvers dictionary MUST include "
        "corresponding *Final sub-dictionaries for each solver entry "
        "(e.g., pFinal for p, UFinal for U). "
        "Typically { $<field>; relTol 0; }. "
        "Also ensure the PIMPLE/PISO sub-dictionary matches the solver's requirement."
    )
```

This follows the existing `controlDict` conditional constraint pattern and only takes effect when generating `fvSolution`.

## Impact

- `src/services/input_writer.py` — `_build_prompts()` function
- Affects all PISO/PIMPLE solvers (icoFoam, pisoFoam, pimpleFoam, interFoam, etc.)

## Status

- [x] Documented — reviewer loop provides fallback
- [x] Prompt constraint added in `_build_prompts()` — test shows 0 loops (was 1 loop with DeepSeek V4-Pro)
- [ ] Note: All testing was done with DeepSeek V4-Pro. The issue is non-deterministic and the reviewer loop already catches and fixes missing entries automatically. The prompt constraint reduces the chance of wasted error-correction iterations (saving ~1-2 LLM calls per occurrence), but is not strictly necessary for correctness.
