import pytest

from deep_research.memory.memory_store import semantically_opposite


@pytest.mark.parametrize('a,b', [
    ("Jane was born in Steventon in North Hampshire.", "Jane Austen was born on 16 December 1775 in Steventon, Hampshire."),
    ("The novel was published in 1813.", "It appeared in 1813 in three volumes."),
    ("Notable growth in 2020.", "Growth in 2020 was notable."),
])
def test_cue_words_inside_other_words_are_not_negations(a, b):
    assert not semantically_opposite(a, b)


@pytest.mark.parametrize('a,b', [
    ("Paris is the capital of France.", "Paris is not the capital of France."),
    ("Revenue increased in 2020.", "Revenue decreased in 2020."),
    ("该政策获得支持。", "该政策遭到反对。"),
    ("公司未盈利。", "公司盈利。"),
])
def test_real_negations_and_antonyms_still_detected(a, b):
    assert semantically_opposite(a, b)
