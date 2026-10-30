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

MARGIN = 24  # px de respiration entre le bloc « règles + étiquette » et le bord du widget
RULER = 20  # px d'épaisseur des règles graduées (en haut et à gauche)
ORIGIN = MARGIN + RULER  # px entre le bord du widget et le coin de l'étiquette
# Graduations : suite 1-2-5, du demi-millimètre à 200 mm
RULER_STEPS = (0.5, 1, 2, 5, 10, 20, 50, 100, 200)
RULER_LABEL_PX = 26  # écart mini à l'écran entre deux graduations chiffrées
GUIDE_TOLERANCE_PX = 6  # distance d'accrochage des guides magnétiques, en pixels écran
GUIDE_MAX_MM = 2.0  # plafond de cette tolérance : dézoomé, l'accrochage serait trop collant
GUIDE_COLOR = "#e8710a"


def alignment_offset(edges: tuple[float, ...], targets: set[float],
                     tolerance: float) -> tuple[float, float] | None:
    """Décalage qui amène l'une des `edges` sur la ligne cible la plus proche.

    Renvoie `(décalage, cible)` pour le plus petit écart resté dans la tolérance,
    sinon `None`. Les centres passent par les mêmes calculs que les bords.
    """
    best: tuple[float, float] | None = None
    for edge in edges:
        for target in targets:
            delta = target - edge
            if abs(delta) <= tolerance and (best is None or abs(delta) < abs(best[0])):
                best = (delta, target)
    return best


