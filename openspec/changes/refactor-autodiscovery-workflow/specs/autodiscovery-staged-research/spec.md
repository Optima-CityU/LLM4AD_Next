## ADDED Requirements

### Requirement: Four-stage AutoDiscovery workflow
The system SHALL present paper baseline, candidate discovery, evaluation design, and build/evolution as backend-owned AutoDiscovery stages using the same stage navigation and revision semantics as AutoProposal and AutoRebuttal. Building a task package SHALL occur only in the final stage.

#### Scenario: New workspace
- **WHEN** a user opens a new AutoDiscovery workspace with a source paper and configured model
- **THEN** the stage navigator shows the four named stages and opens the baseline stage without building a project

#### Scenario: No uploaded paper
- **WHEN** a user configures a model in an AutoDiscovery workspace without a source
- **THEN** the user can open the baseline conversation, ask the Agent to search arXiv by topic or ID, choose a result, and import its downloaded Markdown as the active source before the baseline is published

#### Scenario: arXiv retrieval fails
- **WHEN** arXiv search or download is unavailable
- **THEN** the workspace retains its manual upload path and does not present a search result as a complete source paper

#### Scenario: Missing predecessor
- **WHEN** a user attempts to start an unready later stage
- **THEN** the runtime explains which preceding artifact is required and does not run that stage

### Requirement: Source-grounded intermediate results
The system SHALL persist the baseline, candidate comparison, and evaluation contract as independent stage artifacts tied to the active source version, with exact source references and explicit unknowns. These artifacts SHALL be viewable before a runnable task exists.

#### Scenario: Baseline published
- **WHEN** the Agent publishes the baseline against the uploaded source
- **THEN** the right panel shows its algorithm summary, source-linked evidence, and unresolved facts without showing a fabricated task package

#### Scenario: Candidate comparison
- **WHEN** the Agent publishes candidate directions
- **THEN** the right panel retains distinct candidates, their evolvable boundaries, evidence, feasibility and evaluation risks, and the selected candidate if one has been chosen

#### Scenario: No defensible candidate
- **WHEN** uploaded materials do not support an evaluable algorithm direction
- **THEN** the Agent can publish the finding and required missing inputs without inventing a runnable project

#### Scenario: Evaluation contract
- **WHEN** an evaluation design is published for a selected candidate
- **THEN** the artifact distinguishes intended metrics and direction, baseline, data protocol, validity checks, reproducibility, and unresolved evidence from observed measurements

### Requirement: Revisions do not erase research history
The system SHALL mark dependent stage artifacts stale when a source or upstream decision changes, while preserving prior published artifacts and independently linked evolution tasks. The user SHALL be able to inspect prior candidates and distinguish them from the current revision.

#### Scenario: Source changes after publication
- **WHEN** the active paper source is edited
- **THEN** dependent AutoDiscovery stages become stale, previously created tasks are not deleted, and the old artifacts remain identifiable as superseded

#### Scenario: Candidate revised
- **WHEN** a user changes the selected candidate and republishes the candidate stage
- **THEN** the evaluation and build stages become stale without silently changing an already-running project

### Requirement: Stage-specific guidance and presentation
The system SHALL inject only the Skills relevant to each AutoDiscovery stage and SHALL render each stage's result with suitable bilingual, readable components rather than a single unstructured task-package list.

#### Scenario: Stage switch
- **WHEN** a user switches from baseline to evaluation design
- **THEN** the left conversation receives the evaluation Skill and the right result surface displays the evaluation artifact, not unrelated package controls
