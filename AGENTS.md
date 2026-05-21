<!-- CCB_ROLES_START -->
## Role Assignment

Abstract roles map to concrete agents defined by the current project layout. Skills reference roles, not providers directly.

| Role | Agent | Description |
|------|-------|-------------|
| `designer` | `<agent-name>` | Primary planner and architect — owns plans and designs |
| `inspiration` | `<agent-name>` | Creative brainstorming — provides ideas as reference only |
| `reviewer` | `<agent-name>` | Scored quality gate — evaluates plans/code using Rubrics |
| `executor` | `<agent-name>` | Code implementation — writes and modifies code |

Role assignment authority: `.ccb/ccb.config` in the current project is the single source of truth.
The table above is illustrative only. When a skill references a role (e.g. `reviewer`), resolve it from `.ccb/ccb.config`.
<!-- CCB_ROLES_END -->

<!-- REVIEW_RUBRICS_START -->
## Review Rubrics & Templates

When the `reviewer` receives a review request from the `designer`, use these rubrics to score.

### Rubric A: Plan Review (5 dimensions, each 1-10)

| # | Dimension             | Weight | What to evaluate                                                  |
|---|-----------------------|--------|-------------------------------------------------------------------|
| 1 | Clarity               | 20%    | Unambiguous steps; another developer can follow without questions  |
| 2 | Completeness          | 25%    | All requirements, edge cases, and deliverables covered             |
| 3 | Feasibility           | 25%    | Steps achievable with current codebase and dependencies            |
| 4 | Risk Assessment       | 15%    | Risks identified with concrete mitigations                        |
| 5 | Requirement Alignment | 15%    | Every step traces to a stated requirement; no scope creep          |

**Overall Plan Score** = Clarity×0.20 + Completeness×0.25 + Feasibility×0.25 + Risk×0.15 + Alignment×0.15

### Rubric B: Code Review (6 dimensions, each 1-10)

| # | Dimension        | Weight | What to evaluate                                                |
|---|------------------|--------|-----------------------------------------------------------------|
| 1 | Correctness      | 25%    | Code does what the plan specified; no logic bugs                |
| 2 | Security         | 15%    | No injection, no hardcoded secrets, proper input validation     |
| 3 | Maintainability  | 20%    | Clean code, good naming, follows project conventions            |
| 4 | Performance      | 10%    | No unnecessary O(n²), no blocking calls, efficient resource use |
| 5 | Test Coverage    | 15%    | New/changed paths covered by tests; tests pass                  |
| 6 | Plan Adherence   | 15%    | Implementation matches the approved plan                        |

**Overall Code Score** = Correctness×0.25 + Security×0.15 + Maintainability×0.20 + Performance×0.10 + TestCoverage×0.15 + PlanAdherence×0.15

### Response Format

When scoring, return JSON with this structure.

#### Plan Review Response

```json
{
  "review_type": "plan",
  "dimensions": {
    "clarity": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "completeness": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "feasibility": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "risk_assessment": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "requirement_alignment": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." }
  },
  "overall": N.N,
  "critical_issues": ["blocking issues that MUST be fixed"],
  "summary": "one-paragraph overall assessment"
}
```

#### Code Review Response

```json
{
  "review_type": "code",
  "dimensions": {
    "correctness": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "security": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "maintainability": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "performance": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "test_coverage": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." },
    "plan_adherence": { "score": N, "strengths": ["..."], "weaknesses": ["..."], "fix": "..." }
  },
  "overall": N.N,
  "critical_issues": ["blocking issues that MUST be fixed"],
  "summary": "one-paragraph overall assessment"
}
```
<!-- REVIEW_RUBRICS_END -->
