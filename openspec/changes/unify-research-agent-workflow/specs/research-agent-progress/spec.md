## ADDED Requirements

### Requirement: One automatic workflow conversation
The proposal and rebuttal assistants MUST use a stable coordinator conversation, preserve stage artifact contracts, advance stages through their existing dependencies and allow user interruption.

#### Scenario: Fresh workflow
- WHEN the user provides sufficient project materials and asks to proceed
- THEN the main agent delegates stage work, publishes each completed artifact and continues without requiring stepper clicks.

#### Scenario: Existing artifacts
- WHEN a workflow is reopened or resumed
- THEN the agent reads current persisted stage state and continues without silently repeating completed stages.

### Requirement: Observable subagent execution
The interface MUST display actual parent/child execution state and preserve access to results and files without a proposal/rebuttal top stepper.

#### Scenario: Background agent
- WHEN a child continues after the parent's turn
- THEN the runtime remains connected until completion, cancellation or its existing idle ceiling.

#### Scenario: Session replacement
- WHEN the user switches workspace or rewinds history
- THEN progress belongs only to the current session and superseded artifacts are invalidated.
