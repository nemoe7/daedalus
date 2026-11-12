"""Prose detectors. Each one returns `(line, column, count)` rows for one text scope."""

import re

from config import MIN_THE_COUNT, THE_DENSITY_CAP

from prose.markdown import (
  prose_block_matches,
  prose_blocks,
  prose_line_matches,
  prose_lines,
)

PASSIVE_VOICE_RE = re.compile(
  r"\b(?:am|is|are|was|were|be|been|being)\s+"
  r"(?:(?:not|never|always|only|also|still|often|already)\s+)?"
  r"([a-z]{3,}ed|written|taken|given|made|done|kept|held|found|chosen|"
  r"known|shown|seen|built|sent|run|broken|spoken|stolen|hidden|driven|drawn|"
  r"forgiven|forgotten|frozen|grown|thrown|worn|torn)\b",
  re.IGNORECASE,
)


PASSIVE_STATE_ADJECTIVES = {
  "able",
  "alleged",
  "armed",
  "ashamed",
  "complicated",
  "concerned",
  "crowded",
  "disabled",
  "enabled",
  "expected",
  "experienced",
  "fixed",
  "interested",
  "isolated",
  "limited",
  "locked",
  "mixed",
  "needed",
  "pleased",
  "prepared",
  "related",
  "required",
  "reserved",
  "restricted",
  "satisfied",
  "scoped",
  "shared",
  "signed",
  "sorted",
  "staged",
  "structured",
  "supported",
  "supposed",
  "tired",
  "tracked",
  "trusted",
  "typed",
  "unchanged",
  "unexpected",
  "unmodified",
  "unsupported",
  "unused",
}


def prose_passives(text):
  """Find passive-voice constructions across prose blocks outside code fences and code spans."""
  return prose_block_matches(
    text,
    lambda clean: [
      match
      for match in PASSIVE_VOICE_RE.finditer(clean)
      if match.group(1).lower() not in PASSIVE_STATE_ADJECTIVES
      or re.match(r"\s+by\b", clean[match.end() :], re.IGNORECASE)
    ],
  )


PHRASAL_VERB_RE = re.compile(
  r"\b(?:carry\s+out|set\s+up|tear\s+down|spin\s+up|hand\s+over|fill\s+out|"
  r"find\s+out|figure\s+out|point\s+out|turn\s+on|turn\s+off|look\s+up|"
  r"back\s+up|clean\s+up|plug\s+in|bail\s+out|opt\s+in|opt\s+out|roll\s+back|"
  r"wire\s+up|hook\s+up|boot\s+up|shut\s+down)\b",
  re.IGNORECASE,
)


ELLIPSIS_RE = re.compile(
  r",\s+(?:and|or)\s+(?:is|are|was|were|has|have|had)\b|"
  r"\b(?:the|a|an|this|that|these|those|its|their|our|your)\s+[a-z]{3,}\s+is\s+([a-z]{3,}ed)\s+and\s+([a-z]{3,}ed)\b",
  re.IGNORECASE,
)


SERIAL_COMMA_RE = re.compile(
  r"\b([A-Za-z0-9_'-]+), ([A-Za-z0-9_'-]+) (?:and|or) "
  r"([A-Za-z0-9_'-]+)(?=[.!?:;)]|\s|$)"
)


MIXED_SLASH_OR_RE = re.compile(
  r"\b([A-Za-z0-9_'-]+) / ([A-Za-z0-9_'-]+),? (?:and|or) "
  r"([A-Za-z0-9_'-]+)(?=[.!?:;)]|\s|$)|"
  r"\b([A-Za-z0-9_'-]+) (?:and|or) ([A-Za-z0-9_'-]+) / "
  r"([A-Za-z0-9_'-]+)(?=[.!?:;)]|\s|$)",
  re.IGNORECASE,
)