def ruler_steps(px_per_mm: float) -> tuple[float, float]:
    """Pas des graduations chiffrées et des petits traits, adaptés à l'échelle.

    Le pas principal est la plus petite valeur de la suite 1-2-5 qui laisse assez
    de place à une étiquette chiffrée ; les traits secondaires descendent d'un cran
    tant qu'ils restent lisibles.
    """
    major = next((m for m in RULER_STEPS if m * px_per_mm >= RULER_LABEL_PX), RULER_STEPS[-1])
    minor = max((m for m in RULER_STEPS if m < major and m * px_per_mm >= 4), default=major)
    return float(major), float(minor)


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
        self.grid_mm = 5.0  # pas du quadrillage (mm) ; 0 = pas de pas défini
        self.grid_visible = False  # lignes dessinées par-dessus l'aperçu (jamais imprimées)
        self.snap = False  # déplacements arrondis au pas de la grille
        self._guides: tuple[float | None, float | None] = (None, None)  # guides magnétiques actifs (mm)
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

    def set_grid(self, step_mm: float, visible: bool, snap: bool) -> None:
        """Règle le quadrillage : pas en mm, affichage, et alignement des déplacements."""
        self.grid_mm = max(0.0, float(step_mm))
        self.grid_visible = bool(visible)
        self.snap = bool(snap)
        self.update()

    def snap_mm(self, value: float) -> float:
        """Valeur arrondie au pas de grille le plus proche (au dixième de mm sinon)."""
        if not self.snap or self.grid_mm <= 0:
            return round(value, 1)
        return round(round(value / self.grid_mm) * self.grid_mm, 2)

    def magnetize(self, x: float, y: float, index: int | None = None, active: bool = True) -> tuple[float, float]:
        """Position accrochée aux bords et centres des éléments voisins et de l'étiquette.

        Les lignes retenues sont mémorisées dans `_guides` pour être dessinées pendant
        le glisser. Chaque axe est traité séparément : un élément peut s'aligner
        verticalement sur l'un et horizontalement sur un autre.

        `active=False` (touche Alt pendant le glisser) rend la position inchangée et
        efface les guides : l'accrochage est suspendu le temps du geste.
        """
        index = self.selected if index is None else index
        self._guides = (None, None)
        if not active or not (0 <= index < len(self._rendered)):
            return x, y

        s = self.label.settings
        mm = 25.4 / s.dpi  # un point imprimante en millimètres
        rx = self._rendered[index]
        w, h = rx.img.width * mm, rx.img.height * mm
        xs, ys = {0.0, s.width_mm / 2, s.width_mm}, {0.0, s.height_mm / 2, s.height_mm}
        for i, other in enumerate(self._rendered):
            if i == index:
                continue
            ox, oy = other.x * mm, other.y * mm
            ow, oh = other.img.width * mm, other.img.height * mm
            xs.update((ox, ox + ow / 2, ox + ow))
            ys.update((oy, oy + oh / 2, oy + oh))

        tolerance = min(GUIDE_MAX_MM, GUIDE_TOLERANCE_PX / self.px_per_mm)
        hit_x = alignment_offset((x, x + w / 2, x + w), xs, tolerance)
        hit_y = alignment_offset((y, y + h / 2, y + h), ys, tolerance)
        if hit_x:
            x = round(x + hit_x[0], 2)
        if hit_y:
            y = round(y + hit_y[0], 2)
        self._guides = (hit_x[1] if hit_x else None, hit_y[1] if hit_y else None)
        return x, y

    def set_selected(self, index: int) -> None:
        if index != self.selected:
            self.selected = index
            self.update()

    def _resize_to_zoom(self) -> None:
        s = self.label.settings
        self.setFixedSize(
            int(s.width_mm * self.px_per_mm) + self.frame_px,
            int(s.height_mm * self.px_per_mm) + self.frame_px,
        )

    @property
    def frame_px(self) -> int:
        """Marge totale ajoutée autour de l'étiquette, règles comprises."""
        return ORIGIN + MARGIN

    # ------------------------------------------------------------ conversions
    def _dot_scale(self) -> float:
        """Pixels écran par point imprimante."""
        return self.px_per_mm * 25.4 / self.label.settings.dpi

    def _label_rect(self) -> QRectF:
        s = self.label.settings
        return QRectF(ORIGIN, ORIGIN, s.width_mm * self.px_per_mm, s.height_mm * self.px_per_mm)

    def _element_rect(self, r: Rendered) -> QRectF:
        k = self._dot_scale()
        return QRectF(ORIGIN + r.x * k, ORIGIN + r.y * k, r.img.width * k, r.img.height * k)

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
        self._paint_rulers(p, rect)
        p.fillRect(rect.translated(3, 3), QColor(0, 0, 0, 60))  # ombre
        p.fillRect(rect, QColor("white"))
        if self._qimage is not None:
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self._dot_scale() < 1.0)
            p.drawImage(rect, self._qimage)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self._paint_grid(p, rect)
        self._paint_guides(p, rect)
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

    def _paint_guides(self, p: QPainter, rect: QRectF) -> None:
        """Lignes d'alignement actives : bords ou centres sur lesquels l'élément se cale."""
        gx, gy = self._guides
        if gx is None and gy is None:
            return
        p.save()
        p.setClipRect(rect)
        p.setPen(QPen(QColor(GUIDE_COLOR), 1, Qt.PenStyle.DashLine))
        if gx is not None:
            x = rect.left() + gx * self.px_per_mm
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if gy is not None:
            y = rect.top() + gy * self.px_per_mm
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        p.restore()

    def _paint_rulers(self, p: QPainter, rect: QRectF) -> None:
        """Règles graduées en millimètres, en haut et à gauche de l'étiquette.

        Elles font partie du même widget que l'aperçu : elles restent donc toujours
        alignées sur l'origine de l'étiquette, quel que soit le zoom.
        """
        s = self.label.settings
        band = QRectF(rect.left() - RULER, rect.top() - RULER, rect.width() + RULER, RULER)
        side = QRectF(rect.left() - RULER, rect.top(), RULER, rect.height())
        major, minor = ruler_steps(self.px_per_mm)

        p.save()
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.fillRect(band, QColor("#f1f3f4"))
        p.fillRect(side, QColor("#f1f3f4"))
        p.fillRect(QRectF(band.left(), band.top(), RULER, RULER), QColor("#e8eaed"))
        p.setPen(QPen(QColor("#dadce0"), 1))
        p.drawLine(QPointF(band.left(), band.bottom()), QPointF(band.right(), band.bottom()))
        p.drawLine(QPointF(side.right(), side.top()), QPointF(side.right(), side.bottom()))

        font = p.font()
        font.setPointSizeF(7.0)
        p.setFont(font)
        fm = p.fontMetrics()
        p.setClipRect(band)
        p.setPen(QPen(QColor("#9aa0a6"), 1))
        steps = int((s.width_mm + 1e-9) / minor) + 1
        for i in range(steps):
            value = i * minor
            if value > s.width_mm + 1e-9:
                break
            x = round(rect.left() + value * self.px_per_mm)
            label = abs(value / major - round(value / major)) < 1e-6
            p.setPen(QPen(QColor("#5f6368" if label else "#9aa0a6"), 1))
            p.drawLine(QPointF(x, band.bottom()), QPointF(x, band.bottom() - (7 if label else 4)))
            if label:
                text = f"{value:g}"
                # Une graduation chiffrée coupée par le bord droit serait illisible : on l'omet
                if x + 3 + fm.horizontalAdvance(text) <= band.right() - 1:
                    p.drawText(QPointF(x + 3, band.top() + fm.ascent() + 2), text)

        p.setClipRect(side)
        steps = int((s.height_mm + 1e-9) / minor) + 1
        for i in range(steps):
            value = i * minor
            if value > s.height_mm + 1e-9:
                break
            y = round(rect.top() + value * self.px_per_mm)
            label = abs(value / major - round(value / major)) < 1e-6
            p.setPen(QPen(QColor("#5f6368" if label else "#9aa0a6"), 1))
            p.drawLine(QPointF(side.right(), y), QPointF(side.right() - (7 if label else 4), y))
            if label:
                text = f"{value:g}"
                width = fm.horizontalAdvance(text)
                if y - width / 2 > side.top() and y + width / 2 < side.bottom() - 1:
                    p.save()
                    p.translate(side.right() - 8, y)
                    p.rotate(-90)  # lu de bas en haut, comme sur une règle
                    p.drawText(QPointF(-width / 2, 0), text)
                    p.restore()

        font.setPointSizeF(6.0)
        p.setFont(font)
        p.setPen(QColor("#80868b"))
        p.setClipRect(QRectF(band.left(), band.top(), RULER, RULER))
        p.drawText(QRectF(band.left(), band.top(), RULER, RULER), Qt.AlignmentFlag.AlignCenter, "mm")
        p.restore()

    def _paint_grid(self, p: QPainter, rect: QRectF) -> None:
        """Quadrillage en millimètres : traits fins, trait plus marqué toutes les 5 mailles."""
        if not self.grid_visible or self.grid_mm <= 0:
            return
        step = self.grid_mm * self.px_per_mm
        if step < 3:  # trop dense pour être lisible à cette échelle : on n'encombre pas
            return
        s = self.label.settings
        p.save()
        p.setClipRect(rect)
        minor, major = QPen(QColor(26, 115, 232, 45), 1), QPen(QColor(26, 115, 232, 110), 1)
        cols, rows = int(s.width_mm / self.grid_mm), int(s.height_mm / self.grid_mm)
        for i in range(cols + 1):
            p.setPen(major if i % 5 == 0 else minor)
            x = rect.left() + i * self.grid_mm * self.px_per_mm
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for j in range(rows + 1):
            p.setPen(major if j % 5 == 0 else minor)
            y = rect.top() + j * self.grid_mm * self.px_per_mm
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        p.restore()

    # ------------------------------------------------------------ souris / clavier
    def mousePressEvent(self, e: QMouseEvent) -> None:
        self.setFocus()
        if e.button() != Qt.MouseButton.LeftButton:
            return
        idx = self._hit(e.position())
        self.selected = idx
        self._guides = (None, None)
        self.selectionChanged.emit(idx)
        self._moved = False
        if idx >= 0:
            el = self.label.elements[idx]
            mx = (e.position().x() - ORIGIN) / self.px_per_mm
            my = (e.position().y() - ORIGIN) / self.px_per_mm
            self._drag_offset = (mx - el.x, my - el.y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            self._drag_offset = None
        self.update()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None and 0 <= self.selected < len(self.label.elements):
            el = self.label.elements[self.selected]
            mx = (e.position().x() - ORIGIN) / self.px_per_mm
            my = (e.position().y() - ORIGIN) / self.px_per_mm
            x = self.snap_mm(mx - self._drag_offset[0])  # grille, sinon 1/10 de mm
            y = self.snap_mm(my - self._drag_offset[1])
            # Alt suspendu : le geste reste libre, sans accrochage aux voisins
            suspended = bool(e.modifiers() & Qt.KeyboardModifier.AltModifier)
            el.x, el.y = self.magnetize(x, y, active=not suspended)
            self._moved = True
            self.refresh()
            self.elementMoved.emit(self.selected)
        else:
            over = self._hit(e.position()) >= 0
            self.setCursor(Qt.CursorShape.OpenHandCursor if over else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, _e: QMouseEvent) -> None:
        self._drag_offset = None
        self._guides = (None, None)
        self.update()
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
            coarse = e.modifiers() & Qt.KeyboardModifier.ShiftModifier
            if self.snap and self.grid_mm > 0:
                # Alignement actif : le pas est celui de la grille, et Maj en fait cinq.
                step = self.grid_mm * (5 if coarse else 1)
            else:
                # Pas libre : 0,5 mm par flèche, 2 mm avec Maj (voir le README).
                step = 2.0 if coarse else 0.5
            dx, dy = arrows[e.key()]
            el = self.label.elements[self.selected]
            el.x, el.y = self.snap_mm(el.x + dx * step), self.snap_mm(el.y + dy * step)
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
