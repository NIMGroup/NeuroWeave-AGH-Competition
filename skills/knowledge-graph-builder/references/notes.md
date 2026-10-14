# knowledge-graph-builder：背景与注意事项

这些资料用于按需解释和排错；当前输入、参数、产物和运行能力以独立 Method/Pipeline 契约为准。

### 1. Schema Design: Three-Tier Nodes

The graph uses three types of nodes, all stored in the same DiGraph:

| Node Type | ID Format | Purpose |
|-----------|-----------|---------|
| **ConceptNode** | `NN:1234`, `CUI:xxx`, `MESH:D0001` | Domain concepts (brain regions, diseases, genes, drugs) |
| **Claim** | `CLM:abc123def456` | Structured scientific claims extracted from papers |
| **Edge** | (implicit) | Typed relationships between any two nodes |

**Why this matters**: Claims are stored as nodes (not just edges) so they can carry full metadata (evidence, p-value, sample size, conditions, population). Simplified edges are also generated for fast traversal.

### 2. Entity Resolution: 5-Level Matching

When ingesting claims, entity names must be resolved to existing concept IDs. Use a cascading strategy:

1. **Exact match** on preferred_name
2. **Case-insensitive** match
3. **Alias match** (check synonyms)
4. **Substring match** (entity contained in name or vice versa, prefer shortest name)
5. **Create new** concept if no match found

**Why not just use embeddings?** For small-to-medium graphs (<100K concepts), string matching is fast and predictable. SapBERT/FAISS alignment is recommended when UMLS is available and the graph exceeds 100K concepts.

### 3. LLM Extraction: Keep Prompts Short

LLMs (especially via proxy endpoints) return empty responses when prompts + expected output exceed the token window. Hard-won rules:

- **Truncate abstracts** to 2000 chars before sending to LLM
- **Keep extraction prompts concise** — list field names and allowed values, not verbose descriptions
- **Use `max_tokens=8192`** — 4096 is too small for papers with many claims
- **Fix `[[` double brackets** — a common LLM error when outputting JSON arrays
- **Temperature=0.1** for extraction consistency

### 4. Contextualized Triplets (MDKG-style)

Beyond simple (subject, predicate, object), extract:

- **Conditions**: list of conditions under which the claim holds (e.g., `["female only", "age > 65"]`)
- **Population**: study demographics (mean_age, gender distribution, sample size, cohort name)

This enables more nuanced graph queries and downstream hypothesis generation.

### 5. Checkpoint/Resume for Batch Jobs

Large-scale extraction (10 diseases x 27 years x 20 papers = 5400 papers) takes 10-60 hours. Always implement:

- Save checkpoint after each batch (disease+year)
- Track completed_diseases and completed_years
- Save graph periodically (every 5 years or after each disease)
- CSV export of paper metadata for audit trail

## Reference

- MDKG paper: Gao et al., "Large language model powered knowledge graph construction for mental health exploration." Nature Communications (2025). PMID: 40804250
- NeuroNames: Brain region hierarchy
- MeSH: Medical Subject Headings (NLM)
- DisGeNET: Gene-disease association database
- Cognitive Atlas: Cognitive paradigm ontology
- Nilearn: Python brain atlas library

## 来源与归属

由导入的原始说明选取背景、科学限制和解释性内容整理；执行策略与参数以当前 JSON 契约为准。
原说明作者：chengwang96。
原说明标注许可：MIT License (NeuroClaw custom skill – freely modifiable within the project)。
原说明更新时间：2026-05-06 14:46 HKT。
原始来源与许可记录见仓库 UPSTREAM.md、THIRD_PARTY_NOTICES/ 和实现来源清单。
