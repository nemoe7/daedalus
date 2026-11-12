"""One record per rule ID. The lookup tables come from these records."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
  code: str
  family: str
  keys: tuple[str, ...] = ()
  description: str | None = None
  advisory: bool = False
  line_scoped: bool = False


RULES = (
  Rule("NMO100", "rotation", ("transmit", "kind", "nemoizer")),
  Rule("NMO901", "rotation", ("repository", "pull request", "name"), advisory=True),
  Rule(
    "STE001",
    "prose",
    ("dangling_conjunction",),
    "dangling conjunction",
    line_scoped=True,
  ),
  Rule("STE100", "prose", ("semicolon",), "semicolon in prose", line_scoped=True),
  Rule(
    "STE200",
    "prose",
    ("sentence",),
    "sentence has {count} words (cap {cap})",
    line_scoped=True,
  ),
  Rule("STE300", "prose", ("nominalization",), "nominalized verb"),
  Rule("STE400", "prose", ("wordy_phrase",), "wordy phrase"),
  Rule("STE401", "prose", ("archaic_connector",), "archaic or legalistic connector"),
  Rule("STE901", "prose", ("passive",), "passive voice", advisory=True),
  Rule("STE902", "prose", ("phrasal",), "phrasal verb", advisory=True),
  Rule("STE903", "prose", ("ellipsis",), "omitted passive auxiliary", advisory=True),
  Rule(
    "STE904", "prose", ("compound_instruction",), "compound instruction", advisory=True
  ),
  Rule("STE905", "prose", ("noun_cluster",), "noun cluster", advisory=True),
  Rule("STE906", "prose", ("synonym_cluster",), "synonym cluster", advisory=True),
  Rule("STE908", "prose", ("compound_tense",), "compound tense", advisory=True),
  Rule("STE909", "prose", ("inline_list",), "inline list in prose", advisory=True),
  Rule("STE910", "prose", ("double_negative",), "double negative", advisory=True),
  Rule("HMN100", "prose", ("staged_frame",), "staged frame"),
  Rule("HMN200", "prose", ("stacked_qualifier",), "stacked qualifier"),
  Rule("HMN300", "prose", ("ai_vocabulary",), "AI vocabulary"),
  Rule("HMN400", "prose", ("arrow",), "arrow in prose", line_scoped=True),
  Rule("HMN500", "prose", ("chatbot_courtesy",), "chatbot courtesy"),
  Rule("HMN600", "prose", ("curly_quote",), "curly quotation mark", line_scoped=True),
  Rule("HMN901", "prose", ("negative_contrast",), "negative contrast", advisory=True),
  Rule("HMN902", "prose", ("aphorism",), "aphorism or importance claim", advisory=True),
  Rule("HMN903", "prose", ("rhetorical_setup",), "rhetorical setup", advisory=True),
  Rule(
    "HMN904", "prose", ("repeated_opener",), "repeated sentence opener", advisory=True
  ),
  Rule(
    "HMN905",
    "prose",
    ("prose_dash",),
    "em or en dash in prose",
    advisory=True,
    line_scoped=True,
  ),
  Rule("HMN906", "prose", ("vague_attribution",), "vague attribution", advisory=True),
  Rule("HMN907", "prose", ("copula_avoidance",), "copula avoidance", advisory=True),
  Rule(
    "HMN908",
    "prose",
    ("bold_label_bullet",),
    "bold-label bullet",
    advisory=True,
    line_scoped=True,
  ),
  Rule(
    "HMN909",
    "prose",
    ("consecutive_emoji",),
    "consecutive emojis",
    advisory=True,
    line_scoped=True,
  ),
  Rule("HMN910", "prose", ("self_narration",), "self-narration", advisory=True),
  Rule(
    "NMP100", "prose", ("paragraph",), "paragraph has {count} sentences (cap {cap})"
  ),
  Rule(
    "NMP101", "prose", ("comment_block",), "comment block has {count} lines (cap {cap})"
  ),
  Rule(
    "NMU100",
    "prose",
    ("padded_ui_label",),
    'label "{text}" carries "{word}" ({count} words, cap {cap})',
  ),
  Rule("NMO003", "prose", ("serial_comma",), "missing Oxford comma"),
  Rule(
    "NMO902",
    "prose",
    ("the_density",),
    '"the" density is {count}% (cap {cap}%); '
    'collapse list "the" articles (--format) or drop "the" before proper names',
    advisory=True,
  ),
  Rule("NMU200", "unit", ("byte_unit",), "byte-unit divisor and label mismatch"),
  Rule("NMT100", "test", ("output_text",)),
  Rule("NMT200", "test", ("measurement",)),
  Rule(
    "NMT300",
    "test",
    ("timing_wait",),
    "unconditional real-time test wait",
    advisory=True,
  ),
  Rule("NMT400", "test", ("numeric_setting",), advisory=True),
  Rule("HMN000", "reserved"),
)


def family_ids(family):
  """Map each lookup key of one rule family to its rule ID."""
  return {
    key: rule.code for rule in RULES if rule.family == family for key in rule.keys
  }


RULE_IDS = family_ids("rotation")
PROSE_RULE_IDS = family_ids("prose")
NMU_RULE_IDS = family_ids("unit")
TEST_RULE_IDS = family_ids("test")
RESERVED_RULE_IDS = {rule.code for rule in RULES if rule.family == "reserved"}
KNOWN_RULE_IDS = {rule.code for rule in RULES}
ADVISORY_RULE_IDS = {rule.code for rule in RULES if rule.advisory}
LINE_PROSE_RULE_IDS = {rule.code for rule in RULES if rule.line_scoped}
RULE_DESCRIPTIONS = {rule.code: rule.description for rule in RULES if rule.description}
