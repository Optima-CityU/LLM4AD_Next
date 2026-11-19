# Reviewer analysis

Adapted from AutoRebuttal's reviewer outline and reviewer-model guidance. See
[the adaptation attribution](../../ATTRIBUTION.md).

For the review passages relevant to the request, identify atomic concerns
before replying. Distinguish these useful labels when applicable:

- `W`: weakness or objection requiring a direct defense, correction, or concession.
- `Q`: question requiring a factual answer or clarification.
- `M`: minor point or minor comment that still deserves an explicit response.

For every concern record its reviewer, type, severity, likely answer source,
response move, and source references. Keep distinct concerns separate even
when one sentence mentions several issues.

When reviewer-level analysis is useful, keep one assessment per reviewer. Summarize sentiment, movability, attitude,
primary concerns, and linked concern IDs. Movability is an editorial planning
judgment, not a prediction of acceptance. Prioritize factual errors, central
validity concerns, and high-impact misunderstandings over cosmetic requests.

Examples:

- “No comparison with X” -> `W`, comparison/baseline, high if central.
- “How was the threshold selected?” -> `Q`, implementation detail, medium.
- “The method requires labels at inference” when the method does not -> `W`,
  factual misunderstanding, high, with the exact paper section as evidence.
- “Please define the symbol in Equation 3” -> `M`, presentation/clarity, low,
  with the equation location as evidence.
