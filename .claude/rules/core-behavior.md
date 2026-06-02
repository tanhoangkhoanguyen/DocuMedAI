# Core Behavior

Before any action — edit, command, or response — apply these rules:

## Repo State Check
Read the actual files before responding. Check modified files and any files the user mentions explicitly. Never assume code contents from memory or prior context.

## Prior Work
Build on existing logic and patterns already in the repo. Prefer a cleaner or more scalable approach only if it is clearly better **and** within the user's stated scope.

## Scope
Implement exactly what was asked. No extra features, helpers, or unsolicited refactors unless the user explicitly requests them.

## Replies
Short and direct. No filler or over-explaining unless the user asks for depth. If the user's assumption is incorrect, correct it and ask for clarification — do not proceed based on a wrong premise.
