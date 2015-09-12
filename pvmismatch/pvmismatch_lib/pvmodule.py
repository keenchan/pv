# -*- coding: utf-8 -*-
"""
This module defines the :class:`~pvmismatch.pvmismatch_lib.pvmodule.PVmodule`.
"""

import numpy as np
from copy import deepcopy
from matplotlib import pyplot as plt
# use absolute imports instead of relative, so modules are portable
from pvmismatch.pvmismatch_lib.pvconstants import PVconstants, npinterpx, \
    MODSIZES, SUBSTRSIZES, NUMBERCELLS
from pvmismatch.pvmismatch_lib.pvcell import PVcell


def zip_flat_meshgrid(nrows, ncols):
    x, y = np.meshgrid(np.arange(nrows), np.arange(ncols))
    return zip(x.flat, y.flat)


def serpentine(nrows, ncols):
    x, y = np.meshgrid(np.arange(nrows), np.arange(ncols))
    x[1::2] = np.fliplr(x[1::2])  # flip alternate rows
    return zip(x.flat,y.flat)


# cell positions presets
STD96 = [
    {'row': r, 'col': c,
     'series': (n + 1 if n < 96 else None), 'parallel': None,
     'substring': (n + 24) / 48} for n, (r, c) in enumerate(serpentine(12, 8))
]
TCT96 = [
    {'row': r, 'col': c,
     'series': c*12+r+1 if r<11 else None, 'parallel': (c+1)*12+r if c<7 else None,
     'substring': r / 4} for r, c in zip_flat_meshgrid(12, 8)
]


