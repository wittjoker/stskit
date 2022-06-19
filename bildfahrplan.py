import math
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, Generator, Iterable, List, Mapping, Optional, Set, Tuple, Union

import matplotlib as mpl
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import numpy as np
from PyQt5 import Qt, QtCore, QtGui, QtWidgets

from auswertung import Auswertung
from anlage import Anlage
from planung import Planung, ZugDetailsPlanung, ZugZielPlanung
from slotgrafik import hour_minutes_formatter, ZugFarbschema
from stsplugin import PluginClient
from stsobj import FahrplanZeile, ZugDetails, time_to_minutes, format_verspaetung

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

mpl.use('Qt5Agg')


def format_label(plan1: ZugZielPlanung, plan2: ZugZielPlanung):
    """
    zuglabel formatieren mit verspätungsangabe

    das label besteht aus zugname und verspätungsangabe (falls nicht null).
    die verspätungsangabe besteht aus einem teil wenn sie am anfang und ende der linie gleich ist,
    sonst aus der verspätung am anfang und ende.

    :param plan1: anfangspunkt der linie. zug.name und verspaetung_ab werden benutzt.
    :param plan2: endpunkt der linie. verspaetung_an wird benutzt.
    :return: (str)
    """
    v1 = plan1.verspaetung_ab
    v2 = plan2.verspaetung_an
    name = plan1.zug.name

    if v1 == v2:
        if v1 == 0:
            v = ""
        else:
            v = format_verspaetung(v1)
    else:
        v = "|".join((format_verspaetung(v1), format_verspaetung(v2)))

    if v:
        return f"{name} ({v})"
    else:
        return f"{name}"


@dataclass(init=False)
class Trasse:
    zug: ZugDetails
    start: ZugZielPlanung
    ziel: ZugZielPlanung
    koord: List[Tuple[float]]
    halt: bool = False
    color: str = "b"
    fontstyle: str = "normal"
    linestyle: str = "-"
    linewidth: int = 1
    marker: str = "."

    def plot_args(self):
        args = {'color': self.color,
                'linewidth': self.linewidth,
                'linestyle': self.linestyle,
                'marker': self.marker}
        try:
            args['markevery'] = [not self.start.durchfahrt(), not self.ziel.durchfahrt()]
        except AttributeError:
            args['marker'] = ""

        return args


