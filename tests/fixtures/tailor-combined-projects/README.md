# Tailor combined-projects fixture (issue #46)

Synthetic candidate "Sam Jones" with two related-but-distinct GitHub repos.
The tailor LLM, pre-fix, occasionally combined these into a single H3 heading
like:

```markdown
### prompt-harness + nonprofit-prompts (github.com/sam/prompt-harness, github.com/sam/nonprofit-prompts)
```

After the prompt rule added in #46, the tailor must emit them as separate
entries:

```markdown
### prompt-harness (github.com/sam/prompt-harness)
- bullets describing the harness

### nonprofit-prompts (github.com/sam/nonprofit-prompts)
- bullets describing the static site
```

The fixtures here mirror the shape of @earino's real run that surfaced the
bug — two repos sharing a theme (one is a CLI tool, the other consumes
its output) with a tailor-tempting "they're related" relationship in
the folder summary. The live test asserts the tailor produces two separate
H3 blocks regardless of the relationship.

All names and content are fabricated. Safe to commit.
