# Init Command Guide

When `/init` is invoked, generate `CLAUDE.md` following these constraints.

## Length Target
- **150–200 lines** for the main `CLAUDE.md`
- If the full content would exceed 200 lines, split into reference files

## What Always Stays in Main CLAUDE.md
- Project overview: 3–5 lines
- Core insight / key concept: 2–3 lines
- Architecture summary: one line per module, no implementation detail
- Most common commands: 3–5 examples only
- `## Rules` section (required — points to `.claude/rules/core-behavior.md`)
- `## References` section at the bottom (if split was needed)

## Split Strategy
Move verbose sections to `.claude/docs/`:

| Section | Target file |
|---------|------------|
| Module descriptions, data flow | `.claude/docs/architecture.md` |
| Full CLI reference | `.claude/docs/commands.md` |
| Output file schemas | `.claude/docs/outputs.md` |
| Troubleshooting / debugging | `.claude/docs/debugging.md` |
| Development notes | `.claude/docs/dev-notes.md` |

## References Section Format
```markdown
## References
- [Architecture & Data Flow](.claude/docs/architecture.md)
- [CLI Commands](.claude/docs/commands.md)
- [Output Schemas](.claude/docs/outputs.md)
- [Debugging](.claude/docs/debugging.md)
```

## Usage Rule
During a task, read only the reference file relevant to that task — not all of them. Avoid loading unrelated sections into context.