class BildFahrplanWindow(QtWidgets.QMainWindow):

    def __init__(self):
        super().__init__()
        self.client: Optional[PluginClient] = None
        self.anlage: Optional[Anlage] = None
        self.planung: Optional[Planung] = None
        self.auswertung: Optional[Auswertung] = None

        self.setWindowTitle("bildfahrplan")
        ss = f"background-color: {mpl.rcParams['axes.facecolor']};"
        self.setStyleSheet(ss)
        self._main = QtWidgets.QWidget()
        self.setCentralWidget(self._main)
        layout = QtWidgets.QVBoxLayout(self._main)

        canvas = FigureCanvas(Figure(figsize=(5, 3)))
        layout.addWidget(canvas)
        self._axes = canvas.figure.subplots()

        self._strecken_name: str = ""
        # bahnhofname -> distanz [minuten]
        self._strecke: Dict[str, float] = {}
        self._zug_trassen: Dict[int, List[Trasse]] = {}

        self.zeitfenster_voraus = 55
        self.zeitfenster_zurueck = 5
        self.farbschema = ZugFarbschema()
        self.farbschema.init_schweiz()

    def set_strecke(self, streckenname: str):
        if streckenname != self._strecken_name:
            self._strecken_name = streckenname
            self._strecke = {}

    def update(self):
        if not self._strecken_name:
            k0 = None
            v0 = []
            for k, v in self.anlage.strecken.items():
                if len(v) > len(v0):
                    k0 = k
                    v0 = v
            if k0:
                self.set_strecke(k0)

        if self._strecken_name:
            self.daten_update()
            self.grafik_update()

    def daten_update(self):
        self.update_strecke()
        for zug in self.planung.zugliste.values():
            self.update_zuglauf(zug)

    def update_strecke(self):
        sd = self.anlage.get_strecken_distanzen(self._strecken_name)
        for k, v in sd.items():
            sd[k] = v / 60
        self._strecke = sd

    def update_zuglauf(self, zug: ZugDetailsPlanung):
        color = self.farbschema.zugfarbe(zug)
        zuglauf = []
        plan1 = zug.fahrplan[0]

        for plan2 in zug.fahrplan[1:]:
            trasse = Trasse()
            trasse.zug = zug
            trasse.color = color
            trasse.start = plan1
            trasse.ziel = plan2

            try:
                gruppe1 = self.anlage.gleiszuordnung[plan1.gleis]
                gruppe2 = self.anlage.gleiszuordnung[plan2.gleis]
            except KeyError:
                logger.warning(f"zug {zug.name}, gleis {plan1.gleis} oder {plan2.gleis} "
                               f"kann keinem bahnhof zugeordnet werden.")
            else:
                if gruppe1 in self._strecke and gruppe2 in self._strecke:
                    trasse.koord = [(self._strecke[gruppe1], time_to_minutes(plan1.ab) + plan1.verspaetung_ab),
                                    (self._strecke[gruppe2], time_to_minutes(plan2.an) + plan2.verspaetung_an)]
                    zuglauf.append(trasse)

                    # haltelinie
                    an = time_to_minutes(plan2.an) + plan2.verspaetung_an
                    ab = time_to_minutes(plan2.ab) + plan2.verspaetung_ab
                    if ab > an:
                        trasse = Trasse()
                        trasse.zug = zug
                        trasse.color = color
                        trasse.start = plan2
                        trasse.ziel = plan2
                        trasse.halt = True
                        trasse.linestyle = '--'
                        trasse.koord = [(self._strecke[gruppe2], an), (self._strecke[gruppe2], ab)]
                        zuglauf.append(trasse)

            plan1 = plan2

        if zuglauf:
            self._zug_trassen[zug.zid] = zuglauf
        else:
            try:
                del self._zug_trassen[zug.zid]
            except (AttributeError, KeyError):
                pass

    def grafik_update(self):
        self._axes.clear()

        x_labels = list(self._strecke.keys())
        x_labels_pos = list(self._strecke.values())

        self._axes.set_xticks(x_labels_pos, x_labels, rotation=45, horizontalalignment='right')
        self._axes.yaxis.set_major_formatter(hour_minutes_formatter)
        self._axes.yaxis.set_minor_locator(mpl.ticker.MultipleLocator(1))
        self._axes.yaxis.set_major_locator(mpl.ticker.MultipleLocator(5))
        self._axes.yaxis.grid(True, which='major')
        self._axes.xaxis.grid(True)

        zeit = time_to_minutes(self.client.calc_simzeit())
        ylim = (zeit - self.zeitfenster_zurueck, zeit + self.zeitfenster_voraus)
        self._axes.set_ylim(top=ylim[0], bottom=ylim[1])
        self._axes.set_xlim(left=x_labels_pos[0], right=x_labels_pos[-1])

        wid_x = x_labels_pos[-1] - x_labels_pos[0]
        wid_y = self.zeitfenster_zurueck + self.zeitfenster_voraus
        off_x = 0
        off = self._axes.transData.inverted().transform([(0, 0), (0, -5)])
        off_y = (off[1] - off[0])[1]

        label_args = {'ha': 'center',
                      'va': 'center',
                      'fontsize': 'small',
                      'fontstretch': 'condensed',
                      'rotation_mode': 'anchor',
                      'transform_rotates_text': True}

        for zuglauf in self._zug_trassen.values():
            for trasse in zuglauf:
                pos_x = [pos[0] for pos in trasse.koord]
                pos_y = [pos[1] for pos in trasse.koord]
                trasse.mpl_line = self._axes.plot(pos_x, pos_y, **trasse.plot_args())
                seg = trasse.koord
                pix = self._axes.transData.transform(seg)
                cx = (seg[0][0] + seg[1][0]) / 2 + off_x
                cy = (seg[0][1] + seg[1][1]) / 2 + off_y
                dx = (seg[1][0] - seg[0][0])
                dy = (seg[1][1] - seg[0][1])
                if ylim[0] < cy < ylim[1] and abs(pix[1][0] - pix[0][0]) > 20:
                    ang = math.degrees(math.atan(dy / dx))
                    titel = format_label(trasse.start, trasse.ziel)
                    trasse.mpl_label = self._axes.text(cx, cy, titel, rotation=ang, **label_args)

        for item in (self._axes.get_xticklabels() + self._axes.get_yticklabels()):
            item.set_fontsize('small')

        if self.zeitfenster_zurueck > 0:
            self._axes.axhline(y=zeit, color=mpl.rcParams['axes.edgecolor'], linewidth=mpl.rcParams['axes.linewidth'])

        self._axes.figure.tight_layout()
        self._axes.figure.canvas.draw()