CLAUSE_OPENERS = {
  "when",
  "if",
  "after",
  "before",
  "while",
  "because",
  "since",
  "unless",
  "where",
  "once",
  "in",
  "on",
  "for",
  "from",
  "with",
  "without",
  "by",
  "at",
  "to",
  "under",
  "during",
  "and",
  "or",
  "not",
  "the",
  "a",
  "an",
  "that",
  "which",
  "who",
  "whose",
  "whether",
  "never",
  "always",
  "only",
  "also",
  "either",
  "neither",
  "both",
  "such",
  "as",
  "so",
  "yet",
  "but",
  "nor",
  "then",
  "thus",
  "instead",
  "rather",
  "directly",
  "indented",
  "all",
  "them",
}


def prose_phrasal_verbs(text):
  return prose_block_matches(text, lambda clean: list(PHRASAL_VERB_RE.finditer(clean)))


def prose_ellipsis(text):
  return prose_block_matches(
    text,
    lambda clean: [
      match
      for match in ELLIPSIS_RE.finditer(clean)
      if not (
        match.group(1)
        and match.group(2)
        and (
          match.group(1).lower() in PASSIVE_STATE_ADJECTIVES
          or match.group(2).lower() in PASSIVE_STATE_ADJECTIVES
        )
      )
    ],
  )


def _serial_comma_hits(clean):
  stripped = re.sub(r"\[[^\]]*\]\([^)]*\)", lambda item: " " * len(item[0]), clean)
  hits = [
    match
    for match in SERIAL_COMMA_RE.finditer(stripped)
    if not (
      {match.group(1).lower(), match.group(2).lower(), match.group(3).lower()}
      & CLAUSE_OPENERS
    )
    and not clean[: match.start()].rstrip().lower().endswith("to")
  ]
  hits.extend(
    match
    for match in MIXED_SLASH_OR_RE.finditer(stripped)
    if not ({word.lower() for word in match.groups() if word} & CLAUSE_OPENERS)
  )
  hits.sort(key=lambda match: match.start())
  return hits


def prose_serial_commas(text):
  return prose_block_matches(text, _serial_comma_hits)


def prose_semicolons(text):
  """Find semicolons on prose lines outside code fences, code spans, and comments."""
  semicolons = []
  for number, clean in prose_lines(text):
    clean = re.sub(
      r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);",
      lambda match: " " * len(match[0]),
      clean,
    )
    if ";" in clean:
      semicolons.append((number, clean.index(";") + 1, clean.count(";")))
  return semicolons


DANGLING_CONJUNCTION_RE = re.compile(
  r"^\s*(?:[-*+]|\d+[.)])\s+.*[,;]\s+(?:and|or)[.;:]?\s*$",
  re.IGNORECASE,
)


NOMINALIZATION_RE = re.compile(
  r"\b(?:make|made|makes|making)\s+(?:a\s+|an\s+|the\s+)?"
  r"(?:decision|determination|adjustment|modification|selection|recommendation|assumption)\b|"
  r"\b(?:perform|performed|performs|performing|conduct|conducted|conducts|conducting)\s+"
  r"(?:a\s+|an\s+|the\s+)?"
  r"(?:validation|verification|investigation|analysis|assessment|evaluation|examination|inspection|comparison)\b|"
  r"\b(?:provide|provided|provides|providing|give|gave|gives|giving)\s+"
  r"(?:a\s+|an\s+|the\s+)?(?:description|explanation|indication|illustration)\b|"
  r"\b(?:is|are|was|were)\s+indicative\s+of\b|"
  r"\b(?:for\s+the\s+purpose\s+of|with\s+the\s+aim\s+of)\b",
  re.IGNORECASE,
)


COMPOUND_INSTRUCTION_RE = re.compile(
  r"\b(?:click|press|tap|select|open|close|save|delete|run|execute)\s+"
  r"[^.!?;,]{2,40},\s+and\s+then\s+"
  r"(?:click|press|tap|select|open|close|save|delete|run|execute)\b",
  re.IGNORECASE,
)


