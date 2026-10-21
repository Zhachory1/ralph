---
name: simplified-technical-english
description: Write, convert, or check technical documentation in ASD-STE100 Simplified Technical English (STE), the controlled-English standard used in aerospace, defense, and other technical industries. Use this whenever the user wants to write procedures, maintenance steps, warnings and cautions, or descriptive technical text in plain, unambiguous, translation-ready English; asks to "simplify," "plain-language," "STE-ify," "controlled English," or "de-jargon" a document; wants text checked or scored against STE rules; or mentions Simplified Technical English, ASD-STE100, STE100, or controlled language. Also use for making instructions clearer, shorter, and more consistent even when the user does not name STE explicitly.
license: Condensed practitioner reference. Source standard (c) ASD, ASD-STE100 Issue 9, 2025. Not affiliated with or endorsed by ASD.
---

# Simplified Technical English (ASD-STE100)

## What STE is and why it exists

Simplified Technical English is a controlled version of English for technical documentation. It has two parts: a set of writing rules (grammar and style) and a controlled dictionary (approved words). The goal is text that is unambiguous, easy to read for non-native speakers, and cheap to translate. It was built for aerospace maintenance manuals but works for any procedure, instruction set, or technical description.

STE trades expressive range for clarity. It is deliberately plain and slightly repetitive. Do not "improve" the prose with variety or elegance — consistency is the point.

This skill is a condensed working reference for Issue 9 (53 rules across 9 sections). It captures the rules that change writing the most. For the complete rule list see `references/all-rules.md`; for word choice see `references/word-swaps.md`; for worked transformations see `references/examples.md`.

## Which mode are you in?

- **Write new text in STE** — apply the rules below as you draft.
- **Convert existing text to STE** — rewrite sentence by sentence, then run the checklist at the end.
- **Check / score text** — evaluate against the checklist, flag each violation with the rule it breaks and a fixed version. Do not silently rewrite unless asked.

If the user gives text that is clearly a procedure (numbered steps, commands), treat it as procedural writing (≤20 words/sentence). If it explains how something works or what something is, treat it as descriptive writing (≤25 words/sentence). Ask only if it is genuinely unclear.

## The high-impact rules

These are the rules that most change ordinary technical writing. Apply all of them by default.

### 1. One instruction per sentence
In procedures, write one action per sentence. If two actions happen at the same time, they can share a sentence.
- Not STE: *Remove the cover and disconnect the cable, then drain the reservoir.*
- STE: *Remove the cover. Disconnect the cable. Drain the reservoir.*

### 2. Short sentences
Procedures: 20 words maximum per sentence. Descriptive text: 25 words maximum. Long sentences get split, not comma-spliced.

### 3. Give commands as commands (imperative)
Instructions start with the verb. Do not soften them or use passive.
- Not STE: *The filter should be replaced.* / *You will need to replace the filter.*
- STE: *Replace the filter.*

### 4. Active voice
Use active voice everywhere. In descriptive text only, passive is allowed when the agent (who/what does the action) is genuinely unknown.
- Not STE: *The valve is closed by the actuator.*
- STE: *The actuator closes the valve.*

### 5. Simple tenses only
Allowed: infinitive, imperative, simple present, simple past, simple future, and past participle used as an adjective. Do not use perfect tenses (has closed, had closed), progressive tenses (is closing, was closing), or stacked auxiliaries.
- Not STE: *The system has been calibrated and will have completed the cycle.*
- STE: *A technician calibrated the system. The cycle is complete.*

### 6. No "-ing" as a verb
Use "-ing" words only as a technical noun (bearing, coating) or as a modifier. Never as the main verb.
- Not STE: *Start closing the valve while monitoring the pressure.*
- STE: *Close the valve slowly. Monitor the pressure.*

### 7. Keep the articles and small words — no telegraphic style
Do not drop "the/a/an," and do not use contractions to shorten. Shorten by cutting ideas, not grammar.
- Not STE: *Remove filter, install new one, don't overtighten.*
- STE: *Remove the filter. Install a new filter. Do not tighten the filter too much.*

