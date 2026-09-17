# Brief

## The problem

An infrastructure agent needs context from cloud inventory, Terraform state, Kubernetes, and a service catalog. These sources describe overlapping parts of an environment, but their identifiers, terminology, observation times, and responsibilities differ.

Build a small, local context layer that answers operational questions without turning incomplete or misleading evidence into facts.

Treat this as the first increment of a platform another engineering team will use. Your responsibility is not just to produce output. It is to make the meaning of that output clear, verify the important decisions, and explain what the system is ready to support.

We are looking for useful engineering judgment, not the largest implementation.

## Time, tools, and scope

Spend **no more than four hours**, including reading, coding, testing, and notes. Stop at the limit and state what remains incomplete. Build a working, verified end-to-end slice before broadening it. Unfinished requirements are missing evidence, not an automatic rejection; extra features receive no additional credit.

Use **Python**. Choose a local representation you can justify: typed objects, relational tables, a graph, or another simple approach. No particular database or framework is preferred. A deterministic full rebuild from the supplied files is sufficient.

AI coding tools and public documentation are allowed throughout the take-home and live session. You remain responsible for the behavior you submit. We do not require an AI transcript, a particular subscription, or an example of disagreeing with an agent.

**Not required:** production connectors, live credentials, cloud deployment, a UI, an LLM at runtime, vector search, streaming infrastructure, a general-purpose ontology, or a production authorization system.

## The inputs

We will provide small, synthetic, documented extracts—not complete provider exports.

| Input | Contents |
|---|---|
| `aws_inventory.json` | Selected EC2 and database records, provider identifiers, state, and tags |
| `terraform_state/` | Selected workspace state records, resource addresses, provider identifiers, and resource modes |
| `k8s_resources.json` | Selected Deployments, ReplicaSets, Pods, and Nodes, including object references and placement information |
| `service_catalog.csv` | Application identities, workload references, application teams, and declared dependencies |
| `manifest.json` | Trusted tenant context, collection scopes, observation times, completeness information, freshness budgets, and a fixed evaluation time |

Use the manifest's evaluation time, not your computer's current date. A collection is complete only within its declared scope. An unavailable collection is not an empty inventory. The extract notes will define the fields and any simplifications; implement that bounded contract rather than reconstructing full provider behavior.

The data includes justified matches, misleading similarities, differing observations, and some links that cannot be established from the available information. Neither matching everything nor marking everything unknown is a useful solution.

## Required work

### 1. Define the model and its rules

Document the entities and relationships you support. Explain what makes an entity the same entity across records, the scope in which its identifiers are meaningful, and when two records describe related objects rather than the same object.

Your representation must retain enough evidence to explain important attribute and relationship claims, including their source and relevant observation time. It must also represent unresolved links and disagreements without silently converting them into certainty. Numerical confidence scores are optional; explanations are not.

Tenant scope must be enforced in resolution and query results, including supporting evidence. Treat the manifest as trusted tenant context for this exercise; do not implement authentication.

Choose the schema. We are not prescribing tables, classes, edge names, or a universal abstraction. Explain at least two rules your implementation is intended to preserve.

### 2. Build a runnable pipeline and answer two questions

Ingest the four source families, construct your model, and answer the following **by querying that model**. Do not hard-code answers or independently reconstruct identities inside each question.

#### Question A — Terraform management evidence

For the requested tenant, which EC2 instances are observed as running in production, and what evidence exists that they have a **managed-resource binding in the supplied Terraform state**?

Distinguish a binding found, no binding found in the supplied scope, and unavailable evidence where relevant. Include the supporting workspace and observation time. Explain what the supplied evidence does—and does not—establish about current management.

This is an inventory question, not a request to recommend removing resources.

#### Question B — Application context and responsibility

For the requested tenant's production catalog application `payments-api`, show its declared dependencies and the supported path from its workloads through Kubernetes Nodes to cloud resources.

Identify the application team and any separately evidenced infrastructure operator. Make the meaning of each connection clear: a workload running on a resource does not automatically establish every kind of dependency or responsibility.

Show what you can substantiate, where the path stops, and why. Distinguish source statements from relationships or conclusions derived by your code.

#### Output contract

Return machine-readable answers, such as JSON or structured tables. Each answer should include its query scope, evaluation time, supporting record references, and limitations that materially affect interpretation. A reference can be a source file plus a record identifier or locator; do not duplicate the entire input in every answer.

Also produce a small **issues report** covering unresolved links, conflicting observations, and freshness or coverage limitations. For important issues, state what additional evidence would resolve them. Limitations must appear where they affect results, not only in a README disclaimer.

Reprocessing the same input must not duplicate entities or relationships or change their semantic identities. A rebuild is acceptable; incremental processing and historical queries are not required.

### 3. Verify the decisions that matter

Include a small set of executable tests demonstrating:

- A justified cross-source match and a substantive query result.
- A misleading similarity or scope collision that does not contaminate another entity or tenant's answer.
- An unresolved, conflicting, or stale case that remains correctly qualified.
- Repeatable results when the same inputs are processed again.

Four to six focused tests can be sufficient. Test quality matters more than count. You may create small counterexamples by modifying copies of input records. Be ready to explain which plausible implementation error a test would catch.

### 4. Leave a concise engineering handoff

Include run and test commands, generated answers, and a short design note—**approximately 600–800 words**, excluding a schema table or diagram.

| Topic | What to explain |
|---|---|
| Model and design | What the important entities and relationships mean; why you chose this representation |
| Consequential decisions | Two decisions, their alternatives, and evidence that would cause you to revisit them |
| Verification | One assumption you checked, how you checked it, and what you changed or retained |
| Readiness and next steps | What a read-only internal consumer may rely on, the highest-risk remaining gap, and the next one or two changes you would prioritize |

State roughly how you used the time and identify unfinished work. No detailed activity tracking is expected. A production-ready system is not expected; an honest, specific readiness assessment is.

You may challenge a requirement or interpretation that would produce a misleading result. Explain the concern and deliver a useful, bounded alternative rather than silently changing the meaning or stopping at a warning.

## The follow-up

In a **60-minute working session**, we will trace a result, give you a small change to the existing input, and discuss a production failure and a possible new source. We will ask you to predict behavior, inspect your code, and demonstrate a narrow change or regression test. A production connector or incremental engine will not be expected.

Come ready to explain and revise, not give a polished presentation. AI tools remain allowed.

We assess modeling judgment, clean and changeable code, verification, handling of uncertainty, and ownership of the resulting system. We do not score typing speed, architecture vocabulary, presentation polish, or the number of generated files.

**Submit:** code, run instructions, tests, generated answers/issues, and the short design note. Do not include credentials or unrelated files.
