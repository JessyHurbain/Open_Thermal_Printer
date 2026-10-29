"""Liste des éléments : deux lignes et une icône de type agrandie.

Le texte de l'élément (rôle `DisplayRole`) sert de titre ; l'icône et la ligne
secondaire passent par des rôles dédiés, renseignés par la fenêtre principale.
"""

from __future__ import annotations

from PyQt6.QtCore import QModelIndex, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

ICON_ROLE = Qt.ItemDataRole.UserRole + 1
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 2

ICON_WIDTH = 26  # place réservée à l'icône
ROW_HEIGHT = 46  # deux lignes de texte + un peu d'air


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
        icon = index.data(ICON_ROLE) or ""
        if icon:
            font = QFont(opt.font)
            font.setPointSizeF(base + 5)
            painter.setFont(font)
            painter.setPen(main)
            painter.drawText(QRect(left, opt.rect.top(), ICON_WIDTH, opt.rect.height()),
                             Qt.AlignmentFlag.AlignCenter, icon)
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
