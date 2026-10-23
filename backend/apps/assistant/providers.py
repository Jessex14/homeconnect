from abc import ABC, abstractmethod


class ProviderUnavailable(Exception):
    """Raised when no approved AI provider is configured or it fails."""


class RentAnswerProvider(ABC):
    """Provider-independent contract. Implementations must not perform writes."""

    @abstractmethod
    def answer(self, *, question, context, instructions):
        """Return {"answer": str, "source_ids": list[str]} for supplied context only."""


class UnconfiguredProvider(RentAnswerProvider):
    def answer(self, *, question, context, instructions):
        raise ProviderUnavailable("Rent Q&A is not configured on this server.")