NOUN_CLUSTER_WORD = (
  r"(?:system|server|client|user|interface|configuration|management|process|service|"
  r"module|component|pipeline|workflow|database|connection|request|response|handler|"
  r"controller|policy|validation|execution|parameter|environment|storage|session|"
  r"token|state|schema|gateway|proxy|adapter|provider|registry)"
)


NOUN_CLUSTER_RE = re.compile(
  rf"\b{NOUN_CLUSTER_WORD}(?:\s+{NOUN_CLUSTER_WORD}){{3,}}\b",
  re.IGNORECASE,
)


SYNONYM_CLUSTER_RE = re.compile(
  r"\b(?:rules?\s+(?:and|or)\s+regulations?|null\s+and\s+void|each\s+and\s+every|"
  r"first\s+and\s+foremost|basic\s+and\s+fundamental|various\s+and\s+sundry|"
  r"checks?\s+and\s+validations?)\b",
  re.IGNORECASE,
)


COMPOUND_TENSE_RE = re.compile(
  r"\b(?:will\s+have\s+[a-z]{3,}ed|would\s+have\s+[a-z]{3,}ed|"
  r"(?:has|have|had)\s+been\s+[a-z]{3,}ing)\b",
  re.IGNORECASE,
)


STAGED_FRAME_RE = re.compile(
  r"\b(?:let's\s+dive\s+in|without\s+further\s+ado|in\s+today's\s+fast-paced\s+world|"
  r"in\s+conclusion,\s+by\s+leveraging|all\s+things\s+considered,\s+it\s+is\s+clear|"
  r"hope\s+this\s+helps)\b",
  re.IGNORECASE,
)


STACKED_QUALIFIER_RE = re.compile(
  r"\b(?:very\s+unique|completely\s+essential|absolutely\s+critical|"
  r"truly\s+remarkable|undeniably\s+important|extremely\s+crucial|highly\s+pivotal)\b",
  re.IGNORECASE,
)


AI_VOCABULARY_RE = re.compile(
  r"\b(?:delve|delves|delved|delving|tapestry|testament\s+to|beacon\s+of|"
  r"paradigm\s+shift|game-changer|groundbreaking|synergy|unleash(?:ing)?|"
  r"elevate\s+your|foster(?:ing)?\s+innovation|realm\s+of)\b",
  re.IGNORECASE,
)


ARROW_RE = re.compile(r"\u2192")


CHATBOT_COURTESY_RE = re.compile(
  r"\b(?:as\s+an\s+ai(?:\s+language\s+model)?|"
  r"as\s+of\s+my\s+last\s+(?:knowledge\s+)?(?:update|cutoff)|"
  r"i\s+don't\s+have\s+access\s+to\s+real-time|certainly!\s+here\s+is|"
  r"sure\s+thing!\s+here)\b",
  re.IGNORECASE,
)


CURLY_QUOTE_RE = re.compile(r"[\u201c\u201d\u2018\u2019]")


NEGATIVE_CONTRAST_RE = re.compile(
  r"\bnot\s+(?:just|merely|simply)\s+[^,.!?;]{2,40},\s+but\s+"
  r"(?:rather|instead|also)?\s*[A-Za-z]",
  re.IGNORECASE,
)


APHORISM_RE = re.compile(
  r"\b(?:at\s+the\s+end\s+of\s+the\s+day\b|the\s+bottom\s+line\s+is\b|"
  r"it\s+goes\s+without\s+saying\b|key\s+takeaway:|why\s+this\s+matters:)",
  re.IGNORECASE,
)


RHETORICAL_SETUP_RE = re.compile(
  r"\b(?:you\s+might\s+be\s+wondering|have\s+you\s+ever\s+wondered|"
  r"some\s+critics\s+might\s+argue|what\s+if\s+i\s+told\s+you)\b",
  re.IGNORECASE,
)


