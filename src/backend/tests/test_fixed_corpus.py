"""Frozen corpus source isolation and preparation must survive malformed input."""
from argparse import ArgumentTypeError, Namespace
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from arena.engine.projection import RULES_VERSION, digest
from arena.evaluation.fixed_corpus import _verify_observation, build_corpus, verify_corpus
from arena.evaluation.spec import ExperimentSpec, load_experiment_spec

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def corpus():
    return build_corpus(["effect-dev-20260926-00"], ["effect-test-20260926-00"])


def rehash(corpus):
    corpus["sha256"] = digest({key: value for key, value in corpus.items() if key != "sha256"})


@pytest.mark.parametrize("mutation", [
    "unknown_seed", "empty_split", "duplicate_seed", "cross_split_match", "missing_source",
    "duplicate_source", "source_split", "unknown_field", "post_game_score", "post_game_winner",
    "opponent_hands", "nested_reasoning", "future_play", "early_bottom_cards", "wrong_category",
    "duplicate_bin", "malformed_hand", "missing_observation",
])
def test_rehashed_invalid_corpus_rejected_without_replay(corpus, mutation):
    forged = deepcopy(corpus)
    first = forged["observations"][0]
    if mutation == "unknown_seed":
        first["source_seed"] = "unregistered-source"
    elif mutation == "empty_split":
        forged["development_seeds"] = []
    elif mutation == "duplicate_seed":
        forged["development_seeds"] *= 2
    elif mutation == "cross_split_match":
        first["source_match"] = "rule-policy/" + forged["test_seeds"][0]
    elif mutation == "missing_source":
        forged["sources"].pop()
    elif mutation == "duplicate_source":
        forged["sources"].append(deepcopy(forged["sources"][0]))
    elif mutation == "source_split":
        forged["sources"][0]["split"] = "test"
    elif mutation == "unknown_field":
        first["observation"]["future_actions"] = [{"seat": "E", "cards": ["♠A"]}]
    elif mutation == "post_game_score":
        first["observation"]["hand_score"] = 6
    elif mutation == "post_game_winner":
        first["observation"]["winner_team"] = "blue"
    elif mutation == "opponent_hands":
        first["observation"]["remaining_hands"] = {"E": ["♠A"]}
    elif mutation == "nested_reasoning":
        play = next(item for item in forged["observations"] if item["observation"]["play_history"])
        play["observation"]["play_history"][0]["reasoning"] = "another player's hidden reasoning"
    elif mutation == "future_play":
        first["observation"]["play_history"] = deepcopy(next(
            item["observation"]["play_history"] for item in forged["observations"]
            if item["observation"]["play_history"]))
    elif mutation == "early_bottom_cards":
        first["observation"]["dizhu_cards"] = [{"rank": 14, "suit": 4}]
    elif mutation == "wrong_category":
        first["category"] = "follow"
    elif mutation == "duplicate_bin":
        lead = [item for item in forged["observations"] if item["split"] == "development" and item["category"] == "lead"]
        lead[1]["temporal_bin"] = lead[0]["temporal_bin"]
    elif mutation == "malformed_hand":
        first["observation"]["hand_cards"] = None
    else:
        del first["observation"]
    rehash(forged)
    with pytest.raises(ValueError):
        verify_corpus(forged, replay=False)


def test_archived_visibility_schema_preserved_and_rules_version_not_mislabelled(corpus):
    archived = json.loads((ROOT / "docs/reviews/20260926-fixed-effects/corpus.json").read_text())
    assert archived["sha256"] == "bd7a44ef459750554a4a3f5929ed3785be79de56e535e1431ac225eb02039bb5"
    for item in archived["observations"]:
        _verify_observation(item)
    # The archive predates the current airplane rules. It must be replayed with
    # its saved source, rather than relabelled as current-rules evidence.
    if archived["rules_version"] != RULES_VERSION:
        with pytest.raises(ValueError, match="unsupported corpus/rules version"):
            verify_corpus(archived)
    assert verify_corpus(corpus)["test_observations"] == 8


@pytest.fixture(scope="module")
def cli():
    spec = importlib.util.spec_from_file_location("fixed_effect_study_cli_test", ROOT / "scripts/fixed_effect_study.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_namespace_preserves_defaults_and_separates_new_ids(cli):
    assert cli.planned_seeds("development", 1) == ["effect-dev-20260926-00"]
    assert cli.planned_seeds("test", 1) == ["effect-test-20260926-00"]
    dev = cli.planned_seeds("development", 2, "review-20261004")
    test = cli.planned_seeds("test", 2, "review-20261004")
    assert len(set(dev + test)) == 4
    assert test == ["review-20261004-test-00", "review-20261004-test-01"]
    assert not set(test) & set(cli.planned_seeds("test", 2))
    with pytest.raises(ValueError, match="positive"):
        cli.planned_seeds("test", 0)
    for invalid in ("../unsafe", "Upper", "with.dot", "x" * 32):
        with pytest.raises(ArgumentTypeError):
            cli.planned_seeds("test", 1, invalid)


def test_prepare_freezes_declared_seed_namespace_without_unseen_claim(cli, tmp_path):
    output = tmp_path / "plan"
    namespace = "x" * 31
    cli.prepare(Namespace(output=output, development_seeds=1, test_seeds=1,
                          seed_namespace=namespace))
    corpus = json.loads((output / "corpus.json").read_text())
    plan = json.loads((output / "plan.json").read_text())
    seeds = json.loads((output / "seeds-test.json").read_text())
    assert corpus["test_seeds"] == seeds["seeds"] == [f"{namespace}-test-00"]
    assert seeds["seed_set_id"] == f"{namespace}-test"
    assert plan["seed_namespace"] == namespace
    assert "does not certify" in plan["test_split_status"]
    assert plan["corpus_sha256"] == corpus["sha256"]
    assert plan["max_calls_per_model"] == 40
    for name in ("minimax", "qwen"):
        spec = load_experiment_spec(output / f"{name}.yaml")
        assert spec.experiment_id.endswith(namespace)
        for split in ("development", "test"):
            run_spec = spec.model_dump()
            run_spec["experiment_id"] += f"-{split}"
            assert ExperimentSpec.model_validate(run_spec).experiment_id.endswith(f"-{split}")
