# Design

An opt-in `auto_workflow` runtime session uses one stable coordinator ID. Existing stage run IDs and artifact persistence remain authoritative. The workspace-scoped MCP bridge exposes workflow context and stage-targeted publication. A coordinator Skill selects the next dependency-ready stage, delegates to a stage-specific agent with explicit Skills, waits for results, publishes, and continues; it asks for author input only when necessary.

The embedded profile forwards typed SDK agent definitions. Child roles receive their current stage, source/read paths, artifact context, preferred explanation language and installed Skills. Existing tool and write policies remain in effect. Background Agent/Task launches keep the stream open until completion or cancellation.

CloudCLI derives a bounded progress snapshot from actual session records, sends it to its same-origin parent, and restores it from history after refresh. A shared progress/results right panel replaces the proposal/rebuttal top stepper. It uses a compact connected timeline, status badges, expandable child activity and artifact links, with reduced-motion support. No estimated percentages or fabricated completion states.
