# Skills

Skills the agents load, in lanes. A profile mounts whole lanes, read-only, so the
unit of selection is a directory and a skill two profiles need has to sit in a
lane they both mount.

This repository is public and MIT. What goes in it is decided by the rule below
rather than by taste, so that adding a skill later is a question you can answer
yourself.

## The rule

Apply in order and stop at the first match. The order runs from the tests that
take a skill out of the import entirely, through the test that decides whose
licence it ships under, to the tests that only choose which repository holds it.
A later test is never asked a question an earlier one has already settled.

1. **Does a Hermes builtin already do it, or share its name?**
   → **Drop.** A skill whose name matches a builtin can never resolve: builtins
   sit in the profile skills tier, which outranks both mount points. Filing it
   would be deciding where to put something that will never load.

2. **Is it someone else's?** A LICENSE naming another copyright holder, or a git
   remote outside this account.
   → **Reference, never copy.** It goes in `third-party.yaml` with an upstream
   and a pinned commit. Never vendored, never relicensed. When the pointer to it
   is dead and nobody has missed it, the honest form of "reference" is **skip**.

3. **Does it need something only one machine has?** A local daemon, a private
   document store, a personal credential path.
   → **Config repo.** It is useless in a container, so it ships in neither
   repository and is mounted only where its dependency exists.

4. **Does any line of it name something that exists only inside the company?**
   An internal repository, host, endpoint, tenant URL, standard, process, metric,
   incident tier, brand value, or data model. The test is the name, not the
   topic: "write tests in a pyramid" is generic; "the pyramid as defined by our
   standards doc" is not.
   → **Private overlay.**

5. **Otherwise** → **public.** It belongs here, in the lane that fits.

### Two notes that make this usable without asking

**A mention is not a coupling.** A skill that names one internal thing in one
sentence is a public skill with a sentence to fix. A skill whose *procedure* only
makes sense inside the company is private, however few times it says so.

**When in doubt, private.** Moving a skill from private to public later is a pull
request. Moving it the other way is a history rewrite and a disclosure
conversation.

## The lanes

Lanes are cut by **audience**, not by topic, because a profile mounts a directory
and not a list.

| Lane | For |
| --- | --- |
| `plan/` | Deciding what to do: planning, prompts, deliberation, session continuity |
| `voice/` | How every profile writes: mounted by all of them, nothing else lives here |
| `write/` | Producing prose: documents, summaries, controlled language |
| `code/` | Changing code: tests, shipping, branches, pull requests |
| `debug/` | Finding out why something is broken, per stack |
| `ui/` | Building and judging interfaces |
| `review/` | Judging someone else's work |
| `ops/` | Running things: alerts, monitors, notification surfaces |
| `meta/` | Making more agents and skills |

## Adding a skill

1. Run the rule. If it lands anywhere but step 5, it does not go here.
2. Put it in the lane whose *audience* matches, not whose topic does.
3. If it is someone else's, add it to `third-party.yaml` instead and pin a commit.
4. `evals/check-skills-public` runs in CI as a backstop for rule 4, not as a
   substitute for reading it. On a public runner it can only scan the markers
   that are already public, because the full list lives in the private overlay
   and putting it here would be the leak the rule exists to prevent. **A green
   build means "no publicly-known internal name", not "nothing internal".** The
   complete check is `--markers <overlay list>`, run by a reviewer who has the
   overlay; rule 4 still needs a person who has read the skill.
