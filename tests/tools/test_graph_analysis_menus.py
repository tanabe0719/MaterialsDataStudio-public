"""Scientific references, atomic previews and Igor-style menu workflows."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import Qt
from scipy import stats

from materials_data_studio.tools.command_engine import CommandEngine
from materials_data_studio.tools.command_services import DocumentServices
from materials_data_studio.tools.graph_analysis_forms import AnalysisDialog
from materials_data_studio.tools.graph_document import load_graph_document, save_graph_document
from materials_data_studio.tools.graph_fit import cursor_limits
from materials_data_studio.tools.graph_native_analysis_dialog import NativeAnalysisDialog
from materials_data_studio.tools.graph_signal_analysis import process
from materials_data_studio.tools.graph_statistics import statistics
from materials_data_studio.tools.graph_waves import empty_document, make_wave
from materials_data_studio.tools.graph_window import GraphEditorWindow


@pytest.fixture
def engine():
    engine = CommandEngine(DocumentServices(empty_document()))
    result = engine.execute("Make/D/N=21 x,y,err,b; x=p; y=2+3*p+0.2*sin(p); err=0.2; b=3+2*p+sin(p); Display/N=G y vs x; ErrorBars y Y,wave=(err,err); Cursor/P A,y,3; Cursor/P B,y,15")
    assert result.success, result.message
    return engine


@pytest.fixture
def viewer(qtbot, engine):
    w = GraphEditorWindow()
    w.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    w._discard_approved = True
    qtbot.addWidget(w)
    w.bind_graph_document(engine.services.document)
    yield w
    w._discard_approved = True
    w.close()


def test_menu_placement_and_quickfit_native_cursor_workflow(viewer, qtbot):
    titles = [a.text() for a in viewer.menuBar().actions()]
    assert titles[titles.index("Data"):titles.index("Data")+4] == ["Data", "Analysis", "Statistics", "Graph"]
    data = viewer.data_menu
    assert not any("Test" in a.text() or "Resample" in a.text() for a in data.actions())
    assert any(a.text() == "T-Test…" for a in viewer.statistics_menu.actions())
    document = viewer.editor.document
    assert cursor_limits(document, "G", "y") == (3, 15)
    before = document.current
    viewer.fit_weighted = False  # matches this native GUI session's unchecked option
    viewer.quick_fit("y", "line")
    result = next(iter(document.current.layout["G"]["fits"].values()))
    # Independently observed Igor 10.03 GUI Quick Fit: a=1.8872,b=3.0128, n=13.
    assert result["count"] == 13
    assert result["values"] == pytest.approx([1.8872, 3.0128], abs=5e-5)
    assert len(document.graph("G").traces) == 2
    assert "Quick Fit: line" in viewer.command_workspace.output.toPlainText()
    viewer.undo_action.trigger()
    assert document.current == before
    viewer.redo_action.trigger()
    assert len(document.graph("G").traces) == 2


@pytest.mark.parametrize("kind,method,reference", [
    ("variances", 0, lambda a,b: stats.bartlett(a,b)),
    ("variances", 1, lambda a,b: stats.levene(a,b,center="mean")),
    ("variances", 2, lambda a,b: stats.levene(a,b,center="median")),
    ("variances", 3, lambda a,b: stats.levene(a,b,center="trimmed",proportiontocut=.1)),
    ("anova", 0, lambda a,b: stats.f_oneway(a,b)),
    ("correlation", 0, lambda a,b: stats.pearsonr(a,b)),
    ("correlation", 1, lambda a,b: stats.spearmanr(a,b)),
    ("correlation", 2, lambda a,b: stats.kendalltau(a,b)),
    ("ks", 0, lambda a,b: stats.ks_2samp(a,b)),
    ("rank", 0, lambda a,b: stats.mannwhitneyu(a,b)),
    ("rank", 1, lambda a,b: stats.wilcoxon(a,b)),
    ("rank", 2, lambda a,b: stats.kruskal(a,b)),
])
def test_statistics_against_independent_scipy_reference(kind, method, reference):
    a = np.arange(1., 31)**1.1
    b = 2*np.arange(1., 31)+np.sin(np.arange(30))
    expected = reference(a,b)
    actual = statistics(kind, [a,b], method=method)
    assert actual["Statistic"] == pytest.approx(expected.statistic)
    assert actual["P"] == pytest.approx(expected.pvalue)


def test_welch_anova_matches_two_sample_t_square():
    a, b = np.array([1,3,4,8.,11]), np.array([2,6,7,8,9.,10])
    result = statistics("anova", [a,b], method=3)
    t = stats.ttest_ind(a,b,equal_var=False)
    assert result["Statistic"] == pytest.approx(t.statistic**2)
    assert result["P"] == pytest.approx(t.pvalue)


def test_f_test_tails_and_critical_values():
    a, b = np.array([1.,2,5,8]), np.array([1.,2,3,4])
    f = a.var(ddof=1)/b.var(ddof=1)
    for tail, expected in [(1,stats.f.sf(f,3,3)),(2,stats.f.cdf(f,3,3)),(4,2*stats.f.sf(f,3,3))]:
        result = statistics("ftest",[a,b],tail=tail)
        assert result["Statistic"] == pytest.approx(f)
        assert result["P"] == pytest.approx(expected)


def test_statistics_missing_pair_alignment_and_normality():
    a,b = np.array([1.,np.nan,3,5,8]),np.array([2.,9,np.nan,7,10])
    result=statistics("correlation",[a,b])
    assert result["Pairs"] == 3
    assert result["Statistic"] == pytest.approx(stats.pearsonr([1,5,8],[2,7,10]).statistic)
    x=np.linspace(-2,2,80)
    for method,ref in [(0,stats.jarque_bera),(1,stats.shapiro)]:
        result=statistics("normality",[x],method=method)
        assert result["P"] == pytest.approx(ref(x).pvalue)
    assert statistics("summary",[a])["Missing"] == 1


def test_counts_multiple_comparisons_bootstrap_and_circular():
    observed,expected=np.array([10.,20,30]),np.array([20.,20,20])
    assert statistics("chi",[observed,expected])["Statistic"] == 10
    matrix=np.array([[12,5],[3,10]])
    for method in range(3):
        result=statistics("contingency",[matrix],method=method)
        ref=stats.fisher_exact(matrix) if method==2 else stats.chi2_contingency(matrix,correction=method==1)
        assert result["P"] == pytest.approx(ref[1])
    assert np.isinf(statistics("contingency",[np.array([[2,0],[0,2]])],method=2)["Statistic"])
    with pytest.raises(ValueError):
        statistics("rank",[observed,expected],method=2,tail=1)
    groups=[np.arange(10.),np.arange(10.)+3,np.arange(10.)-2]
    actual=statistics("multiple",groups)
    assert actual["Group1_vs_2_P"] == pytest.approx(stats.tukey_hsd(*groups).pvalue[0,1])
    assert statistics("resample",[groups[0]],seed=19)==statistics("resample",[groups[0]],seed=19)
    uniform=statistics("circular",[np.linspace(0,2*np.pi,100,endpoint=False)])
    assert uniform["P"] == pytest.approx(1)


def test_two_way_anova_row_column_effects():
    data=np.array([[1.,2,4],[4,7,8],[8,8,11]])
    result=statistics("anova2",[data])
    # Hand calculation: error SS=8/9; row SS=602/9; column SS=152/9.
    grand=data.mean()
    error=np.sum((data-data.mean(1)[:,None]-data.mean(0)[None,:]+grand)**2)
    assert result["A_F"] == pytest.approx((3*np.sum((data.mean(1)-grand)**2)/2)/(error/4))
    replicated=np.stack([data,data+np.array([[.1,.3,.2],[.2,.1,.4],[.4,.2,.3]])],axis=2)
    assert "Interaction_P" in statistics("anova2",[replicated],method=1)


def test_native_igor_f_and_anova_references(engine):
    ref=Path(__file__).parents[1]/"reference_igor/v10_03_analysis"
    a,b=(engine.services.wave(n).data for n in ("y","b"))
    f=statistics("ftest",[a,b])
    native=np.loadtxt(ref/"ftest.tsv")
    assert [f["Statistic"],f["LowerCritical"],f["UpperCritical"],f["P"]]==pytest.approx(native[8:12],rel=2e-10)
    for method,name in [(0,"anova"),(1,"welch"),(2,"brown_forsythe")]:
        report=statistics("anova",[a,b],method=method)
        if method==0:
            native=np.genfromtxt(ref/(name+".tsv"),delimiter="\t")
            expected=native[-1,3:6]
        else:
            native=np.loadtxt(ref/(name+".tsv"))
            expected=native[2:5]
            assert report["DF2"]==native[1]
        assert [report["Statistic"],report["Critical"],report["P"]]==pytest.approx(expected,rel=2e-10)


def test_advanced_fit_mask_initial_values_holds_and_curve_range():
    from materials_data_studio.tools.graph_fit import quick_fit
    x=np.arange(20.)
    y=2+3*x
    y[15]=2000
    selected=np.ones(20)
    selected[15]=0
    result=quick_fit(x,y,"line",3,12,initial_values=[2,1],held=(0,),selected=selected,curve_range=(0,19))
    assert result.values==pytest.approx([2,3]) and result.errors[0]==0
    assert result.count==10 and result.x[[0,-1]].tolist()==[0,19]
    for options in [{"held":(9,)},{"initial_values":[1]},{"selected":[1,1]},{"curve_range":(2,1)}]:
        with pytest.raises(ValueError):
            quick_fit(x,y,"line",**options)


def test_spectral_methods_and_surface_fits():
    from materials_data_studio.tools.graph_surface_fit import surface_fit
    sample=np.arange(128.)/128
    values=np.sin(2*np.pi*12*sample)
    wave=replace(make_wave("signal",values),dimension_scaling=((0,1/128),),dimension_units=("s",))
    for key in ("periodogram","multitaper","lomb"):
        inputs=[wave,make_wave("time",sample)] if key=="lomb" else [wave]
        output=process(key,inputs,"result",count=128)
        peak=output.coordinate(0)[np.argmax(output.data)]
        assert peak==pytest.approx(12,abs=.5)
    dwt=process("dwt",[wave],"result")
    assert np.dot(dwt.data,dwt.data)==pytest.approx(np.dot(values,values))
    for key in ("stft","cwt","wigner"):
        output=process(key,[wave],"result",count=16)
        assert output.data.ndim==2 and np.isfinite(output.data).all()
        assert output.dimension_units[0]=="s"
    x,y=np.meshgrid(np.linspace(-3,3,21),np.linspace(-2,2,17),indexing="ij")
    metadata=(((-3,.3),(-2,.25)),("mm","mm"))
    plane=replace(make_wave("plane",2+3*x-4*y+x*y),dimension_scaling=metadata[0],dimension_units=metadata[1])
    fitted,residual,_=surface_fit(plane,"poly")
    np.testing.assert_allclose(fitted.data,plane.data,atol=1e-12)
    assert abs(residual.data).max()<1e-12
    image=replace(plane,data=1+5*np.exp(-((x-.4)/1.2)**2-((y+.2)/.6)**2))
    fitted,residual,coef=surface_fit(image,"gauss",gaussian=True)
    assert abs(residual.data).max()<1e-7 and coef.data.shape==(7,)


def test_surface_command_is_atomic_and_retains_labels(engine):
    assert engine.execute('Make/D/N=(12,12) im; im=1+2*p+3*q; MDSProcess/KIND=poly2d/METH=0/DEST=fitted im').success
    assert engine.services.wave("fitted").shape==(12,12)
    assert engine.services.wave("fitted_coef").dimension_labels[0]==("x0_y0","x1_y0","x0_y1")


def test_statistics_transaction_preview_persistence_and_protection(engine,tmp_path,qapp):
    document=engine.services.document
    before=document.current
    dialog=AnalysisDialog(None,document,"ftest")
    dialog.waves.item(1).setSelected(True)
    dialog.waves.item(3).setSelected(True)
    dialog.preview_result()
    assert document.current is before
    assert "Statistic" in dialog.preview.toPlainText()
    result=engine.execute(dialog.build_command())
    assert result.success,result.message
    wave=engine.services.wave("W_ftest")
    assert "P" in wave.dimension_labels[0] and "inputs" in wave.wave_note
    save_graph_document(tmp_path/"analysis.mdsg",document)
    restored=load_graph_document(tmp_path/"analysis.mdsg")
    np.testing.assert_array_equal(restored.render_source().wave("W_ftest").data,wave.data)
    before=document.current
    for command in ['MDSStatistics/KIND=ftest/DEST=y y,b','MDSStatistics/KIND=anova/METH=99 y,b','MDSProcess/KIND=interpolate/N=-1 y']:
        assert not engine.execute(command).success
        assert document.current is before
    dialog.close()


def test_pointwise_average_missing_values_and_scaling(engine):
    result=engine.execute("MDSStatistics/KIND=average/DEST=avg/T y,b")
    assert result.success,result.message
    a,b=(engine.services.wave(n).data for n in ("y","b"))
    np.testing.assert_allclose(engine.services.wave("avg").data,(a+b)/2)
    np.testing.assert_allclose(engine.services.wave("avg_sd").data,abs(a-b)/np.sqrt(2))


def test_analysis_commands_in_isolated_python_worker(engine,tmp_path):
    from materials_data_studio.tools.procedure_sandbox import launch
    (tmp_path/"input").mkdir()
    (tmp_path/"output").mkdir()
    save_graph_document(tmp_path/"input/input.mdsg",engine.services.document)
    (tmp_path/"input/procedure.py").write_text('from mds import command\ncommand("MDSStatistics/KIND=ftest/DEST=f y,b;MDSStatistics/KIND=anova/METH=1/DEST=welch y,b;MDSProcess/KIND=periodogram/DEST=psd y")\n',encoding="utf-8")
    assert launch(tmp_path)==0,(tmp_path/"output/procedure.log").read_text(encoding="utf-8")
    output=load_graph_document(tmp_path/"output/output.mdsg").render_source()
    assert output.wave("f") is not None and output.wave("psd").data.ndim==1
    assert "DF2" in output.wave("welch").dimension_labels[0]


def test_processing_convolution_correlation_interpolation_filter_and_fft():
    from materials_data_studio.tools.igor_transforms import forward_fft
    a,b=make_wave("kernel",[1.,2,1]),make_wave("signal",[1.,3,5,2])
    np.testing.assert_allclose(process("convolve",[a,b],"out").data,np.convolve(b.data,a.data),atol=1e-12)
    np.testing.assert_allclose(process("convolve",[a,b],"out",method=1).data,np.convolve(b.data,a.data)[1:5],atol=1e-12)
    corr=process("correlate",[a,b],"out")
    np.testing.assert_allclose(corr.data,np.correlate(b.data,a.data,"full"),atol=1e-12)
    assert corr.dimension_scaling==((-2,1),)
    auto=process("correlate",[b,b],"out",method=2)
    assert auto.data[len(b.data)-1]==pytest.approx(1)
    interpolated=process("interpolate",[b],"out",count=7)
    np.testing.assert_allclose(interpolated.data,np.interp(np.linspace(0,3,7),np.arange(4),b.data))
    filtered=process("filter",[b,make_wave("coef",[.5,.5])],"out")
    np.testing.assert_allclose(filtered.data,[.5,2,4,3.5])
    spectrum,start,delta=forward_fft(b.data,.2)
    spec=replace(make_wave("spectrum",spectrum),dimension_scaling=((start,delta),))
    inverse=process("ifft",[spec],"out",method=2)
    np.testing.assert_allclose(inverse.data,b.data,atol=1e-12)
    assert inverse.dimension_scaling[0]==pytest.approx((0,.2))


def test_additional_processing_modes_and_power():
    from scipy.signal import lfilter

    from materials_data_studio.tools.graph_anova_power import anova_power
    assert anova_power(2,30,0)==pytest.approx(.05)
    assert anova_power(2,30,10)>anova_power(2,30,2)
    with pytest.raises(ValueError):
        anova_power(0,30,2)
    a,b=make_wave("a",[1.,2,1,0]),make_wave("b",[2.,3,5,7])
    circular=process("convolve",[a,b],"result",method=2).data
    expected=np.array([sum(b.data[m]*a.data[(p-m)%4] for m in range(4)) for p in range(4)])
    np.testing.assert_allclose(circular,expected)
    numerator,denominator=make_wave("num",[1.,.5]),make_wave("den",[1.,-.2])
    np.testing.assert_allclose(process("filter",[b,numerator,denominator],"result",method=1).data,lfilter(numerator.data,denominator.data,b.data))
    for method in (1,2):
        result=process("interpolate",[b],"result",method=method,count=7)
        np.testing.assert_allclose(result.data[::2],b.data)


@pytest.mark.parametrize("kind,arrays,kwargs", [
    ("ftest",[[1,1],[2,2]],{}), ("anova",[[1,1],[2,3]],{}),
    ("correlation",[[1,2,3],[1,2]],{}),("chi",[[1,2],[1,3]],{}),
    ("contingency",[[[1,-1],[2,3]]],{}),("resample",[[1,2]],{"repeats":0}),
    ("anova2",[[[1,np.nan],[3,4]]],{}),("normality",[[1,1,1]],{}),
])
def test_invalid_statistics_are_diagnosed(kind,arrays,kwargs):
    with pytest.raises(ValueError):
        statistics(kind,arrays,**kwargs)


@pytest.mark.parametrize("operation",["Differentiate","Integrate","Smooth","Sort","Histogram","FindLevel","FFT"])
def test_native_analysis_forms_share_command_engine(qapp,engine,operation):
    dialog=NativeAnalysisDialog(None,engine.services.document,operation)
    # FFT's native subset requires even length.
    if operation=="FFT":
        assert engine.execute("Make/D/N=20 even;even=sin(p)").success
        dialog.close()
        dialog=NativeAnalysisDialog(None,engine.services.document,operation)
        dialog.source.setCurrentIndex(dialog.source.findData("root:even"))
    result=engine.execute(dialog.build_command())
    assert result.success,result.message
    dialog.close()
