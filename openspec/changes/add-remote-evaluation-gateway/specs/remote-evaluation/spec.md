## ADDED Requirements

### Requirement: Authorized durable remote evaluation
Gateways SHALL accept jobs only for allowed users, with a persisted recovery identity and compatible evaluator contracts.

#### Scenario: Selected gateways are offline
- WHEN a remote task has no online selected gateway
- THEN it waits without creating a failed candidate or switching to unselected gateways.

### Requirement: Recoverable execution
Accepted jobs SHALL retain gateway ownership across disconnections and require confirmation before repeating uncertain work.

#### Scenario: Gateway reconnects
- WHEN an accepted job has a durable completed result
- THEN the result is uploaded idempotently and counted once.

#### Scenario: Gateway loses execution state
- WHEN a restarted gateway cannot identify the external execution
- THEN it reports recovery required instead of repeating the evaluation.