PROSE_DASH_RE = re.compile(
  r"(?:[A-Za-z0-9]\u2014[A-Za-z0-9]|\s\u2013\s|\\u201[34])",
  re.IGNORECASE,
)


UI_STRING_DASH_RE = re.compile(
  r"""(['"`])(?:\\.|(?!\1)[^\r\n])*?(?:[\u2013\u2014]|\\u201[34])(?:\\.|(?!\1)[^\r\n])*?\1""",
  re.IGNORECASE,
)


VAGUE_ATTRIBUTION_RE = re.compile(
  r"\b(?:experts\s+agree\s+that|studies\s+have\s+shown\s+that|"
  r"researchers\s+say\s+that|industry\s+leaders\s+believe|"
  r"many\s+people\s+think\s+that)\b",
  re.IGNORECASE,
)


COPULA_AVOIDANCE_RE = re.compile(
  r"\b(?:serves\s+as\s+a|stands\s+as\s+a|boasts\s+a|"
  r"features\s+a\s+wide\s+array\s+of)\b",
  re.IGNORECASE,
)


BOLD_LABEL_BULLET_RE = re.compile(
  r"^\s*[-*+]\s+\*\*[A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*)?:\*\*"
)


EMOJI_CHAR = r"[\U0001F300-\U0001FAFF\u2600-\u27BF]"


CONSECUTIVE_EMOJI_RE = re.compile(rf"{EMOJI_CHAR}\s*{EMOJI_CHAR}")


SELF_NARRATION_RE = re.compile(
  r"\b(?:this\s+section\s+(?:covers|explains|describes|outlines)|"
  r"in\s+this\s+(?:document|section|guide),\s+we\s+will)\b",
  re.IGNORECASE,
)


WORDY_PHRASE_RE = re.compile(
  r"\b(?:in\s+order\s+to\s+[a-z]{2,}|prior\s+to|subsequent\s+to|"
  r"due\s+to\s+the\s+fact\s+that|in\s+the\s+event\s+that|by\s+means\s+of|"
  r"at\s+this\s+point\s+in\s+time|at\s+the\s+present\s+time|"
  r"(?:is|are|was|were)\s+(?:able\s+to|capable\s+of))\b",
  re.IGNORECASE,
)


ARCHAIC_CONNECTOR_RE = re.compile(
  r"\b(?:heretofore|herein(?:after)?|there(?:of|in|by)|where(?:by|in|of)|"
  r"aforementioned|pursuant\s+to|inter\s+alia)\b",
  re.IGNORECASE,
)


INLINE_LIST_RE = re.compile(
  r"(?:(?<!\w)\(1\)\s+\S.*?\s+\(2\)\s+\S.*?\s+\(3\)\s+\S|"
  r"(?<!\w)\(a\)\s+\S.*?\s+\(b\)\s+\S.*?\s+\(c\)\s+\S)",
  re.IGNORECASE,
)


DOUBLE_NEGATIVE_RE = re.compile(
  r"\b(?:not\s+(?:uncommon|unlikely|impossible|insignificant|unreasonable|"
  r"infrequent(?:ly)?|unavailable|unlike|unaware)|never\s+fails\s+to)\b",
  re.IGNORECASE,
)


def prose_dangling_conjunctions(text):
  return prose_line_matches(
    text, lambda clean: list(DANGLING_CONJUNCTION_RE.finditer(clean))
  )


def prose_nominalizations(text):
  return prose_block_matches(
    text, lambda clean: list(NOMINALIZATION_RE.finditer(clean))
  )


def prose_wordy_phrases(text):
  return prose_block_matches(
    text,
    lambda clean: list(WORDY_PHRASE_RE.finditer(clean)),
    skip_kinds=frozenset({"blockquote"}),
  )


def prose_archaic_connectors(text):
  return prose_block_matches(
    text, lambda clean: list(ARCHAIC_CONNECTOR_RE.finditer(clean))
  )


def prose_inline_lists(text):
  return prose_block_matches(text, lambda clean: list(INLINE_LIST_RE.finditer(clean)))


