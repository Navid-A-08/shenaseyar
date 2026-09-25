"""Random retriever. Exists only to test the evaluation harness end to end; it is not a baseline."""
import random


class RandomRetriever:
    def __init__(self, catalog_ids, seed=0):
        self._ids = sorted(catalog_ids)  # sorted: set order varies between runs
        self._rng = random.Random(seed)

    def search(self, text, k):
        return self._rng.sample(self._ids, min(k, len(self._ids)))
