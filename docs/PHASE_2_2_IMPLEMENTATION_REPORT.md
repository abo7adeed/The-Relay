# Phase 2.2 Implementation Report — F-04 / F-05 / F-06 Corrective Work

**Repository:** `E:\Ai-projects\vaaniresearch-assignment`  
**Date:** 2026-11-09  
**Scope:** Authorized Phase 2.2 corrective implementation for F-04, F-05, and F-06 only.  
**Final status:** `IMPLEMENTATION BLOCKED — WITH EVIDENCE`

---

## 1. Why the status is BLOCKED (read first)

The parser and the 22 new regression tests are implemented, and all validation passes against a **staged** end-to-end corpus. The delivered corpus under `data/processed/` was **not regenerated**, because the authorization explicitly says:

> Do not regenerate existing processed chunks without explicit authorization.

Consequently, the files that downstream retrieval consumes still contain the old F-06 artifacts:

| File | SHA-256 | Stale malformed forms still present |
|---|---|---|
| `data/processed/chunks_setting_1.json` | `073BEBC9B342187C9842EB966A87952337128151F85C21458A167DE9EEE461C0` | 12 forms |
| `data/processed/chunks_setting_2.json` | `E1B7AC131E42E3019AE3A45F19D9F85EABE74B458794A01BC3CBDD0F3505766D` | 12 forms |
| `data/processed/chunks_setting_3.json` | `073BEBC9B342187C9842EB966A87952337128151F85C21458A167DE9EEE461C0` | 12 forms |

Those 12 forms are: `text-tospeech`, `melspectrogram`, `semanticto-acoustic`, `singleturn`, `textonly`, `0shot`, `lowercapacity`, `speech-tospeech`, `predecay`, `number-tokenizationblog`, `bitter-lessontokenization-and-blt`, and the line-break `Mamba2` case.

**Authorization is requested to replace the three processed chunk files with the staged output whose hashes are recorded in §6.** Until then, the code is corrected but the delivered corpus is not.

---

## 2. Working-tree baseline and change inventory

### 2.1 No Git repository

`git status` and `git rev-parse --is-inside-work-tree` both fail with:

```
fatal: not a git repository (or any of the parent directories): .git
```

There is therefore **no authoritative Git diff and no history**. A complete SHA-256 + mtime inventory was captured before editing at:

`C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_pre_edit_hashes.txt`

The post-edit inventory is at:

`C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_post_edit_hashes.txt`

### 2.2 Modified-file inventory

Exactly **two pre-existing implementation files** changed. One documentation deliverable (this report) was added. No implementation files were added or removed.

| Path | Pre-edit SHA-256 | Post-edit SHA-256 | Status |
|---|---|---|---|
| `src/relay/corpus/parser.py` | `2B590518324884099A6287EC9457FB517B9D93C00E7D1D898A2FFA5E8B5CA945` | `FD71EAA629986A4930247702FDD3D547E1605D4FE3A5A1ADC424DAD5B7CF2E53` | Modified |
| `tests/test_corpus.py` | `62B8F8039F8E313C86DE72B3246AB70F07FBBD73AB95F2DD9A1E3FAD5147AF0E` | `B98CD77B9F71B213A40A6A9E2562E85234F89BD10B0A7A5C0BC0B098D1F54E08` | Modified |
| `docs/PHASE_2_2_IMPLEMENTATION_REPORT.md` | — | added deliverable | Added |

The other **36** inventoried files are byte-identical, including:

- All three source PDFs under `data/raw_papers/`
- `data/eval_questions.json`
- `data/processed/chunks_setting_1.json`
- `data/processed/chunks_setting_2.json`
- `data/processed/chunks_setting_3.json`
- `src/relay/corpus/chunker.py`
- `src/relay/corpus/fetcher.py`
- All model, retrieval, pipeline, evaluation, and benchmark components

---

## 3. What changed

### 3.1 `src/relay/corpus/parser.py` — F-04 heading detection