### 8. Condition before the instruction
When the reader must know a condition first, state the condition, then the command.
- Not STE: *Press the reset button if the light is red.*
- STE: *If the light is red, press the reset button.*

### 9. Warnings and cautions: hazard command first, reason second
Start a safety instruction with the command or condition, then explain the risk. Put warnings and cautions before the step they apply to.
- STE: *Warning: Do not touch the manifold. The manifold is hot and can cause burns.*

### 10. One word, one meaning; one thing, one name
Use approved words with their approved meaning only. Never call the same item by two different names in the same document (do not alternate "cover," "lid," "cap"). Pick one term and keep it. See `references/word-swaps.md`.

### 11. Prefer approved vocabulary
Replace fancy or vague words with the plain approved word: *use* not *utilize*, *before* not *prior to*, *get* not *obtain/acquire*, *help* not *assist*, *start* not *commence*, *stop* not *terminate*, *tell* not *advise/notify*, *make* not *manufacture*, *enough* not *adequate*, *about* (approx.) not *approximately*. Full cheat-sheet in `references/word-swaps.md`. Technical nouns and technical verbs specific to the equipment are always allowed even if not in the dictionary (see rule 1.5 / 1.12 in `references/all-rules.md`).

### 12. Structure for skimming
Give information gradually. One topic per paragraph, six sentences maximum per paragraph. Use a vertical (bulleted or numbered) list when a sentence would otherwise pack in many conditions or items. Keep multi-word noun strings to three words maximum; if a technical term is longer, write it in full and do not stack extra modifiers on it.

### 13. Punctuation
Standard English punctuation is fine except the semicolon — do not use it (split into two sentences or use a list). Use "that" after verbs like *make sure, show, recommend* to remove ambiguity (*Make sure that the valve is open.*).

## STE compliance checklist

Run this over any text before calling it STE. For a check/score task, report each miss with the offending sentence and a corrected version.

1. Every instruction is one action, in the imperative, active voice.
2. No sentence exceeds 20 words (procedure) / 25 words (descriptive).
3. Only simple tenses; no perfect/progressive/complex verb strings; no "-ing" as a main verb.
4. Articles and full words present; no contractions; no dropped words.
5. Conditions come before commands; warnings/cautions precede their step and lead with the hazard.
6. Each item has exactly one name throughout; approved words used with approved meanings.
7. Paragraphs: one topic, six sentences maximum; long/complex content is in a vertical list.
8. Multi-word nouns are three words or fewer.
9. No semicolons; "that" used to prevent ambiguity.

## Output format

- **Converting or writing:** return the STE text only, in the same structure (steps stay steps). If you made non-obvious choices (split a sentence, renamed a part for consistency, replaced a word), add a short "Notes" list under the text.
- **Checking/scoring:** return a table or list of findings — sentence, rule broken, fix — then a one-line verdict. Optionally give a rough score (share of sentences that pass).

## Reference files

- `references/all-rules.md` — all 53 rules, paraphrased, grouped by the 9 sections. Read when you need a rule not covered above, or when the user cites a rule number.
- `references/word-swaps.md` — how the controlled dictionary works, the 22 technical-noun and 4 technical-verb categories, and a curated non-approved to approved cheat-sheet. Read for any word-choice decision.
- `references/examples.md` — longer before/after conversions (procedure, description, warning) and common writing pitfalls (pronoun ambiguity, "with," dropped "that"). Read when converting whole passages.

## Scope and honesty

This is a condensed reference, not the official standard. The real dictionary has ~2,000 entries and the rules have detailed explanatory text and examples. For formal/certified STE work, verify against the official ASD-STE100 Issue 9 specification at asd-ste100.org. When you are unsure whether a specific word is approved, say so rather than guessing — flag it as "verify in the official dictionary."