class PVmodule(object):
    """
    PVmodule - A Class for PV modules.

    :param pvconst: An object with common parameters and constants.
    :type pvconst: :class:`PVconstants`
    :param numberCells: The number of cells in the module.
    :type numberCells: int
    :param subStrCells: A sequence of the number of cells in each
        substring. The length of the sequence is the number of substrings.
        The sum of the sequence must equal the number of cells in the
        module or else raises error.
    :param Ee: Effective irradiance in suns [1].
    :type Ee: float
    """
    def __init__(self, pvcells=None, cell_pos=STD96, Ee=1.,
                 pvconst=PVconstants(), numberCells=None, subStrCells=None):
        # Constructor
        self.numberCells = numberCells
        if pvcells is None:
            # use deep copy instead of making each object in a for-loop
            pvc = PVcell(pvconst=pvconst, Ee=Ee)
            self.pvcells = [deepcopy(pvc) for _ in xrange(self.numberCells)]
        if subStrCells:
            self.subStrCells = subStrCells  # sequence of cells per substring
        elif self.numberCells in MODSIZES:
            self.subStrCells = SUBSTRSIZES[MODSIZES.index(self.numberCells)]
        else:
            self.subStrCells = [self.numberCells]
        self.numSubStr = len(self.subStrCells)  # number of substrings
        if sum(self.subStrCells) != self.numberCells:
            raise Exception("Invalid cells per substring!")
        self.Ee = Ee
        # initialize members so PyLint doesn't get upset
        self.Voc = self.Vcell = self.Vmod = self.Vsubstr = 0
        self.Icell = self.Imod = 0
        self.Pcell = self.Pmod = 0
        self.pvconst = pvconst
        self.setSuns(Ee)

    def setSuns(self, Ee, cells=None):
        """
        Set the irradiance in suns, Ee, on the solar cells in the module.
        Recalculates cell current (Icell [A]), voltage (Vcell [V]) and power
        (Pcell [W]) as well as module current (Imod [A]), voltage (Vmod [V])
        and power (Pmod [W]).
        Arguments
            Ee : <float> or <np.array of floats> Effective Irradiance
        Optional
            cells : <np.array of int> Cells to change
        """
        if cells is None:
            if np.isscalar(Ee):
                self.Ee = np.ones((1, self.numberCells)) * Ee
            elif np.size(Ee) == self.numberCells:
                self.Ee = np.reshape(Ee, (1, self.numberCells))
            else:
                raise Exception("Input irradiance value (Ee) for each cell!")
        else:
            Nsuns = np.size(cells)
            if np.isscalar(Ee):
                self.Ee[0, cells] = np.ones(Nsuns) * Ee
            elif np.size(Ee) == Nsuns:
                self.Ee[0, cells] = Ee
            else:
                raise Exception("Input irradiance value (Ee) for each cell!")
        self.Voc = self.calcVoc()
        (self.Icell, self.Vcell, self.Pcell) = self.calcCell()
        (self.Imod, self.Vmod, self.Pmod, self.Vsubstr) = self.calcMod()

        # VPTS = VPTS.repeat(self.numberCells, axis=1)

    def calcMod(self):
        """
        Calculate module I-V curves.
        Returns (Imod, Vmod, Pmod) : tuple of numpy.ndarray of float
        """
        # create range for interpolation, it must include reverse bias
        # and some negative current to interpolate all cells
        # find Icell at Vrbd for all cells in module
        IatVrbd = [np.interp(self.pvconst.VRBD, Vcell, Icell) for
                   (Vcell, Icell) in zip(self.Vcell.T, self.Icell.T)]
        Isc = np.mean(self.Ee) * self.pvconst.Isc0
        # max current
        Imax = (np.max(IatVrbd) - Isc) * self.pvconst.Imod_pts + Isc
        Imin = np.min(self.Icell)
        Imin = Imin if Imin < 0 else 0
        Ineg = (Imin - Isc) * self.pvconst.Imod_negpts + Isc  # min current
        Imod = np.concatenate((Ineg, Imax), axis=0)  # interpolation range
        Vsubstr = np.zeros((2 * self.pvconst.npts, self.numSubStr))
        start = np.cumsum(self.subStrCells) - self.subStrCells
        stop = np.cumsum(self.subStrCells)
        for substr in range(self.numSubStr):
            for cell in range(start[substr], stop[substr]):
                xp = np.flipud(self.Icell[:, cell])
                fp = np.flipud(self.Vcell[:, cell])
                Vsubstr[:, substr] += npinterpx(Imod.flatten(), xp, fp)
        bypassed = Vsubstr < self.pvconst.Vbypass
        Vsubstr[bypassed] = self.pvconst.Vbypass
        Vmod = np.sum(Vsubstr, 1).reshape(2 * self.pvconst.npts, 1)
        Pmod = Imod * Vmod
        return (Imod, Vmod, Pmod, Vsubstr)

    def plotCell(self):
        """
        Plot cell I-V curves.
        Returns cellPlot : matplotlib.pyplot figure
        """
        cellPlot = plt.figure()
        plt.subplot(2, 2, 1)
        plt.plot(self.Vcell, self.Icell)
        plt.title('Cell Reverse I-V Characteristics')
        plt.ylabel('Cell Current, I [A]')
        plt.xlim(self.pvconst.VRBD - 1, 0)
        plt.ylim(0, self.pvconst.Isc0 + 10)
        plt.grid()
        plt.subplot(2, 2, 2)
        plt.plot(self.Vcell, self.Icell)
        plt.title('Cell Forward I-V Characteristics')
        plt.ylabel('Cell Current, I [A]')
        plt.xlim(0, np.max(self.Voc))
        plt.ylim(0, self.pvconst.Isc0 + 1)
        plt.grid()
        plt.subplot(2, 2, 3)
        plt.plot(self.Vcell, self.Pcell)
        plt.title('Cell Reverse P-V Characteristics')
        plt.xlabel('Cell Voltage, V [V]')
        plt.ylabel('Cell Power, P [W]')
        plt.xlim(self.pvconst.VRBD - 1, 0)
        plt.ylim((self.pvconst.Isc0 + 10) * (self.pvconst.VRBD - 1), -1)
        plt.grid()
        plt.subplot(2, 2, 4)
        plt.plot(self.Vcell, self.Pcell)
        plt.title('Cell Forward P-V Characteristics')
        plt.xlabel('Cell Voltage, V [V]')
        plt.ylabel('Cell Power, P [W]')
        plt.xlim(0, np.max(self.Voc))
        plt.ylim(0, (self.pvconst.Isc0 + 1) * np.max(self.Voc))
        plt.grid()
        return cellPlot

    def plotMod(self):
        """
        Plot module I-V curves.
        Returns modPlot : matplotlib.pyplot figure
        """
        modPlot = plt.figure()
        plt.subplot(2, 1, 1)
        plt.plot(self.Vmod, self.Imod)
        plt.title('Module I-V Characteristics')
        plt.ylabel('Module Current, I [A]')
        plt.ylim(ymax=self.pvconst.Isc0 + 1)
        plt.grid()
        plt.subplot(2, 1, 2)
        plt.plot(self.Vmod, self.Pmod)
        plt.title('Module P-V Characteristics')
        plt.xlabel('Module Voltage, V [V]')
        plt.ylabel('Module Power, P [W]')
        plt.grid()
        return modPlot