The old detection accepted regular body text at `sz >= 10.5` for numbered headings and `sz >= 8.9` for standard section names. It also lacked a conservative unnumbered-bold rule.

The new detector combines multiple independent signals:

1. **Structural table immunity** via `page.find_tables()` bounding boxes. A line whose bounding box lies inside a detected table is never a heading, but its text is retained as body content.
2. **Relative font size** against a document-wide body-size mode, rather than absolute point thresholds.
3. **PyMuPDF style flags** (`flags & 16`) plus bold/small-caps font hints as a secondary signal.
4. **Line shape**: length, word count, terminal punctuation, digits, URL markers, and capitalized first token.
5. **Reading position**: caption/watermark rejection and block-start gating for generic headings.
6. **Explicit numbered, lettered, two-line, appendix, and standard-name rules**, each requiring styling or a size jump.

No rule relies exclusively on a hardcoded font family. Font names are one signal among several.

### 3.2 `src/relay/corpus/parser.py` — F-06 hyphenation

`repair_hyphenation` no longer blindly joins every `word-\nword` pair. Before repair, the parser builds a document-wide vocabulary of intact hyphenated forms, intact joined forms, and word parts. The final token of a line ending in `-` is excluded so a broken word cannot vote for itself.

For each real line-break pair, the decision order is:

1. URL / DOI / identifier context → preserve.
2. Curated known compound → preserve.
3. Curated known broken word → join.
4. Document-frequency evidence → follow the dominant intact form.
5. Connector particle (`to`, `of`, `and`, `per`, …) → preserve.
6. Numeric head (`0-shot`) → preserve.
7. Two capitalized parts (`H-Net`, `Berkeley-Parser`) → preserve.
8. Productive compound suffix (`-based`, `-wise`, `-aligned`, …) → preserve.
9. Conservative compound prefix (`single-`, `lower-`, `pre-`, `text-`, …) → preserve.
10. Otherwise → join.

Only an actual newline triggers a decision. Same-line suspended hyphens such as `content- and` are never matched by the repair regex.

### 3.3 `tests/test_corpus.py`

- Existing 14 tests retain every original assertion.
- **22 new regression tests** added (7 F-04, 8 F-05, 7 F-06), using session-scoped fixtures so each PDF is parsed once per session.
- The new tests validate the production pipeline (`parse_pdf` → `chunk_document` → `build_all_processed_chunks`) into a pytest temporary directory, never overwriting `data/processed/`.

---

## 4. Test results

### 4.1 Corpus suite (existing + 22 new)

```
tests/test_corpus.py ............ 36 passed in 90.32s
```

### 4.2 Full repository suite

```
tests/ .................................................... 52 passed in 76.96s
```

Raw logs:

- `C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_pytest_corpus3.txt`
- `C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_pytest_all.txt`

The 22 new tests cover:

| # | Test | Area |
|---|---|---|
| 1 | `test_f04_attention_table_column_headers_not_promoted` | F-04 |
| 2 | `test_f04_moshi_table_headers_and_body_questions_not_promoted` | F-04 |
| 3 | `test_f04_hnet_table_headers_not_promoted` | F-04 |
| 4 | `test_f04_all_toc_entries_aligned` | F-04 |
| 5 | `test_f04_small_font_and_small_caps_headings_detected` | F-04 |
| 6 | `test_f04_captions_and_watermarks_never_headings` | F-04 |
| 7 | `test_f04_reading_order_and_page_provenance` | F-04 |
| 8 | `test_f05_every_nonheading_source_line_preserved` | F-05 |
| 9 | `test_f05_no_duplicated_source_blocks` | F-05 |
| 10 | `test_f05_source_to_block_sequence_preserved` | F-05 |
| 11 | `test_f05_block_to_chunk_token_completeness` | F-05 |
| 12 | `test_f05_chunk_page_and_section_provenance` | F-05 |
| 13 | `test_f05_mixed_heading_body_blocks_preserved` | F-05 |
| 14 | `test_f05_table_and_figure_captions_preserved` | F-05 |
| 15 | `test_f05_reference_entries_preserved` | F-05 |
| 16 | `test_f06_fifteen_malformed_cases_fixed_in_pipeline` | F-06 |
| 17 | `test_f06_nine_suspended_hyphens_preserved` | F-06 |
| 18 | `test_f06_broken_words_rejoined` | F-06 |
| 19 | `test_f06_compounds_and_entities_preserved` | F-06 |
| 20 | `test_f06_urls_and_identifiers_not_reflowed` | F-06 |
| 21 | `test_f06_no_hyphens_inserted_or_left_unresolved` | F-06 |
| 22 | `test_f06_staged_setting_identity_and_caps` | F-06 / M1 |

