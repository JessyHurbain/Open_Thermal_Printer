"""Zone d'aperçu : affiche le rendu réel de l'étiquette et permet de déplacer les éléments à la souris.

L'aperçu est produit par exactement le même moteur de rendu que l'impression
(`renderer.render_label`) : ce que l'on voit est ce qui sera imprimé.
"""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..models import Label
from ..renderer import Rendered, render_label

MARGIN = 24  # px autour de l'étiquette


class LabelCanvas(QWidget):
    selectionChanged = pyqtSignal(int)  # index de l'élément sélectionné (-1 = aucun)
    elementMoved = pyqtSignal(int)  # pendant le glisser
    editFinished = pyqtSignal()  # relâchement de la souris après un déplacement
    deleteRequested = pyqtSignal()
    zoomRequested = pyqtSignal(float)  # Ctrl + molette : variation relative

    def __init__(self, label: Label, parent: QWidget | None = None):
        super().__init__(parent)
        self.label = label
        self.selected = -1
        self.px_per_mm = 3.0
        self._qimage: QImage | None = None
        self._buf = b""
        self._rendered: list[Rendered] = []
        self._drag_offset: tuple[float, float] | None = None
        self._moved = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.refresh()

    # ------------------------------------------------------------ données
    def set_label(self, label: Label) -> None:
        self.label = label
        self.selected = -1
        self.refresh()

    def refresh(self) -> None:
        """Recalcule le rendu et met à jour la taille du widget."""
        image, self._rendered = render_label(self.label)
        self._buf = image.tobytes()
        self._qimage = QImage(self._buf, image.width, image.height, image.width, QImage.Format.Format_Grayscale8)
        self._resize_to_zoom()
        self.update()

    def set_zoom(self, px_per_mm: float) -> None:
        self.px_per_mm = max(0.5, min(20.0, px_per_mm))
        self._resize_to_zoom()
        self.update()

    def set_selected(self, index: int) -> None:
        if index != self.selected:
            self.selected = index
            self.update()

    def _resize_to_zoom(self) -> None:
        s = self.label.settings
        self.setFixedSize(
            int(s.width_mm * self.px_per_mm) + 2 * MARGIN,
            int(s.height_mm * self.px_per_mm) + 2 * MARGIN,
        )

    # ------------------------------------------------------------ conversions
    def _dot_scale(self) -> float:
        """Pixels écran par point imprimante."""
        return self.px_per_mm * 25.4 / self.label.settings.dpi

    def _label_rect(self) -> QRectF:
        s = self.label.settings
        return QRectF(MARGIN, MARGIN, s.width_mm * self.px_per_mm, s.height_mm * self.px_per_mm)

    def _element_rect(self, r: Rendered) -> QRectF:
        k = self._dot_scale()
        return QRectF(MARGIN + r.x * k, MARGIN + r.y * k, r.img.width * k, r.img.height * k)

    def _hit(self, pos: QPointF) -> int:
        for i in range(len(self._rendered) - 1, -1, -1):  # le dernier est au-dessus
            if self._element_rect(self._rendered[i]).adjusted(-3, -3, 3, 3).contains(pos):
                return i
        return -1

    # ------------------------------------------------------------ dessin
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#9aa0a6"))
        rect = self._label_rect()
        p.fillRect(rect.translated(3, 3), QColor(0, 0, 0, 60))  # ombre
        p.fillRect(rect, QColor("white"))
        if self._qimage is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._dot_scale() < 1.0)
            p.drawImage(rect, self._qimage)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.setPen(QPen(QColor("#5f6368"), 1))
        p.drawRect(rect)

        if 0 <= self.selected < len(self._rendered):
            r = self._rendered[self.selected]
            pen = QPen(QColor("#1a73e8"), 1.5, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawRect(self._element_rect(r).adjusted(-2, -2, 2, 2))
            if r.error:
                p.setPen(QColor("#d93025"))
                p.drawText(int(rect.left()), int(rect.bottom()) + 16, f"⚠ {r.error}")
        p.end()

    # ------------------------------------------------------------ souris / clavier
    def mousePressEvent(self, e: QMouseEvent) -> None:
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._hit(e.position())
        self.selected = idx
        self.selectionChanged.emit(idx)
        self._moved = False
        if idx >= 0:
            el = self.label.elements[idx]
            mx = (e.position().x() - MARGIN) / self.px_per_mm
            my = (e.position().y() - MARGIN) / self.px_per_mm
            self._drag_offset = (mx - el.x, my - el.y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self._drag_offset = None
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None and 0 <= self.selected < len(self.label.elements):
            el = self.label.elements[self.selected]
            mx = (e.position().x() - MARGIN) / self.px_per_mm
            my = (e.position().y() - MARGIN) / self.px_per_mm
            el.x = round(mx - self._drag_offset[0], 1)  # aimantation au 1/10 de mm
            el.y = round(my - self._drag_offset[1], 1)
            self._moved = True
            self.refresh()
            self.elementMoved.emit(self.selected)
        else:
            over = self._hit(e.position()) >= 0
            self.setCursor(Qt.CursorShape.OpenHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        self._drag_offset = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if self._moved:
            self._moved = False
            self.editFinished.emit()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key.Key_Delete and self.selected >= 0:
            self.deleteRequested.emit()
            return
        arrows = {
            Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
            Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1),
        }
        if e.key() in arrows and 0 <= self.selected < len(self.label.elements):
            step = 2.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.5
            dx, dy = arrows[e.key()]
            el = self.label.elements[self.selected]
            el.x, el.y = round(el.x + dx * step, 1), round(el.y + dy * step, 1)
            self.refresh()
            self.elementMoved.emit(self.selected)
            self.editFinished.emit()
            return
        super().keyPressEvent(e)

    def wheelEvent(self, e: QWheelEvent) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequested.emit(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
            e.accept()
        else:
            e.ignore()  # laisse la zone de défilement faire son travail
