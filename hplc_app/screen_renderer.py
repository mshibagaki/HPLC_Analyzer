"""Interactive screen surface boundary.

Only the application screen uses this module.  Reports and publication exports
keep their independent Matplotlib figure path, and scientific data never enters
the surface contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Tuple

from matplotlib.figure import Figure

from .qt_compat import QT_API
from .rendering import ScreenRendererCapabilities

if QT_API == 6:
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
else:
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg


class ScreenRenderSurface(ABC):
    """Minimal lifecycle contract required by the interactive application UI."""

    @property
    @abstractmethod
    def capabilities(self) -> ScreenRendererCapabilities:
        raise NotImplementedError

    @property
    @abstractmethod
    def figure(self):
        raise NotImplementedError

    @property
    @abstractmethod
    def widget(self):
        raise NotImplementedError

    @abstractmethod
    def connect_event(self, event_name: str, callback: Callable) -> int:
        raise NotImplementedError

    @abstractmethod
    def disconnect_event(self, connection_id: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def draw(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def draw_idle(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def renderer(self):
        raise NotImplementedError

    @abstractmethod
    def snapshot(self):
        raise NotImplementedError


class MatplotlibScreenRenderSurface(ScreenRenderSurface):
    """Current Qt/Matplotlib implementation of the screen-only contract."""

    _CAPABILITIES = ScreenRendererCapabilities(
        backend_id="matplotlib_qt",
        supports_vector_export=False,
        supports_native_snapshot=True,
        supports_matplotlib_artists=True,
    )

    def __init__(
        self,
        figsize: Tuple[float, float] = (8.0, 5.0),
        constrained_layout: bool = True,
    ):
        self._figure = Figure(
            figsize=figsize,
            constrained_layout=bool(constrained_layout),
        )
        self._widget = FigureCanvasQTAgg(self._figure)

    @property
    def capabilities(self) -> ScreenRendererCapabilities:
        return self._CAPABILITIES

    @property
    def figure(self):
        return self._figure

    @property
    def widget(self):
        return self._widget

    def connect_event(self, event_name: str, callback: Callable) -> int:
        return self._widget.mpl_connect(event_name, callback)

    def disconnect_event(self, connection_id: int) -> None:
        self._widget.mpl_disconnect(connection_id)

    def draw(self) -> None:
        self._widget.draw()

    def draw_idle(self) -> None:
        self._widget.draw_idle()

    def renderer(self):
        return self._widget.get_renderer()

    def snapshot(self):
        self.draw()
        return self._widget.grab()


def create_screen_render_surface(
    backend_id: str = "matplotlib_qt",
    figsize: Tuple[float, float] = (8.0, 5.0),
    constrained_layout: bool = True,
) -> ScreenRenderSurface:
    """Create a screen surface without exposing backend construction to the UI."""

    normalized = str(backend_id or "").strip().lower()
    if normalized != "matplotlib_qt":
        raise ValueError("Unsupported screen renderer backend: %s" % backend_id)
    return MatplotlibScreenRenderSurface(
        figsize=figsize,
        constrained_layout=constrained_layout,
    )