---

## 5. Completeness and provenance evidence (F-05)

Source: `C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_staging_evidence.txt`

### 5.1 Every non-heading source line preserved

| Paper | Non-heading source lines | Missing | Coverage | Pages with missing |
|---|---:|---:|---:|---|
| `1706.03762v7` | 911 | 0 | 1.00000 | none |
| `2410.00037v1` | 4,272 | 0 | 1.00000 | none |
| `2507.07955v2` | 3,312 | 0 | 1.00000 | none |

The check runs at line level against every PyMuPDF extracted source line, excluding only running headers, page-number footers, the page-1 arXiv watermark, and lines that are part of a detected heading.

### 5.2 Block → chunk token completeness

| Paper | Setting | Chunks | Pages with missing block tokens |
|---|---|---:|---|
| Attention | 350/50 | 42 | none |
| Attention | 175/25 | 58 | none |
| Moshi | 350/50 | 149 | none |
| Moshi | 175/25 | 245 | none |
| H-Net | 350/50 | 106 | none |
| H-Net | 175/25 | 178 | none |

### 5.3 Duplicate and provenance checks

| Paper | Blocks | Duplicate block texts | Invalid chunk provenance |
|---|---:|---:|---:|
| Attention | 40 | 0 | 0 |
| Moshi | 104 | 0 | 0 |
| H-Net | 71 | 0 | 0 |

### 5.4 TOC alignment

All embedded PDF TOC entries map to a detected heading:

- Attention: 22 / 22
- Moshi: 44 / 44
- H-Net: 34 / 34

### 5.5 F-04 false positives removed

Previously reported false positives are now absent from section titles:

- Attention p8: `Model`, `BLEU`, `Training Cost (FLOPs)`, `EN-DE`, `EN-FR`, table rows
- Moshi p29/p30: `Model`; p35: `Background` and numbered body questions
- H-Net p14/p15/p17/p38/p39: `Model` (including hex-content tables not found by `find_tables()`)

Genuine headings that must remain are still detected, including H-Net `Abstract` at 8.97 pt and Moshi `3.3.1 Architecture` at 10.91 pt small-caps.

Two new unnumbered H-Net lead-ins are intentionally detected (`Dynamic Chunking: End-to-end Sequence Modeling Without Tokenization`, `Hierarchical Chunking: From Data to Abstractions`). Inspection of pages 2 and 4 confirms they are standalone bold author headings, not figure titles or table headers.

---

## 6. Staged end-to-end output (authorization target)

Built with `build_all_processed_chunks` into:

