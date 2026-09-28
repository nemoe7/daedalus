---
name: deep-research
description: Researches a question in depth, with cited sources.
version: 0.1.0
---

# Deep Research

This skill makes a cited research report from many web sources. It uses only the builtin tools `search_web` and `fetch_url`. It does not run code and it does not open a browser. It assumes that Web Search is on in Open WebUI.

## When to Use

- "research", "deep research", "investigate", "find out everything about".
- "compare X and Y", "what is the current state of", "give me a report on".
- A question that needs more than 3 sources, or sources from the last months.

Do not use it for a short fact that 1 search answers.

## Prerequisites

- Web Search on (Admin Settings, Web Search).
- Native function calling on for the model, so that `search_web`, `fetch_url` and `view_skill` are available.

## How to Run

Plan, then search in rounds, then read the best pages, then check the claims, then write. Show short progress notes between rounds, so that the user can see the work.

## Quick Reference

- `search_web(query)`: a list of results with titles, links and short text.
- `fetch_url(url)`: the text of 1 page.
- Rounds: 3 to 5. Sources read in full: 6 to 15.

## Procedure

1. Write the plan: the main question, 3 to 6 sub-questions, and the kind of source for each (official docs, papers, news, data).
2. Round 1: 1 `search_web` call for each sub-question. Make each query different.
3. Choose the 2 or 3 best results for each sub-question: primary sources first, then recent sources, then well-known sites. Read each with `fetch_url`.
4. Write down each fact with its source link and date.
5. Find the gaps: sub-questions with no answer, facts from only 1 source, and sources that do not agree.
6. Rounds 2 to 5: new queries for the gaps only. Use other words, names, dates or the source language. Stop when each sub-question has 2 sources that agree, or after round 5.
7. Write the report:
   - A summary of 3 to 5 sentences with the direct answer.
   - 1 section for each sub-question.
   - A table for comparisons.
   - A "Not certain" list: claims with 1 source, or sources that do not agree.
   - Sources: a numbered list of links with dates. Put each number after the claim that it supports, like [3].

## Pitfalls

- Never send the same tool call with the same arguments again. The router counts 3 identical calls as a loop and stops the model.
- A page that does not open: go to the next result, and do not try it again.
- Search result text is short and can be old. Read the page before you use a fact from it.
- Do not use a fact that no source supports. Write "no source found" instead.
- Give the date of each source, and the date of the report, for fast-moving topics.

## Verification

Ask: "Research the current free tiers of 3 cloud AI APIs and compare them in a table." The answer has these parts:

- 2 or more search rounds.
- Pages read with `fetch_url`.
- A table and numbered source links.
