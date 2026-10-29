"""Scientific Python surface. Defense in depth; the OS token is the boundary."""
from __future__ import annotations

import ast
import builtins
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

NUMPY = ["array", "asarray", "arange", "linspace", "logspace", "geomspace", "zeros", "ones", "full", "empty", "zeros_like", "ones_like", "full_like", "eye", "identity", "concatenate", "stack", "vstack", "hstack", "column_stack", "dstack", "reshape", "ravel", "transpose", "squeeze", "expand_dims", "tile", "repeat", "take", "where", "select", "clip", "diff", "gradient", "interp", "trapz", "trapezoid", "cumsum", "cumprod", "sum", "prod", "mean", "median", "std", "var", "min", "max", "amin", "amax", "argmin", "argmax", "nanmean", "nanmedian", "nanstd", "nanvar", "nanmin", "nanmax", "nansum", "nanargmin", "nanargmax", "isfinite", "isnan", "isinf", "allclose", "isclose", "all", "any", "count_nonzero", "nonzero", "unique", "sort", "argsort", "searchsorted", "digitize", "histogram", "histogram2d", "histogramdd", "percentile", "quantile", "nanpercentile", "nanquantile", "sin", "cos", "tan", "arcsin", "arccos", "arctan", "arctan2", "sinh", "cosh", "tanh", "exp", "expm1", "log", "log10", "log2", "log1p", "sqrt", "cbrt", "square", "power", "absolute", "abs", "sign", "floor", "ceil", "round", "real", "imag", "conj", "conjugate", "angle", "degrees", "radians", "deg2rad", "rad2deg", "sinc", "maximum", "minimum", "fmax", "fmin", "dot", "vdot", "inner", "outer", "cross", "matmul", "einsum", "polyfit", "polyval", "meshgrid", "nan", "inf", "pi", "e", "newaxis", "float64", "float32", "int64", "int32", "complex128", "complex64", "bool_", "dtype", "ndarray"]
SCIPY = {
    "optimize": "curve_fit least_squares minimize minimize_scalar root root_scalar brentq bisect newton nnls",
    "integrate": "trapezoid cumulative_trapezoid simpson cumulative_simpson quad quad_vec romb solve_ivp",
    "interpolate": "interp1d CubicSpline PchipInterpolator Akima1DInterpolator UnivariateSpline RegularGridInterpolator griddata interpn make_interp_spline",
    "signal": "savgol_filter find_peaks peak_widths peak_prominences detrend convolve correlate fftconvolve medfilt welch periodogram butter sosfilt sosfiltfilt filtfilt",
    "stats": "linregress pearsonr spearmanr describe sem zscore ttest_ind ttest_rel norm t chi2",
    "linalg": "solve lstsq inv pinv eig eigh eigvals svd det norm expm",
    "fft": "fft ifft rfft irfft fft2 ifft2 fftfreq rfftfreq fftshift ifftshift",
    "special": "erf erfc gamma gammaln beta expit logit voigt_profile jv iv kv",
}
FORBIDDEN = {"open", "exec", "eval", "compile", "getattr", "setattr", "delattr", "vars", "globals", "locals", "input", "breakpoint", "help", "dir", "exit", "quit", "services", "ctypes", "data", "base", "loader", "load_module", "load", "save", "savetxt", "savez", "savez_compressed", "fromfile", "tofile", "memmap", "dump", "dumps", "setflags"}


