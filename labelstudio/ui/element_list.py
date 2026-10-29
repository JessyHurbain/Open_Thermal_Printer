"""Liste des éléments : deux lignes et une icône de type agrandie.

Le texte de l'élément (rôle `DisplayRole`) sert de titre ; l'icône et la ligne
secondaire passent par des rôles dédiés, renseignés par la fenêtre principale.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

ICON_ROLE = Qt.ItemDataRole.UserRole + 1
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 2

ICON_WIDTH = 32  # place réservée à la miniature
ROW_HEIGHT = 46  # deux lignes de texte + un peu d'air
THUMB_SIZE = 28  # côté de la miniature du rendu


class ElementItemDelegate(QStyledItemDelegate):
    """Une icône à gauche, le nom sur la première ligne, le détail en dessous."""

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(super().sizeHint(option, index).width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title = opt.text
        opt.text = ""  # le fond et la sélection sont dessinés par le style, pas le texte
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        main = opt.palette.highlightedText().color() if selected else opt.palette.text().color()
        dim = main if selected else QColor("#5f6368")
        base = opt.font.pointSizeF() if opt.font.pointSizeF() > 0 else 9.5

        painter.save()
        painter.setClipRect(opt.rect.adjusted(2, 0, -2, 0))
        left = opt.rect.left() + 6
        thumb = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(thumb, QIcon) and not thumb.isNull():
            box = QRect(left + (ICON_WIDTH - THUMB_SIZE) // 2,
                        opt.rect.top() + (opt.rect.height() - THUMB_SIZE) // 2,
                        THUMB_SIZE, THUMB_SIZE)
            painter.setPen(QPen(QColor("#dadce0"), 1))  # contour : lisible sur fond blanc
            painter.drawRect(box)
            thumb.paint(painter, box.adjusted(1, 1, 0, 0))
        else:
            glyph = index.data(ICON_ROLE) or ""
            font = QFont(opt.font)
            font.setPointSizeF(base + 5)
            painter.setFont(font)
            painter.setPen(main)
            painter.drawText(QRect(left, opt.rect.top(), ICON_WIDTH, opt.rect.height()),
                             Qt.AlignmentFlag.AlignCenter, glyph)
        left += ICON_WIDTH + 4

        width = opt.rect.right() - left - 6
        painter.setFont(opt.font)
        painter.setPen(main)
        painter.drawText(QRect(left, opt.rect.top() + 5, width, 20),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(opt.font).elidedText(title, Qt.TextElideMode.ElideRight, width))

        small = QFont(opt.font)
        small.setPointSizeF(max(6.0, base - 1.5))
        painter.setFont(small)
        painter.setPen(dim)
        detail = index.data(DETAIL_ROLE) or ""
        painter.drawText(QRect(left, opt.rect.top() + 23, width, 17),
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(small).elidedText(detail, Qt.TextElideMode.ElideRight, width))
        painter.restore()
