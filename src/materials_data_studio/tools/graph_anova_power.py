"""ANOVA noncentral-F power calculator; no sample data or document mutations."""
from __future__ import annotations

import numpy as np
from scipy.stats import f, ncf


def anova_power(df1: float, df2: float, noncentrality: float, alpha: float = .05) -> float:
    if not np.isfinite([df1,df2,noncentrality,alpha]).all() or df1 <= 0 or df2 <= 0 or noncentrality < 0 or not 0 < alpha < 1:
        raise ValueError("Positive degrees of freedom, nonnegative noncentrality and 0<alpha<1 are required")
    critical = f.isf(alpha,df1,df2)
    return float(f.sf(critical,df1,df2) if noncentrality == 0 else ncf.sf(critical,df1,df2,noncentrality))


def show_power(parent) -> None:
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel
    dialog = QDialog(parent)
    dialog.setWindowTitle("ANOVA Power")
    form = QFormLayout(dialog)
    controls=[]
    for label,value,low,high in [("Numerator DF",2.,.001,1e6),("Denominator DF",30.,.001,1e6),("Noncentrality",5.,0.,1e6),("Alpha",.05,.000001,.999999)]:
        spin=QDoubleSpinBox()
        spin.setDecimals(6)
        spin.setRange(low,high)
        spin.setValue(value)
        form.addRow(label,spin)
        controls.append(spin)
    result=QLabel()
    form.addRow("Power",result)
    def update() -> None:
        try:
            result.setText(f"{anova_power(*(spin.value() for spin in controls)):.9g}")
        except ValueError as exc:
            result.setText(str(exc))
    for spin in controls:
        spin.valueChanged.connect(update)
    update()
    buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    dialog.exec()
