# Claude Rules Location

How to add or edit Claude rules in this project.

## Directory

All rule files live in:
```
PROJECT_ROOT/.claude/rules/<rule-name>.md
```

```
.claude/
├── rules/
│   ├── core-behavior.md       ← always-active behavioral rules
│   ├── rules-location.md      ← this file
│   ├── self-improvement.md    ← pattern learning
│   └── init-guide.md          ← /init command behavior
└── commands/
    └── init.md                ← custom /init slash command
```

## Naming
- kebab-case filenames
- `.md` extension (not `.mdc` — that is Cursor-specific)
- Names describe the rule's purpose

## To make a rule "always apply"
Add a line to `CLAUDE.md` under `## Rules`:
```
- Read `.claude/rules/<rule-name>.md` before acting
```
Claude loads CLAUDE.md at the start of every session, so the instruction is always active.

## To make a rule "apply to specific files or tasks"
Do not add it to CLAUDE.md. Instead, add a trigger note at the top of the rule file:
```
> This rule applies when editing files in `src/`.
```
Claude will pull it when relevant based on the description.

## Format
Plain markdown. No YAML frontmatter required. Start with a `# Title` and short description.

## Never place rule files
- In the project root
- In `src/` or any code directory
- Outside `.claude/rules/`
