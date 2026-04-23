from __future__ import annotations

from abc import ABC, abstractmethod

from chanakya.arthashastra.models import AdaptorError, AdaptorMetrics, AdaptorRequest, AdaptorResponse


class BaseAdaptor(ABC):
    @abstractmethod
    def probe(self) -> bool:
        """
        Return True if this adaptor can run on the current machine.
        Never throws.
        """

    @abstractmethod
    async def dispatch(
        self, request: AdaptorRequest
    ) -> tuple[AdaptorResponse, AdaptorMetrics] | AdaptorError:
        """
        Dispatch a request and return either:
        - (AdaptorResponse, AdaptorMetrics)
        - AdaptorError

        Must not leak raw subprocess errors above this layer.
        """

    @abstractmethod
    def adaptor_info(self) -> dict:
        """
        Static metadata for registry and diagnostics.
        """

