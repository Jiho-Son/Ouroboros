from __future__ import annotations

import pytest

from src.analysis.walk_forward_split import generate_walk_forward_splits


def test_generates_sequential_folds() -> None:
    folds = generate_walk_forward_splits(
        n_samples=30,
        train_size=10,
        test_size=5,
    )
    assert len(folds) == 4
    assert folds[0].train_indices == list(range(0, 10))
    assert folds[0].test_indices == list(range(10, 15))
    assert folds[1].train_indices == list(range(5, 15))
    assert folds[1].test_indices == list(range(15, 20))


def test_purge_removes_boundary_samples_before_test() -> None:
    folds = generate_walk_forward_splits(
        n_samples=25,
        train_size=8,
        test_size=4,
        purge_size=2,
    )
    first = folds[0]
    # test starts at 10, purge=2 => train end must be 7
    assert first.train_indices == list(range(0, 8))
    assert first.test_indices == list(range(10, 14))


def test_embargo_excludes_post_test_samples_from_next_train() -> None:
    folds = generate_walk_forward_splits(
        n_samples=45,
        train_size=15,
        test_size=5,
        step_size=10,
        embargo_size=3,
    )
    assert len(folds) >= 2
    # Fold1 test: 15..19, next fold train window: 10..24.
    # embargo_size=3 should remove 20,21,22 from fold2 train.
    second_train = folds[1].train_indices
    assert 20 not in second_train
    assert 21 not in second_train
    assert 22 not in second_train
    assert 23 in second_train


def test_respects_min_train_size_and_returns_empty_when_impossible() -> None:
    folds = generate_walk_forward_splits(
        n_samples=15,
        train_size=5,
        test_size=5,
        min_train_size=6,
    )
    assert folds == []


@pytest.mark.parametrize(
    ("n_samples", "train_size", "test_size"),
    [
        (0, 10, 2),
        (10, 0, 2),
        (10, 5, 0),
    ],
)
def test_invalid_args_raise(n_samples: int, train_size: int, test_size: int) -> None:
    with pytest.raises(ValueError):
        generate_walk_forward_splits(
            n_samples=n_samples,
            train_size=train_size,
            test_size=test_size,
        )
