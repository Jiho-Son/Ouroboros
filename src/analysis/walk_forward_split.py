"""Walk-forward splitter with purge/embargo controls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WalkForwardFold:
    train_indices: list[int]
    test_indices: list[int]

    @property
    def train_size(self) -> int:
        return len(self.train_indices)

    @property
    def test_size(self) -> int:
        return len(self.test_indices)


def generate_walk_forward_splits(
    *,
    n_samples: int,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
    purge_size: int = 0,
    embargo_size: int = 0,
    min_train_size: int = 1,
) -> list[WalkForwardFold]:
    """Generate chronological folds with purge/embargo leakage controls."""
    if n_samples <= 0:
        raise ValueError("n_samples must be positive")
    if train_size <= 0 or test_size <= 0:
        raise ValueError("train_size and test_size must be positive")
    if purge_size < 0 or embargo_size < 0:
        raise ValueError("purge_size and embargo_size must be >= 0")
    if min_train_size <= 0:
        raise ValueError("min_train_size must be positive")

    step = step_size if step_size is not None else test_size
    if step <= 0:
        raise ValueError("step_size must be positive")

    folds: list[WalkForwardFold] = []
    prev_test_end: int | None = None
    test_start = train_size + purge_size

    while test_start + test_size <= n_samples:
        test_end = test_start + test_size - 1
        train_end = test_start - purge_size - 1
        if train_end < 0:
            break

        train_start = max(0, train_end - train_size + 1)
        train_indices = list(range(train_start, train_end + 1))

        if prev_test_end is not None and embargo_size > 0:
            emb_from = prev_test_end + 1
            emb_to = prev_test_end + embargo_size
            train_indices = [i for i in train_indices if i < emb_from or i > emb_to]

        if len(train_indices) >= min_train_size:
            folds.append(
                WalkForwardFold(
                    train_indices=train_indices,
                    test_indices=list(range(test_start, test_end + 1)),
                )
            )

        prev_test_end = test_end
        test_start += step

    return folds