def validate(source: str) -> ast.Module:
    tree = ast.parse(source, filename="MDS isolated Python Procedure")
    if sum(1 for _ in ast.walk(tree)) > 20000:
        raise ValueError("Procedure exceeds the syntax size limit")
    modules = {"numpy", "math", "statistics", "mds", "scipy", *("scipy." + name for name in SCIPY)}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await)):
            raise ValueError("Classes/async execution are not part of the scientific Procedure API")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in FORBIDDEN):
            raise ValueError(f"Name is not allowed in isolated procedures: {node.id}")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in FORBIDDEN or node.attr == "ctypeslib"):
            raise ValueError(f"Attribute is not allowed in isolated procedures: {node.attr}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module]
            if isinstance(node, ast.ImportFrom) and (node.level or any(a.name == "*" for a in node.names)):
                raise ValueError("Relative/wildcard imports are not allowed")
            if any(name not in modules for name in names):
                raise ValueError("Only numpy, selected scipy modules, math, statistics and mds may be imported")
    return tree


def namespace(mds: Any) -> dict[str, Any]:
    import math
    import statistics

    import numpy as np

    # ndarray's first str/repr initializes a private NumPy formatter using the
    # calling frame's importer. Initialize it here, not from user builtins; do
    # not add a private-import exception to guarded_import.
    str(np.array([0.0]))
    repr(np.array([0.0]))

    # Preload trusted dependency initialization before restricting file/network
    # operations during user execution. Users receive facades, not raw modules.
    from matplotlib.colors import to_rgb  # noqa: F401
    modules: dict[str, Any] = {"numpy": SimpleNamespace(**{n: getattr(np, n) for n in NUMPY if hasattr(np, n)}), "math": SimpleNamespace(**{n: getattr(math, n) for n in dir(math) if not n.startswith("_")}), "statistics": SimpleNamespace(**{n: getattr(statistics, n) for n in statistics.__all__ if n not in {"NormalDist"}}), "mds": mds}
    scipy = SimpleNamespace()
    for key, names in SCIPY.items():
        module = importlib.import_module("scipy." + key)
        facade = SimpleNamespace(**{n: getattr(module, n) for n in names.split() if hasattr(module, n)})
        setattr(scipy, key, facade)
        modules["scipy." + key] = facade
    modules["numpy"].linalg = scipy.linalg
    modules["numpy"].fft = scipy.fft
    modules["scipy"] = scipy

    def guarded_import(name: str, _globals=None, _locals=None, fromlist=(), level=0):
        if level or name not in modules:
            raise ImportError("Module is not available in the isolated Procedure environment: " + name)
        return modules[name] if fromlist or "." not in name else modules[name.split(".")[0]]

    names = ["abs", "all", "any", "bool", "complex", "dict", "enumerate", "filter", "float", "int", "isinstance", "iter", "len", "list", "map", "max", "min", "next", "pow", "print", "range", "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip", "Exception", "ValueError", "TypeError", "RuntimeError", "ZeroDivisionError", "AssertionError", "StopIteration", "ArithmeticError", "PermissionError"]
    safe = {n: getattr(builtins, n) for n in names}
    safe["__import__"] = guarded_import
    return {"__builtins__": safe, "__name__": "__mds_isolated_procedure__"}


class ExecutionGuard:
    def __init__(self) -> None:
        self.active = False
        self.runtime = Path(sys.executable).parent.resolve()
        sys.addaudithook(self.audit)

    def audit(self, event: str, arguments: tuple[Any, ...]) -> None:
        if not self.active:
            return
        if event.startswith(("socket.", "subprocess.", "ctypes.", "winreg.", "os.spawn", "os.exec")) or event in {"os.system", "os.startfile", "os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.chmod", "os.link", "os.symlink", "os.putenv"}:
            raise PermissionError("External communication/process/filesystem operation is forbidden: " + event)
        if event == "open":
            path, mode, flags = arguments
            write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (1 | 2 | 64 | 512 | 1024))
            if write or not isinstance(path, str) or not Path(path).resolve().is_relative_to(self.runtime):
                raise PermissionError("Procedure file access is forbidden")
        if event in {"os.listdir", "os.scandir"} and not Path(arguments[0]).resolve().is_relative_to(self.runtime):
            raise PermissionError("Procedure directory access is forbidden")


class BoundedOutput:
    def __init__(self, stream: Any) -> None:
        self.stream, self.count = stream, 0

    def write(self, value: str) -> int:
        self.count += len(value.encode("utf-8"))
        if self.count > 1024**2:
            raise RuntimeError("Procedure output limit (1 MiB) reached")
        return self.stream.write(value)

    def flush(self) -> None:
        self.stream.flush()
