# Research Context Engine

Implemented in ExpandRG 0.15.0. The [approved design](research-context-engine-proposal.md) records the assessment and boundaries. CREAO remains the only reasoning provider. This component performs deterministic storage, retrieval and validation; it makes no model calls.

The first connected workflow is **saved code and inputs → simulation run → anomaly → private investigation → selected evidence → approved finding**. Existing experiment and graph identities are reused. Worksheet references expose task status and the findings being taught, without human answers or comments. Onboarding lessons (a paper and its code, taught with equations and pictures) are built separately in `backend/onboarding/` and kept in the researcher's private `lessons` table; they don't yet register context artifacts, so paper, equation and lesson adapters remain future work.

## Stores and identity

The existing shared and personal SQLite files each gain three additive tables: `context_artifacts`, `context_links`, and `context_events`. Schema version 2 and existing data are preserved; no reset is needed. Private investigations, local artifact descriptors, graph specs and relationship proposals remain in the caller's personal database. Shared findings continue to use the existing publication and verification workflows.

Typed references reuse existing IDs: `run:`, `anomaly:`, `investigation:`, `draft:`, `finding:`, `graph:`, `sim:`, `exp:`, `base:`, `worksheet:`, and `artifact:`. A simulation run ID is distinct from a CREAO `provider_run_id`. Legacy `private_episodes.run_id` points to another table and is never interpreted as a shared simulation run.

New saved simulations and experiments capture exact code/input fingerprints, declared effective parameters, optional local Git HEAD, and explicit parent/origin references. Execution uses a working copy; the prepared snapshot is retained. Git dirty state remains unknown: no Git commands, hooks or filters run to capture metadata. A fingerprint identifies captured files; HEAD alone is never presented as their exact version. Legacy records explicitly report absent capture rather than fingerprinting today's files as yesterday's source.

The connector retains metadata projections in each record's durable outbox. Authenticated retrieval applies producer/event receipts and projections together in one private transaction, then acknowledges committed events. Lost acknowledgements replay safely. Older source revisions cannot replace newer metadata. Deliveries are bounded; a large local history reports truncation. A delivery sends each selected record's newest pending complete projection; older pending events remain available for subsequent acknowledgement. This is a small local synchronization mechanism, not a permanent event archive.

Monitoring also uses stable run-start and frozen report keys. Database receipts commit with samples and anomalies, so lost acknowledgements cannot create duplicate records. Frozen prefixes survive buffer growth and token renewal. Connector delivery state is saved beside managed simulations and watched-log state; queued old runs retain their identity during an outage. Excess unsent observations are bounded and reported in connector logs. This does not resume solver processes after a connector restart.

## Interfaces

Authenticated backend endpoints:

| Operation | Contract |
|---|---|
| `GET /context/runs/{run_id}` | Connected context through depth 5; `limit` and optional `cursor` paginate records |
| `GET /context/records/{typed_ref}` | Context from any supported record; optional `project_id` enables first retrieval of local records; `depth`, `limit`, `cursor` bound traversal/output |
| `POST /context/relationships/proposals` | Explicit project, source/target typed refs, predicate and optional evidence refs; always a private unverified proposal |
| `POST /agent-tools` | Existing toolkit adapter; supports `get_research_context`, `find_related_experiments`, and `propose_research_relationship` |

`ContextEngine` provides the same operations to backend services. `get_run_context` returns labelled records, both-direction relationships, section availability, provenance, completeness, and a continuation cursor. Responses fit a 12,000-character canonical JSON budget. References in summaries and both relationship endpoints are independently authorized. Relationship edges are included when both endpoints appear on a page; follow a typed reference to inspect its own neighborhood. Metrics come from stored numeric aggregates, without another CREAO request or a full log attachment.

Related-experiment search requires explicit parameter values and a finite nonnegative absolute tolerance. It returns matching captured values and their completeness labels. A match is an observation about recorded parameters, not evidence that the experiments are scientifically equivalent.

The connector exposes new bearer-authenticated, owner-scoped routes: `GET /context/records`, `POST /context/events/ack`, and `POST /context/artifacts/read`. Artifact reads recheck the original folder grant, retained file index and digest; a descriptor does not grant permanent file access. Existing locally trusted connector routes retain their existing trust model.

## Disclosure and scientific state

Every read rechecks current active membership and source permissions. A leader may see a project run without gaining access to its owner's personal database. Another researcher may read an approved finding without receiving its private source IDs, graph, diagnosis or evidence. Human finding comments, worksheet answers, credentials and absolute filesystem paths are excluded from context.

Server-selected `private_analysis` permits the caller's own unpublished context. Personal chat uses `project_publication`, which excludes unpublished investigation prose and evidence content because eligible chat conclusions may automatically become group hypotheses. Current-project captured run/experiment observations remain eligible. Cross-project findings must satisfy the existing readership-intersection rule. Explicit authenticated toolkit API requests and graph analysis use private analysis. The agent cannot override the server's purpose through tool arguments.

Diagnosis saves explicit simulation/anomaly/evidence references in its canonical private episode and retains only selected, redacted evidence excerpts, with digests and truncation labels. Read-through canonical projections recover relationships even if a later overlay write was interrupted. Approval automatically attaches the explicit source run when it is still the author's own run in that project. Approval and automatic chat sharing retain their existing behavior; neither operation automatically verifies a scientific claim. Relationship proposals likewise cannot alter verification.

## Validation

`test_context_engine.py`, `test_context_capture.py`, `test_run_idempotency.py`, and `test_context_integration.py` cover connected retrieval, pagination/budgets, source completeness, immutable input snapshots, declared parameters, event replays/conflicts/stale revisions, rollback, process-restart delivery, artifact grants, active-membership revocation, private/shared boundaries, and the complete diagnosis-to-publication workflow with mocked CREAO. The existing regression suite and package checks remain required.
