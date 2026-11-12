"""Rotate avoided words to their plain words and collapse repeated list articles."""

import re

from rules import PROSE_RULE_IDS, RULE_IDS

# Exact inflections and their replacements.
ROTATION_FORMS = {
  "transmit": "send",
  "transmits": "sends",
  "transmitted": "sent",
  "transmitting": "sending",
  "transmitter": "sender",
  "transmitters": "senders",
  "kind": "type",
  "kinds": "types",
  "repository": "repo",
  "repositories": "repos",
  "pull request": "PR",
  "pull requests": "PRs",
  "name": "specify",
  "names": "specifies",
  "named": "specified",
  "naming": "specifying",
}


NEMOIZER_MODIFIED_NOUNS = (
  r"rules?|lists?|linters?|prose|skills?|configs?|checks?|scripts?|"
  r"parsers?|suites?|tests?|plugins?|domains?|targets?|markers?"
)


KEEP_LOWER = {"nemoizer"}


NAME_VERB_PATTERN = (
  r"(?:"
  r"(?:(?<=^)|(?<=\n)|(?<=^- )|(?<=\* )|(?<=\. )|(?<=, and )"
  r"|(?<=, or )|(?<=\bto )|(?<=\bmust )|(?<=\bcan )|(?<=\bmay )|(?<=\bwill )"
  r"|(?<=\bshould )|(?<=\bnever )|(?<=\balways )|(?<=\bnot )|(?<=\bplease )|(?<=\balso ))"
  r"name(?=\s+(?:(?:the|a|an|each|every|all|any|this|that|its|their)\s+(?!few\b)[A-Za-z]+|[A-Za-z]+s\b))"
  r"|"
  r"(?:(?<=\bit )|(?<=\bthat )|(?<=\bwhich )|(?<=\bwho )|(?<=\bsheet )"
  r"|(?<=\bcontrol )|(?<=\btable )|(?<=\brule )|(?<=\bclause )|(?<=\breport )"
  r"|(?<=\bconfig )|(?<=\bscript )|(?<=\bcheck )|(?<=\btool )|(?<=\bpage )|(?<=, and ))"
  r"names(?=\s+(?:the|a|an|each|every|all|any|this|that|its|their|[A-Z])[A-Za-z]*)"
  r"|(?<![A-Za-z0-9_$.\/-])names(?=\s+(?:the|a|an|each|every|all|any|this|that|its|their)\s+[A-Za-z]+)"
  r"|(?<![A-Za-z0-9_$.\/-])naming(?=\s+(?:the|a|an|each|every|all|any|this|that|its|their)\s+[A-Za-z]+)"
  r"|(?<![A-Za-z0-9_$.\/-])named(?=\s+(?:in|under|by)\s+the\s+[A-Za-z]+)"
  r")"
)


def rotation_pattern(prefix):
  """Match only the listed inflections, outside identifiers and paths."""
  forms = (form for form in ROTATION_FORMS if form.startswith(prefix))
  choices = "|".join(re.escape(form) for form in sorted(forms, key=len, reverse=True))
  return rf"(?<![A-Za-z0-9_$.\/-])(?:{choices})(?![A-Za-z0-9_\/-]|\.[A-Za-z0-9_])"


ROTATIONS = (
  ("transmit", rotation_pattern("transmit"), "send"),
  ("kind", r"(?<!\bbe )(?<!\bin )" + rotation_pattern("kind"), "type"),
  ("repository", rotation_pattern("repositor"), "repo"),
  (
    "pull request",
    r"(?<![A-Za-z0-9])(?:"
    + "|".join(
      re.escape(form).replace(r"\ ", r"[ \t]+")
      for form in sorted(ROTATION_FORMS, key=len, reverse=True)
      if form.startswith("pull request")
    )
    + r")(?![A-Za-z0-9])",
    "PR",
  ),
  ("name", NAME_VERB_PATTERN, "specify"),
  (
    "nemoizer",
    (
      rf"(?<![A-Za-z0-9_./:-])(?:the[ \t]+(?!nemoizer[ \t]+(?:{NEMOIZER_MODIFIED_NOUNS})\b))?"
      r"nemoizer(?![A-Za-z0-9_/-]|\.[A-Za-z0-9_])"
    ),
    "nemoizer",
  ),
)


CURLY_QUOTES = str.maketrans(
  {"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"}
)


def plain(word, name, use):
  """Give the matched word its plain word, and keep the old word's case."""
  if name in KEEP_LOWER:
    return use
  normalized = " ".join(word.lower().split())
  form = ROTATION_FORMS[normalized]
  if word.isupper():
    return form.upper()
  if word[:1].isupper():
    return form[:1].upper() + form[1:]
  return form


LIST_THE_SPAN = re.compile(
  r"\b(the[ \t]+[^,.;:!?()\n]+)"
  r"((?:,[ \t]+(?:(?:and|or)[ \t]+)?the[ \t]+[^,.;:!?()\n]+)+"
  r"(?:\s+(?:and|or)[ \t]+the[ \t]+[^,.;:!?()\n]+)?)",
  re.IGNORECASE,
)


SUBSEQUENT_LIST_THE = re.compile(
  r"(,[ \t]+(?:(?:and|or)[ \t]+)?|[ \t]+(?:and|or)[ \t]+)the[ \t]+",
  re.IGNORECASE,
)


def collapse_list_articles(line):
  """Collapse repeated leading 'the' articles across comma-separated list items."""
  return LIST_THE_SPAN.sub(
    lambda match: match.group(1) + SUBSEQUENT_LIST_THE.sub(r"\1", match.group(2)),
    line,
  )


PROTECTED_ROTATE_SPAN = re.compile(
  r"(`+)(?:(?!\1).)+?\1"
  r"|(?<=\()https?://[^\s)]+(?=\))"
  r"|https?://[^\s<>]+"
  r"|\$\{\{.*?\}\}"
  r"|:(?:param|type|ivar|vartype|keyword)\s+[A-Za-z0-9_]+\s*:"
  r"|^\s*(?:-\s+)?[A-Za-z0-9_.-]+\s*:(?=\s|$)"
)


def mask_protected_spans(line):
  """Replace inline code spans, workflow expressions, YAML keys, and :param <ident>: with spaces."""
  return PROTECTED_ROTATE_SPAN.sub(lambda match: " " * len(match.group(0)), line)


def _rotate_segment(segment, suppressed):
  for name, pattern, use in ROTATIONS:
    if RULE_IDS[name] in suppressed:
      continue
    segment = re.sub(
      pattern,
      lambda match, name=name, use=use: plain(match.group(0), name, use),
      segment,
      flags=re.IGNORECASE,
    )
  if PROSE_RULE_IDS["curly_quote"] not in suppressed:
    segment = segment.translate(CURLY_QUOTES)
  return segment


def rotate(line, suppressed=frozenset()):
  """Swap each matched word for its plain word, and take the exception words first."""
  out = []
  cursor = 0
  for match in PROTECTED_ROTATE_SPAN.finditer(line):
    if match.start() > cursor:
      out.append(_rotate_segment(line[cursor : match.start()], suppressed))
    out.append(match.group(0))
    cursor = match.end()
  if cursor < len(line):
    out.append(_rotate_segment(line[cursor:], suppressed))
  return "".join(out)


def rotation_legend(line):
  return bool(
    re.match(r"^\|\s*`?avoid`?\s*\|\s*`?use`?\s*\|", line.strip(), re.IGNORECASE)
  )
