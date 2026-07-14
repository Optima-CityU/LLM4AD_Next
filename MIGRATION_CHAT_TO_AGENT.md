# Migration: `llm4ad chat` Now Uses Agent-Based Builder

## Summary

The `llm4ad chat` command has been migrated to use the improved AI agent-based architecture (previously available as `chatv2`). This provides a more flexible, conversational experience with better self-verification capabilities.

## What Changed

### For Users

**Command Usage**:
- `llm4ad chat` now uses the agent-based builder (AgentScope ReAct agent)
- The legacy consultant-based implementation is now available as `llm4ad chat-legacy`
- All parameters remain compatible, though some legacy-specific options have been removed

**Key Differences**:
1. **More Conversational**: The agent engages in natural dialogue rather than following a rigid 3-phase process
2. **Self-Verifying**: Agent automatically runs generated tests to verify correctness
3. **Improved Context**: Agent can read and inspect files in the workspace
4. **Better Error Recovery**: Agent can iteratively fix issues by re-running tests

**Requirements**:
- Python >=3.12 (unchanged from project requirements)
- `agentscope` dependency (already included in base installation)

### Removed Parameters

The following parameters from the old `chat` command are no longer supported in the agent-based version:
- `--resume`: Session resumption (agent uses stateless turns)
- `--list-sessions`: Session listing (no persistent sessions in agent mode)
- `--max-repair`: Repair attempts (agent self-heals via tool calls)
- `--non-interactive`: Non-interactive mode (use `--prompt` instead)
- `--code-path` / `--data-path`: Direct paths (agent discovers via workspace inspection)
- `--lang`: Language selection (agent auto-detects)
- `--max-rounds` / `--max-tokens`: Context limits (agent manages context automatically)

**Migration**: If you were using these parameters, consider:
- For automation: Use `--prompt` to provide problem description directly
- For context: Place code/data files in the output directory before running
- For language: Agent will auto-detect based on your first input

### For Developers

**Architecture**:
- CLI entry point: `chat_agent_build()` in `cli.py:205`
- Core implementation: `llm4ad.agent.runner.run_agent_build()`
- Legacy fallback: `chat_consultant_legacy()` in `cli.py:1252`

**Module Status**:
- `llm4ad.agent`: **Primary** interactive builder
- `llm4ad.consultant`: **Deprecated**, will be removed in v2.0

## Migration Guide

### Basic Usage (No Changes)

```bash
# Simple interactive usage - works the same
llm4ad chat

# With provider selection - works the same
llm4ad chat --provider my_provider

# Direct problem input - works the same
llm4ad chat --prompt "Solve TSP with genetic algorithm"
```

### If You Used Advanced Options

```bash
# Old: Session management
llm4ad chat --resume session_abc123
llm4ad chat --list-sessions

# New: Agent is stateless, starts fresh each time
# (Sessions are a Web-only feature via chat-tune API)
llm4ad chat

# Old: Non-interactive mode
llm4ad chat --prompt "..." --non-interactive

# New: Just use --prompt (agent builds automatically)
llm4ad chat --prompt "..."

# Old: Providing code/data paths
llm4ad chat --code-path ./solver --data-path ./data

# New: Agent discovers files in workspace
# Just copy your files to the output directory first
cp -r ./solver ./data ./output_dir/
llm4ad chat --output ./output_dir
```

### Using the Legacy Version

If you need the old consultant-based behavior:

```bash
# Use chat-legacy instead
llm4ad chat-legacy --prompt "..." --resume session_id

# Note: This will show a deprecation warning
# and will be removed in a future version
```

## Benefits of the Agent-Based Approach

1. **Smarter Conversations**: Agent asks clarifying questions naturally
2. **Self-Healing**: Agent runs tests and fixes issues automatically
3. **Workspace Awareness**: Agent can inspect and understand existing code
4. **Unified Architecture**: Same agent powers both CLI and Web UI

## Rollback

If you encounter issues with the new agent-based builder:

1. **Use Legacy Command**: Run `llm4ad chat-legacy` instead
2. **Report Issue**: File a bug report with details
3. **Pin Version**: Install a previous version if needed:
   ```bash
   pip install llm4ad==<previous_version>
   ```

## Timeline

- **v1.x**: Both `chat` (agent) and `chat-legacy` (consultant) available
- **v2.0**: `chat-legacy` will be removed
- **Deprecation Period**: ~2-3 releases (approximately 6 months)

## Related Changes

- CLAUDE.md updated to reflect new architecture
- CLI help text updated
- Web UI already uses agent mode via `ChatTuneGenerationKind.AI_AGENT`

## Questions?

For issues or questions about the migration:
- Check the [AI Build Migration Plan](AI_BUILD_MIGRATION_PLAN.md) for detailed technical info
- File an issue on GitHub
- Use `llm4ad chat-legacy` as a temporary workaround
