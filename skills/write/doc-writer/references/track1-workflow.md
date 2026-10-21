# Track 1 Google Drive Workflow

Reference companion to `doc-writer/SKILL.md`. Procedure for maintaining the
Track 1 (Google Drive) ↔ Track 2 (GitHub) documentation link.

## Search for Existing Design Doc

Use the google-docs skill to search Google Drive for an existing design doc
related to the feature being documented.

## If a Design Doc Exists

Append an "Implementation Notes" section to the Google Doc with:

- PR links and branch references
- Key implementation decisions and rationale
- Any deviations from the original design
- Link to the Track 2 GitHub docs

## If No Design Doc Exists

With user confirmation, create a new Google Doc using this template:

- **Title**: `[Component] Feature Name - Implementation Notes`
- **Sections**: Overview, Architecture Decisions, Trade-offs, Track 2 Links,
  Status
- Share to the team's Drive folder

## Link Track 1 and Track 2

Add the Google Doc URL to the Track 2 README.md under a "Design Documents"
section. Add the GitHub `/docs` link to the Google Doc.

All Google Drive write operations require user confirmation.
