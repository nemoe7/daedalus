"""The provider file edits: comments, quotes, one-line maps and dropped keys."""

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import CommentMark
from ruamel.yaml.scalarstring import DoubleQuotedScalarString
from ruamel.yaml.tokens import CommentToken

from daedalus.config import provider_edit as edit

FILE = "# The provider list.\nname: old\nnote: keep  # stay\nrpm: 5\n"


def test_merge_text_keeps_the_comments_of_the_file() -> None:
  """A new value keeps the comment above the key and the one at the end of the line."""
  out = edit.merge_text(FILE, {"name": "new", "note": "keep", "rpm": 5})
  assert "# The provider list." in out
  assert "note: keep  # stay" in out
  assert "name: new" in out


def test_merge_text_writes_a_document_over_empty_text() -> None:
  """An empty file takes the whole form."""
  assert "name: x" in edit.merge_text("", {"name": "x"})
  assert "name: x" in edit.merge_text("   \n", {"name": "x"})


def test_merge_text_drops_the_keys_the_form_does_not_send() -> None:
  """A key that the form leaves out goes away."""
  assert "gone" not in edit.merge_text("name: old\ngone: 1\n", {"name": "new"})


def test_same_needs_the_same_kind() -> None:
  """1 does not stand for true, and a string does not stand for a number."""
  assert edit.same(1, 1) is True
  assert edit.same(1, True) is False
  assert edit.same("1", 1) is False
  assert edit.same("a", "a") is True


def test_fresh_quotes_a_value_yaml_would_read_back_wrong() -> None:
  """A value that needs quotes gets them. A plain word does not."""
  assert isinstance(edit.fresh("true"), DoubleQuotedScalarString)
  assert edit.fresh("plain") == "plain"


def test_merge_text_spaces_the_braces_of_a_one_line_map() -> None:
  """A one-line map under `models` comes out as `{ rpm: 5 }`, with the spaces."""
  assert "{ rpm: 5 }" in edit.merge_text("models:\n", {"models": {"rpm": 5}})


def test_spaced_keeps_the_comment_column() -> None:
  """The spaces inside the braces come out of the gap before the comment."""
  found = edit.FLOW_LINE.search("slug: {rpm: 5}    # note\n")
  assert found is not None
  assert edit.spaced(found) == "slug: { rpm: 5 }  # note"


def test_comment_parts_splits_the_end_of_line_comment() -> None:
  """The end-of-line comment comes back apart from the lines that follow it."""
  token = CommentToken("# stay\n# next\n", CommentMark(0))
  assert edit.comment_parts(token) == ("# stay\n", "# next\n")


def test_comment_parts_of_no_token() -> None:
  """No token gives no comment and no text."""
  assert edit.comment_parts(None) == ("", "")


def test_merged_keeps_a_node_it_does_not_change() -> None:
  """An unchanged map keeps its own identity, comments and all."""
  old = edit.round_trip().load("name: old\n")
  assert edit.merged(old, {"name": "old"}) is old


def test_merge_text_keeps_an_end_of_line_comment_when_a_list_changes() -> None:
  """An item that stays in the list keeps its end-of-line comment."""
  out = edit.merge_text("models:\n  - a  # first\n  - b\n", {"models": ["a", "c"]})
  assert "# first" in out
  assert "- c" in out
  assert "- b" not in out


def test_merge_text_gives_a_new_key_a_node() -> None:
  """A key the file does not have yet comes out in the text."""
  out = edit.merge_text("name: old\n", {"name": "old", "added": {"rpm": 5}})
  assert "added:" in out
  assert "rpm: 5" in out


def test_round_trip_keeps_the_indents_of_a_provider_file() -> None:
  """The reader gives a commented map back, with the sequence indent of the files."""
  found = edit.round_trip()
  assert isinstance(found.load("name: old\n"), CommentedMap)
  assert found.map_indent == 2
  assert found.sequence_dash_offset == 2
