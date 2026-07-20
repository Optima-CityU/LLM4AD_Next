# llm4ad-task-builder — a portable Agent Skill

A single, agent-agnostic Skill that teaches any capable coding agent how to build a
runnable **LLM4AD_Next task package** (evaluator + algorithm with EVOLVE markers +
config.yaml + test scripts + sample data) and how the package is used on the
LLM4AD platform.

It follows the open [Agent Skills](https://agentskills.io) `SKILL.md` format
(YAML frontmatter + Markdown body + reference files), which works across Claude
Code, Qwen Code, and other agents.

## Contents

```
llm4ad-task-builder/
├── SKILL.md                       # the skill: package knowledge + how to use the platform
├── reference/
│   ├── config-template.yaml       # annotated 10-section config.yaml skeleton
│   └── example-tsp.md             # pointer to the full working example in this repo
└── README.md                      # this file
```

## Install / use

**Claude Code** — copy or symlink this directory into your skills folder:
```bash
cp -r skills/llm4ad-task-builder ~/.claude/skills/       # user scope
# or project scope: <repo>/.claude/skills/llm4ad-task-builder
```
Claude auto-discovers it by the `name`/`description` frontmatter and loads the body
when a task matches.

**Qwen Code** — place it under Qwen Code's skills directory (see Qwen Code's Skills
docs); the same `SKILL.md` format is supported.

**This platform (AgentScope agent)** — the platform's beta build agent loads
`SKILL.md`'s body into its system prompt at runtime, so the same knowledge drives
the in-platform agent as well. (Maintained as one source; see
`src/llm4ad/agent/`.)

## Scope

This skill is **domain knowledge** (what a package is, its contracts, how to run it),
not a tool implementation. It assumes the host agent already has file read/write and
command execution. The platform's interactive UX (step-by-step option cards, the
plan/confirm/lock flow) lives in the platform agent, not in this portable skill.