def prose_double_negatives(text):
  return prose_block_matches(
    text, lambda clean: list(DOUBLE_NEGATIVE_RE.finditer(clean))
  )


def prose_compound_instructions(text):
  return prose_block_matches(
    text, lambda clean: list(COMPOUND_INSTRUCTION_RE.finditer(clean))
  )


def prose_noun_clusters(text):
  return prose_block_matches(text, lambda clean: list(NOUN_CLUSTER_RE.finditer(clean)))


def prose_synonym_clusters(text):
  return prose_block_matches(
    text, lambda clean: list(SYNONYM_CLUSTER_RE.finditer(clean))
  )


def prose_compound_tenses(text):
  return prose_block_matches(
    text, lambda clean: list(COMPOUND_TENSE_RE.finditer(clean))
  )


def prose_staged_frames(text):
  return prose_block_matches(text, lambda clean: list(STAGED_FRAME_RE.finditer(clean)))


def prose_stacked_qualifiers(text):
  return prose_block_matches(
    text, lambda clean: list(STACKED_QUALIFIER_RE.finditer(clean))
  )


def prose_ai_vocabulary(text):
  return prose_block_matches(text, lambda clean: list(AI_VOCABULARY_RE.finditer(clean)))


def prose_arrows(text):
  return prose_line_matches(text, lambda clean: list(ARROW_RE.finditer(clean)))


def prose_chatbot_courtesy(text):
  return prose_block_matches(
    text, lambda clean: list(CHATBOT_COURTESY_RE.finditer(clean))
  )


def prose_curly_quotes(text):
  return prose_line_matches(text, lambda clean: list(CURLY_QUOTE_RE.finditer(clean)))


def prose_negative_contrasts(text):
  return prose_block_matches(
    text, lambda clean: list(NEGATIVE_CONTRAST_RE.finditer(clean))
  )


def prose_aphorisms(text):
  return prose_block_matches(text, lambda clean: list(APHORISM_RE.finditer(clean)))


def prose_rhetorical_setups(text):
  return prose_block_matches(
    text, lambda clean: list(RHETORICAL_SETUP_RE.finditer(clean))
  )


def prose_repeated_openers(text):
  """Find three consecutive sentences in a prose block with the same two-word opener."""
  rows = []
  for start_line, _, clean, _ in prose_blocks(text):
    sentences = [
      part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()
    ]
    openers = []
    for sentence in sentences:
      words = re.findall(r"[A-Za-z]+", sentence)
      openers.append(
        f"{words[0].lower()} {words[1].lower()}" if len(words) >= 2 else ""
      )
    for pos in range(len(openers) - 2):
      if openers[pos] and openers[pos] == openers[pos + 1] == openers[pos + 2]:
        rows.append((start_line, 1, 1))
        break
  return rows


def prose_dashes(text):
  return prose_line_matches(text, lambda clean: list(PROSE_DASH_RE.finditer(clean)))


def prose_vague_attributions(text):
  return prose_block_matches(
    text, lambda clean: list(VAGUE_ATTRIBUTION_RE.finditer(clean))
  )


def prose_copula_avoidance(text):
  return prose_block_matches(
    text, lambda clean: list(COPULA_AVOIDANCE_RE.finditer(clean))
  )


def prose_bold_label_bullets(text):
  return prose_line_matches(
    text, lambda clean: list(BOLD_LABEL_BULLET_RE.finditer(clean))
  )


def prose_consecutive_emojis(text):
  return prose_line_matches(
    text, lambda clean: list(CONSECUTIVE_EMOJI_RE.finditer(clean))
  )


def prose_self_narration(text):
  return prose_block_matches(
    text, lambda clean: list(SELF_NARRATION_RE.finditer(clean))
  )


LIST_DETERMINER = r"(?:the|its|their|our|your|this|that|these|those)"


