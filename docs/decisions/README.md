# Decision Records

Record non-obvious technical decisions so future contributors understand *why*, not just *what*.

## Format

Create a new file: `YYYY-MM-DD-<topic>.md`

```markdown
# <Title>

## Context

What situation or problem prompted this decision?

## Decision

What was decided and what alternatives were considered?

## Why

Why this option over the alternatives? Include trade-offs.

## Revisit When

Under what conditions should this decision be reconsidered?
```

## When to Write One

- Choosing between libraries or frameworks
- Architectural patterns that aren't obvious from the code
- Decisions that were debated or could reasonably go another way
- Trade-offs where the losing option had real merit