`C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_staged\`

| File | SHA-256 | Bytes | Chunks | Max words |
|---|---|---:|---:|---:|
| `chunks_setting_1.json` | `AC7701354631922FFBEB383F22686E18ED31E0AB2106624CE211FBD2438518AC` | 496,620 | 297 | 350 |
| `chunks_setting_2.json` | `A76F2984F865727EABA4DA9BA3EEEB83E6EEC0ED0E106CC944E91F5C97A4E004` | 572,544 | 481 | 175 |
| `chunks_setting_3.json` | `AC7701354631922FFBEB383F22686E18ED31E0AB2106624CE211FBD2438518AC` | 496,620 | 297 | 350 |

Setting 1 and Setting 3 are **byte-identical** in staging, satisfying the M1 identity requirement for the corrected output.

---

## 7. Hyphenation audit (F-06)

### 7.1 All 15 malformed cases

Every case passes in the parsed blocks and in staged Settings 1 and 2.

| # | Malformed | Corrected | Blocks | Setting 1 | Setting 2 |
|---|---|---|---|---|---|
| 1 | `text-tospeech` | `text-to-speech` | pass | pass | pass |
| 2 | `melspectrogram` | `mel-spectrogram` | pass | pass | pass |
| 3 | `semanticto-acoustic` | `semantic-to-acoustic` | pass | pass | pass |
| 4 | `singleturn` | `single-turn` | pass | pass | pass |
| 5 | `textonly` | `text-only` | pass | pass | pass |
| 6 | `0shot` | `0-shot` | pass | pass | pass |
| 7 | `lowercapacity` | `lower-capacity` | pass | pass | pass |
| 8 | `speech-tospeech` | `speech-to-speech` | pass | pass | pass |
| 9 | `Le-Cun` | `LeCun` | pass | pass | pass |
| 10 | `Speechgptgen` | `Speechgpt-gen` | pass | pass | pass |
| 11 | `predecay` | `pre-decay` | pass | pass | pass |
| 12 | `Computeoptimal` | `Compute-optimal` | pass | pass | pass |
| 13 | `number-tokenizationblog` | `number-tokenization-blog` | pass | pass | pass |
| 14 | `bitter-lessontokenization-and-blt` | `bitter-lesson-tokenization-and-blt` | pass | pass | pass |
| 15 | line-break `Mamba2` | `Mamba-2` | pass | pass | pass |

Case 15 is contextual: the reference title “Improving Mamba2 with Delta Rule” legitimately contains `Mamba2` in the source, so the test asserts that `computations for Mamba-2` is present and `computations for Mamba2` is absent, rather than banning the token globally.

### 7.2 Nine suspended hyphens preserved

| Construction | Occurrences | Pages |
|---|---:|---|
| `content- and` | 3 | 1, 23, 33 |
| `context- dependent` | 1 | 1 |
| `compute- and` | 1 | 1 |
| `data- matched` | 1 | 1 |
| `delimiter- or` | 1 | 2 |
| `main- networks` | 1 | 9 |
| `context- aware` | 1 | 33 |
| **Total** | **9** | — |

All nine are preserved exactly in parsed blocks and staged chunks.

### 7.3 Preserved compounds and entities (sample)

Attention: `multi-head`, `encoder-decoder`, `self-attention`, `feed-forward`, `end-to-end`, `task-specific`, `sequence-aligned`, `position-wise`, `Berkeley-Parser`, `English-to-German`.

Moshi: `real-time`, `cross-entropy`, `well-known`, `fine-tuning`, `pre-training`, `post-training`, `text-to-speech`, `speech-to-speech`, `semantic-to-acoustic`, `single-turn`, `text-only`, `0-shot`, `lower-capacity`, `multi-stream`, `full-duplex`, `non-causal`, `RQ-Transformer`, `state-of-the-art`, `mel-spectrogram`.

H-Net: `H-Net`, `FineWeb-Edu-Chinese-V2`, `Bits-per-byte`, `context-dependent`, `Gradient-Based`, `high-resource`, `BPE-based`, `Mamba-2`, `Compute-optimal`, `pre-decay`, `cross-modality`, plus URL fragments `number-tokenization-blog`, `bitter-lesson-tokenization-and-blt`, `introducing-openai-o1-preview`.

### 7.4 Decision audit

Every unique line-break pair was enumerated and reviewed:

- Attention: 14 pairs, 6 preserved, 8 joined
- Moshi: 173 pairs, 23 preserved, 150 joined
- H-Net: 36 pairs, 16 preserved, 20 joined

Full listing: `C:\Users\ahmed\AppData\Local\Temp\opencode\phase22_f06_decisions.txt`

---

## 8. Integrity report

| Check | Result |
|---|---|
| Pre-edit inventory file count | 38 |
| Post-edit inventory file count | 39 |
| Files unchanged | 36 |
| Pre-existing files modified | 2 (`parser.py`, `test_corpus.py`) |
| Documentation files added | 1 (this report) |
| Implementation files added | 0 |
| Files removed | 0 |
| Source PDFs unchanged | yes |
| `data/eval_questions.json` unchanged | yes (hash `D28D94CD…`) |
| `data/processed/*.json` unchanged | yes (hashes above) |
| Model providers / evaluation config unchanged | yes |
| Setting 1 vs Setting 3 on disk | byte-identical |
| Setting 1 vs Setting 3 staged | byte-identical |

---

## 9. Known limitations

1. **Delivered corpus is stale.** `data/processed/*.json` was not regenerated because authorization is required. The corrected output exists only in staging. This is the reason for the BLOCKED status.
2. **No Git metadata.** No authoritative diff, commit, or history can be produced. Change evidence is the pre/post SHA-256 inventory.
3. **Cross-figure hyphenation is deliberately not forced.** Two Moshi body lines end in `Normaliza-` and `re-` because a figure/table caption interrupts the source column. The repair engine refuses to guess across that structural boundary. Joining them would require column-aware continuation logic; leaving them is the conservative behavior.
4. **Curated vocabulary is corpus-scoped.** The `KNOWN_COMPOUNDS`, `KNOWN_JOINED`, prefix, suffix, and connector sets were derived from these three papers plus standard academic prose. They are not a general English dictionary; no dictionary/NLP package is installed.
5. **Vector figure labels are not covered by image bounding boxes.** `get_image_bboxes` only sees raster image blocks. The generic-heading rule therefore relies on relative size, style, line shape, and block position to avoid promoting vector figure labels. This is robust for the audited corpus but is not a universal guarantee for arbitrary PDFs.
6. **Runtime cost.** Parsing the three PDFs dominates the corpus suite; session-scoped fixtures keep the 36-test corpus run at ~90 s and the full 52-test suite at ~77 s.
7. **This report is a self-audit, not independent verification.** It records reproducible commands and outputs; it does not claim external audit status.

---

## 10. Reproduction commands

```powershell
# Corpus regression suite (36 tests: 14 existing + 22 new)
$env:PYTHONDONTWRITEBYTECODE=1
$env:PYTHONIOENCODING="utf-8"
& "D:\ahmed\conda\python.exe" -m pytest tests/test_corpus.py -v -p no:cacheprovider

# Full repository suite (52 tests)
& "D:\ahmed\conda\python.exe" -m pytest tests/ -q -p no:cacheprovider

# Staged end-to-end corpus + F-05/F-06 evidence
& "D:\ahmed\conda\python.exe" `
  "C:\Users\ahmed\AppData\Local\Temp\opencode\build_staging_evidence.py"

# Line-break hyphen decision listing
& "D:\ahmed\conda\python.exe" `
  "C:\Users\ahmed\AppData\Local\Temp\opencode\audit_f06_decisions.py"
```

---

## 11. Final status

**`IMPLEMENTATION BLOCKED — WITH EVIDENCE`**

The authorized code changes are implemented, and the corrected pipeline passes every required validation in staging, including all 22 new regression tests, 100% non-heading source-line preservation, zero duplicate blocks, zero provenance violations, all 15 malformed hyphenation cases, all nine suspended hyphens, and staged Setting 1 / Setting 3 byte identity.

The blocker is narrow and explicit: replacing `data/processed/chunks_setting_1.json`, `chunks_setting_2.json`, and `chunks_setting_3.json` requires authorization that was not granted. Once granted, the three staged files in §6 can be promoted and the status can move to independent audit.
