# Research Methods and Technical Route Method

The methods stage explains how the project will answer each key problem and how the resulting claims will be evaluated.

## 1. Build the overall technical route

Map every objective and key problem to a method component. Establish a dependency sequence such as:

`problem formulation -> mechanism analysis -> method or system design -> controlled evaluation -> integration and validation`

Use the sequence appropriate to the discipline; do not force this example when the research is theoretical, qualitative, or empirical in another form.

For each component, name its input, output, dependency, and decision criterion. The route should reveal how evidence accumulates toward the overall objective.

## 2. Develop each method component

Use the following structure where applicable:

1. **Problem formulation:** define objects, assumptions, variables, observations, constraints, and target quantities.
2. **Baseline or comparison boundary:** identify what the method must improve, explain, or distinguish without leaking an expected answer.
3. **Core mechanism:** explain the proposed principle and why it addresses the named key problem.
4. **Procedure:** describe the major algorithmic, experimental, analytical, or data steps in executable order.
5. **Evaluation:** define evidence, comparison, ablation, robustness, uncertainty, or failure analysis appropriate to the claim.
6. **Risk and fallback:** identify likely failure conditions and a scientifically valid alternative path.

Include enough technical depth for a reviewer to judge feasibility. Do not bury missing design choices under general phrases such as "use advanced models" or "conduct extensive experiments".

## 3. Formalization and notation

When mathematics is useful:

- define every symbol before or immediately after first use;
- distinguish sets, scalars, vectors, random variables, models, and objectives consistently;
- state domains and constraints explicitly;
- explain what each equation means and how it connects to the procedure;
- use labels and references for equations reused later;
- preserve Typst syntax and semantic structure.

Do not introduce formulas merely to make the section appear technical. Never copy an equation whose assumptions do not match the project.

## 4. Evaluation design

For each intended claim, specify the evidence needed to support it:

- datasets, cases, participants, systems, proofs, or observations, when author-provided or source-grounded;
- baselines and controls that address the actual research question;
- metrics and uncertainty treatment;
- ablation or sensitivity analysis for claimed mechanisms;
- validity checks and failure criteria;
- reproducibility artifacts and resource limits when relevant.

Ask the author when data access, evaluation standards, or experimental conditions materially change feasibility. Do not invent access to proprietary data or equipment.

## 5. Explain methodological contribution

For each major component, state the specific difference from established approaches and why it matters under the proposal's conditions. Avoid unsupported "first" or "groundbreaking" language. Innovation will be summarized later; here it must be technically grounded.

## 6. Quality gate

Confirm that:

- every objective and key problem has a corresponding method and evaluation path;
- procedures are detailed enough to execute or audit;
- assumptions and failure conditions are visible;
- the methods do not depend on unconfirmed resources or results;
- terminology, citation keys, and symbols remain consistent;
- the technical route is coherent rather than a collection of fashionable components.
