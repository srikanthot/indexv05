You are auditing a RAG chatbot codebase. The bot answers questions from technical safety manuals (PSEG / electric utility, ~50 manuals, ~2GB) and runs on Azure (Azure OpenAI / Azure AI Search / Azure AI Foundry). Current answer quality is around 60% and we need to find out what is missing in the code before adding evaluation.

DO NOT MODIFY ANY CODE. This is a READ-ONLY audit. Scan the entire repository and produce a structured report.

For every capability below, mark it as:
- IMPLEMENTED (fully present and wired into the main flow)
- PARTIAL (code exists but is incomplete, disabled, or disconnected)
- MISSING (no code exists)
- UNKNOWN (cannot determine from code alone)

For IMPLEMENTED and PARTIAL, cite file path and line numbers as evidence. For MISSING, briefly say where it should live if added.

CHECK ALL 82 ITEMS BELOW:

INGESTION & CHUNKING
1. Chunking strategy (fixed, recursive, semantic, by-heading)
2. Chunk size and overlap values
3. Table extraction and separate handling
4. Figure/image caption preservation
5. Footnote preservation
6. Section/heading/page metadata attached to chunks
7. Multi-page span handling
8. Document hierarchy stored (Chapter > Section > Subsection)
9. Manual version tracking

EMBEDDING & INDEXING
10. Embedding model name and version
11. Embedding dimension
12. Vector index type (Azure AI Search, FAISS, etc.)
13. Metadata filtering support
14. Re-index pipeline for new manuals
15. Embedding cache

QUERY UNDERSTANDING
16. Spell correction (handles "trnasformer", "voltge")
17. Acronym expansion (SWGR, DLRO, etc.)
18. Synonym handling
19. Query classification / intent detection
20. Multi-question splitting (one message with 3 questions)
21. Query rewriting for vague queries
22. HyDE (hypothetical document embeddings)
23. Multi-query generation (3-5 variations)
24. Sub-query decomposition for multi-hop questions

RETRIEVAL
25. Hybrid search (vector + keyword/BM25) or vector only
26. Top-K value and tuning
27. Reranking (cross-encoder, Cohere Rerank, LLM reranker)
28. MMR (Maximal Marginal Relevance) for diversity
29. Metadata pre-filtering
30. Score thresholding (drop low-confidence chunks)
31. Section-aware retrieval bias
32. Iterative / multi-hop retrieval
33. Parent-document retrieval (small chunk match, large parent return)

CONVERSATION / MULTI-TURN
34. Conversation history storage and turn limit
35. History summarization for long threads
36. Follow-up detection
37. Pronoun / coreference resolution
38. Implicit context carry between turns
39. Topic switch detection
40. Topic return handling
41. Standalone question rewriting before retrieval
42. Disambiguation prompts (bot asks user when ambiguous)
43. Context window overflow strategy

GENERATION
44. Paste the actual system prompt verbatim
45. Grounding instructions (use ONLY retrieved context)
46. Citation requirement and enforced format
47. "I don't know" instruction for insufficient context
48. Refusal pattern for out-of-scope questions
49. Numeric/unit preservation instructions
50. Step-order preservation instructions
51. Warning/caution verbatim preservation
52. Format instructions (list vs prose)
53. Temperature value
54. Generation model name and version
55. Max tokens setting

SAFETY & GUARDRAILS
56. Out-of-scope detection logic
57. Post-generation hallucination check
58. Wrong-equipment guard
59. Numeric/unit validation against retrieved chunks
60. PII redaction
61. Prompt injection defense
62. Confidence scoring exposed
63. Safety disclaimer / footer

OUTPUT POST-PROCESSING
64. Citation rendering format
65. Source link generation (page/section)
66. Answer validation (required warnings, regex checks)
67. Streaming support

OBSERVABILITY & FEEDBACK
68. Request logging (query, chunks, answer)
69. Trace IDs for end-to-end tracing
70. User feedback capture (thumbs, free text)
71. Latency metrics (retrieval, generation)
72. Token usage tracking
73. Error / refusal logging

EVALUATION HOOKS
74. Programmatic eval mode (run against a Q&A dataset)
75. Deterministic mode (fixed temperature/seed)
76. Retrieval-only endpoint for testing retrieval separately
77. Trace export (query + chunks + answer dump)

INFRASTRUCTURE
78. Error handling (Azure timeout, rate limit, content filter)
79. Retry logic with backoff
80. Per-user rate limiting
81. Response caching
82. Async / concurrency support

FINAL OUTPUT — produce these sections in this exact order:

A. CAPABILITY MATRIX
Single table with all 82 items, status, and file:line evidence.

B. TOP 10 CRITICAL GAPS
The 10 missing or partial items most likely causing the 60% quality. Rank by impact. For each:
- Why it likely causes failures
- Which user scenarios it would fix
- Complexity to implement (S / M / L)

C. TOP 5 QUICK WINS
Partial items that could be completed in under a day each for high impact.

D. ARCHITECTURE MAP
Text diagram of actual flow from user query to final answer based on real code. Mark existing vs missing steps.

E. TECH STACK INVENTORY
Frameworks, libraries, Azure services found, with versions.

F. OPEN QUESTIONS
Code paths you could not fully understand.

G. BONUS FINDINGS
Anything else relevant to RAG quality that I did not ask about.

RULES:
- Do not modify any files.
- Do not install packages or run code.
- Every claim needs file:line evidence.
- If unsure, mark UNKNOWN — do not guess.
- Do not propose fixes yet. Diagnosis only.
- Scan the whole repo including config, prompt files, middleware.

Begin the audit now.

