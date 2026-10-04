from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

CONTEXTUALIZE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You rewrite follow-up questions so they stand alone.\n\n"
            "Given a chat history and the latest question, rewrite the question "
            "so it can be understood with no history at all. Resolve every "
            "pronoun and every implicit reference against the history.\n\n"
            "Rules:\n"
            "- If the question is already self-contained, return it unchanged.\n"
            "- Never answer the question. Only rewrite it.\n"
            "- Keep the user's vocabulary. Do not add detail they did not imply.\n"
            "- Preserve identifiers exactly: claim numbers, codes, dates.",
        ),
        ("human", "CHAT HISTORY:\n{history}\n\nLATEST QUESTION: {question}"),
    ]
)

PLAN = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are the router for a question-answering system that has "
            "exactly two data sources.\n\n"
            "SOURCE A -- a SQL warehouse. Structured, numeric, per-record facts. "
            "Use it for counts, sums, averages, trends, rankings, comparisons "
            "over time, and lookups of specific records by identifier.\n\n"
            "SOURCE B -- a document corpus of policies and procedures. Prose "
            "rules. Use it for definitions, eligibility rules, processes, "
            "deadlines, requirements, exclusions, and anything asking "
            '"how does X work" or "what is the rule for X".\n\n'
            "Choose exactly one route:\n"
            '- "sql"    The answer is entirely a number, list or record from '
            "the warehouse.\n"
            '- "docs"   The answer is entirely a rule or process from the '
            "documents.\n"
            '- "both"   The question genuinely needs a number AND a rule. '
            "Typical shapes: a specific record plus what to do about it; a "
            "measured trend plus the policy that explains it.\n"
            '- "direct" Pure conversational filler with no information need: '
            'greetings, thanks, "who are you", "what can you do".\n'
            '- "refuse" Outside this system\'s domain entirely, or asking for '
            "something the data cannot support.\n\n"
            "Guidance that matters:\n"
            '- Prefer "both" when the question contains a specific identifier '
            'AND asks what to do, or asks "why" about a measurable pattern. '
            'Under-using "both" produces half-answers.\n'
            '- Do NOT choose "both" merely because a question mentions a domain '
            "noun appearing in both sources. Ask whether the answer truly needs "
            "both halves.\n"
            '- "refuse" is for out-of-domain questions, not for hard ones.',
        ),
        (
            "human",
            "DOMAIN: {domain_description}\n\n"
            "SOURCE A -- warehouse tables available:\n{table_summaries}\n\n"
            "SOURCE B -- documents available:\n{doc_sources}\n\n"
            "QUESTION: {question}{feedback}",
        ),
    ]
)

SQL_WRITE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You write a single DuckDB SELECT statement that answers a request "
            "about a data warehouse.\n\n"
            "HARD RULES -- a statement violating any of these is rejected "
            "before it runs:\n"
            "1. Exactly ONE statement. No semicolon-separated statements.\n"
            "2. SELECT (or WITH ... SELECT) only. Never INSERT, UPDATE, DELETE, "
            "CREATE, DROP, ALTER, ATTACH, COPY, INSTALL, LOAD, PRAGMA, SET or "
            "EXPORT.\n"
            "3. Reference ONLY the tables and columns given to you. Never invent "
            "a column. Never read from a file or a function like read_csv, "
            "read_parquet or glob.\n"
            "4. Include a LIMIT clause of at most {max_rows}.\n\n"
            "WRITING GOOD SQL HERE:\n"
            "- Aggregate. The caller needs an answer, not raw rows. If the "
            "request implies one number, return one row.\n"
            "- Give every computed column an explicit, readable alias. The "
            "alias becomes a column header a human reads.\n"
            "- Round money to 2 decimals.\n"
            "- Dates are real DATE columns: use date_trunc('month', col), "
            "EXTRACT(YEAR FROM col), or direct comparison to DATE '2025-07-01'. "
            "Do not use strftime.\n"
            "- Text codes stay text. Never cast a procedure or diagnosis code "
            "to a number.\n"
            "- When ranking, ORDER BY the measure DESC and LIMIT to the count "
            "asked for.\n"
            "- Read the column descriptions before choosing a filter. They "
            "state the business rules, and the rules are not guessable from the "
            "column names.",
        ),
        (
            "human",
            "WAREHOUSE SCHEMA:\n{schema}\n\n"
            "WORKED EXAMPLES:\n{examples}\n\n"
            "REQUEST: {intent}",
        ),
    ]
)