LIST_ITEM_TAIL = (
  r"(?:(?:[ \t]+(?!(?:the|its|their|our|your|this|that|these|those|and|or)\b)"
  r"[A-Za-z0-9_'/-]+){1,5}|[ \t]{2,})"
)


LIST_ITEM_PATTERN = rf"\b{LIST_DETERMINER}{LIST_ITEM_TAIL}"


MIXED_DETERMINER_LIST_RE = re.compile(
  rf"{LIST_ITEM_PATTERN}(?:,[ \t]+{LIST_ITEM_PATTERN})+"
  rf"(?:,?[ \t]+(?:and|or)[ \t]+{LIST_ITEM_PATTERN}|,[ \t]+{LIST_ITEM_PATTERN})",
  re.IGNORECASE,
)


LEADING_DETERMINER_RE = re.compile(
  rf"(?:^|,[ \t]+(?:(?:and|or)[ \t]+)?|[ \t]+(?:and|or)[ \t]+)({LIST_DETERMINER})\b",
  re.IGNORECASE,
)


def _mixed_list_the_discount(clean):
  """Return the number of leading 'the' hits discounted by 3+ item mixed-determiner lists."""
  discount = 0
  for match in MIXED_DETERMINER_LIST_RE.finditer(clean):
    determiners = [
      det.group(1).lower() for det in LEADING_DETERMINER_RE.finditer(match.group(0))
    ]
    if len(determiners) >= 3 and any(d != "the" for d in determiners):
      the_count = sum(1 for d in determiners if d == "the")
      if the_count > 1:
        discount += the_count - 1
  return discount


def prose_the_density(text, cap=THE_DENSITY_CAP, min_the=MIN_THE_COUNT):
  """Find prose blocks where 'the' appears at least min_the times and >= cap% of words."""
  rows = []
  for start_line, _, clean, _ in prose_blocks(text):
    words = re.findall(r"[A-Za-z0-9_'-]+", clean)
    hits = list(re.finditer(r"\bthe\b", clean, re.IGNORECASE))
    effective_hits = max(0, len(hits) - _mixed_list_the_discount(clean))
    if effective_hits >= min_the and words and hits:
      density = round(effective_hits * 100 / len(words))
      if density >= cap:
        rows.append((start_line, hits[0].start() + 1, density))
  return rows


# Rule keys in rules.PROSE_RULE_IDS. Findings come out in this order.
DETECTORS = (
  ("dangling_conjunction", prose_dangling_conjunctions),
  ("nominalization", prose_nominalizations),
  ("wordy_phrase", prose_wordy_phrases),
  ("archaic_connector", prose_archaic_connectors),
  ("passive", prose_passives),
  ("phrasal", prose_phrasal_verbs),
  ("ellipsis", prose_ellipsis),
  ("compound_instruction", prose_compound_instructions),
  ("noun_cluster", prose_noun_clusters),
  ("synonym_cluster", prose_synonym_clusters),
  ("compound_tense", prose_compound_tenses),
  ("inline_list", prose_inline_lists),
  ("double_negative", prose_double_negatives),
  ("staged_frame", prose_staged_frames),
  ("stacked_qualifier", prose_stacked_qualifiers),
  ("ai_vocabulary", prose_ai_vocabulary),
  ("arrow", prose_arrows),
  ("chatbot_courtesy", prose_chatbot_courtesy),
  ("curly_quote", prose_curly_quotes),
  ("negative_contrast", prose_negative_contrasts),
  ("aphorism", prose_aphorisms),
  ("rhetorical_setup", prose_rhetorical_setups),
  ("repeated_opener", prose_repeated_openers),
  ("prose_dash", prose_dashes),
  ("vague_attribution", prose_vague_attributions),
  ("copula_avoidance", prose_copula_avoidance),
  ("bold_label_bullet", prose_bold_label_bullets),
  ("consecutive_emoji", prose_consecutive_emojis),
  ("self_narration", prose_self_narration),
  ("serial_comma", prose_serial_commas),
)