SQL_EXAMPLES = """\
Request: How much did the plan pay in total during 2025?
SQL:
SELECT ROUND(SUM(plan_paid), 2) AS total_plan_paid
FROM claims
WHERE status = 'paid'
  AND service_date >= DATE '2025-01-01'
  AND service_date <  DATE '2026-01-01'
LIMIT 1

Request: Which five denial reasons were most common, with how often each occurred?
SQL:
SELECT d.denial_code,
       d.short_description,
       COUNT(*) AS denial_count
FROM claims c
JOIN denial_codes d ON d.denial_code = c.denial_code
WHERE c.status = 'denied'
GROUP BY d.denial_code, d.short_description
ORDER BY denial_count DESC
LIMIT 5\
"""

SQL_REPAIR = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You fix a DuckDB SELECT statement that failed.\n\n"
            "You are given the schema, the statement, and the exact error. "
            "Return a corrected single SELECT statement obeying the same hard "
            "rules as before: one statement, read-only, only the given tables "
            "and columns, a LIMIT of at most {max_rows}.\n\n"
            "Read the error literally. If it names a column that does not "
            "exist, find the right column in the schema rather than guessing a "
            "similar name. If it names a type mismatch, cast explicitly. If it "
            "says a table is not in the warehouse, use one that is.\n\n"
            "Change as little as possible. Do not redesign the query.",
        ),
        (
            "human",
            "WAREHOUSE SCHEMA:\n{schema}\n\n"
            "ORIGINAL REQUEST: {intent}\n\n"
            "STATEMENT THAT FAILED:\n{sql}\n\n"
            "ERROR:\n{error}",
        ),
    ]
)

SYNTHESIZE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You answer a question using only the evidence provided.\n\n"
            "EVIDENCE COMES IN TWO KINDS, AND THEY ARE CITED DIFFERENTLY:\n\n"
            "1. A RESULT TABLE from a SQL query. These are the numbers. State "
            "them exactly as given -- do not round further, do not "
            "recalculate, do not estimate. Numbers need no bracket citation; "
            "say where they came from in words if it helps.\n\n"
            "2. PASSAGES from policy documents, numbered [1], [2], and so on. "
            "Every factual claim taken from a passage MUST carry its bracket "
            "citation, placed immediately after the claim.\n\n"
            "RULES:\n"
            "- Never state a fact that is not in the evidence. If the evidence "
            "does not answer the question, say so plainly and say what is "
            "missing.\n"
            "- Never invent a citation number. Only cite passages you were "
            "given.\n"
            "- If the table is empty, say the query returned no matching "
            "records. Do not describe that as zero unless zero is the answer.\n"
            "- Lead with the answer. Explanation second.\n"
            "- Be specific about numbers and deadlines. Vagueness on a deadline "
            "is a wrong answer.\n"
            "- Plain prose. No headings unless the answer genuinely has parts.",
        ),
        (
            "human",
            "QUESTION: {question}\n\n"
            "RESULT TABLE:\n{table}\n\n"
            "PASSAGES:\n{passages}",
        ),
    ]
)

CRITIQUE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You audit a drafted answer against the evidence it was given.\n\n"
            "Your only question is: is every factual claim in the answer "
            "supported by the evidence? You are not judging style, length, "
            "tone or helpfulness.\n\n"
            "Mark it unsupported when the answer:\n"
            "- states a number that does not appear in the result table;\n"
            "- states a rule, deadline or requirement not present in any "
            "passage;\n"
            "- cites a passage number it was not given;\n"
            "- describes an empty result as a zero, or vice versa.\n\n"
            "Do NOT mark it unsupported for being brief, for declining to "
            "answer when the evidence is genuinely absent, or for omitting "
            "something you personally would have included. A correct refusal "
            "is a supported answer.",
        ),
        (
            "human",
            "QUESTION: {question}\n\n"
            "RESULT TABLE:\n{table}\n\n"
            "PASSAGES:\n{passages}\n\n"
            "DRAFTED ANSWER:\n{answer}",
        ),
    ]
)

DIRECT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are DualPath, a question-answering system over {domain}.\n\n"
            "You have two data sources: a SQL warehouse of records, and a "
            "corpus of policy documents. This turn needs neither -- it is "
            "conversational.\n\n"
            "Answer in one or two sentences. If the user is asking what you can "
            "do, say it concretely: you answer questions about the records and "
            "about the policies, and you can combine both.",
        ),
        ("human", "{question}"),
    ]
)

REFUSAL_TEMPLATE = (
    "That question is outside what I can answer. I work with {domain}\n\n"
    "I can help with questions about the records in the warehouse (counts, "
    "totals, trends, specific claims) and with the policy documents "
    "(deadlines, requirements, what is covered)."
)
