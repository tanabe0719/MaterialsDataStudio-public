"""Explicit statistical workflows; result labels are MDS, not Igor wave layouts."""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats

# Labels shown in the Statistics menu, with deliberately bounded method choices.
STATISTICS = {
    "summary": ("Wave Stats", ("Summary",)),
    "ftest": ("F-Test", ("Variance ratio",)),
    "chi": ("Chi Squared-Test", ("Observed vs expected counts",)),
    "variances": ("Variances Test", ("Bartlett", "Levene (mean)", "Levene (median)", "Levene (10% trimmed)")),
    "anova": ("One-way ANOVA", ("Standard", "Welch (Igor integer DF)", "Brown-Forsythe", "Welch (fractional DF)")),
    "anova2": ("Two-way ANOVA", ("Without replication (matrix)", "Balanced replication (3D array)")),
    "correlation": ("Correlation Tests", ("Pearson", "Spearman", "Kendall")),
    "multiple": ("Multi-Comparison Tests", ("Tukey HSD",)),
    "normality": ("Normality Tests", ("Jarque-Bera", "Shapiro-Wilk")),
    "circular": ("Circular Statistics Tests", ("Rayleigh uniformity (radians)",)),
    "contingency": ("Contingency Table Tests", ("Pearson chi-square", "Yates correction", "Fisher exact (2x2)")),
    "resample": ("Resampling Statistics", ("Bootstrap mean (percentile CI)",)),
    "ks": ("Kolmogorov-Smirnov Test", ("Two samples", "One sample vs standard normal")),
    "rank": ("Two Sample Tests", ("Mann-Whitney U", "Wilcoxon signed rank", "Kruskal-Wallis")),
    "average": ("Waves Average", ("Pointwise mean / SD / SEM",)),
}


def real_array(values: Any) -> np.ndarray:
    array = np.asarray(values)
    if array.dtype.kind not in "biuf" or not array.size or array.size > 10_000_000:
        raise ValueError("Select nonempty real numeric waves, at most 10 million values each")
    return array.astype(float)


def statistics(kind: str, arrays: list[Any], *, method: int = 0, alpha: float = .05,
               tail: int = 4, repeats: int = 2000, seed: int = 0) -> dict[str, float]:
    """Compute a labelled report, retaining paired missing-value alignment."""
    if kind not in STATISTICS or not 0 <= method < len(STATISTICS[kind][1]):
        raise ValueError("Unknown statistical test or method")
    if not np.isfinite(alpha) or not 0 < alpha < 1 or tail not in {1, 2, 4}:
        raise ValueError("Alpha must lie between zero and one; tail must be 1, 2 or 4")
    if tail != 4 and (kind not in {"ftest", "correlation", "ks", "rank", "contingency"} or (kind == "rank" and method == 2) or (kind == "contingency" and method != 2)):
        raise ValueError("This method supports only a two-sided / omnibus test")
    raw = [real_array(a) for a in arrays]
    if not raw or sum(a.size for a in raw) > 10_000_000:
        raise ValueError("Select waves with at most 10 million total observations")
    data = [a.ravel(order="F")[np.isfinite(a.ravel(order="F"))] for a in raw]
    alternative = {1: "greater", 2: "less", 4: "two-sided"}[tail]
    result: dict[str, float] = {}
    r: Any
    df1: Any
    df2: Any

    def count(low: int, high: int, minimum: int = 2) -> None:
        if not low <= len(data) <= high or any(len(a) < minimum for a in data):
            raise ValueError(f"Select {low}..{high} waves, each with at least {minimum} finite observations")

    def paired() -> tuple[np.ndarray, np.ndarray]:
        count(2, 2)
        a, b = (v.ravel(order="F") for v in raw)
        if a.shape != b.shape:
            raise ValueError("Paired waves must have equal lengths")
        valid = np.isfinite(a) & np.isfinite(b)
        if valid.sum() < 3:
            raise ValueError("At least three finite pairs are required")
        return a[valid], b[valid]

    def tested(statistic: Any, p: Any) -> None:
        if np.isnan(statistic) or not np.isfinite(p):
            raise ValueError("Test is undefined for these data (check constant waves or empty cells)")
        result.update(Statistic=float(statistic), P=float(p), Alpha=alpha, Reject=float(p < alpha))

    if kind == "summary":
        count(1, 1, 1)
        a = data[0]
        result.update(N=float(len(a)), Missing=float(raw[0].size-len(a)), Mean=float(a.mean()),
                      SD=float(a.std(ddof=1)) if len(a)>1 else float("nan"), SEM=float(stats.sem(a)) if len(a)>1 else float("nan"), Min=float(a.min()),
                      Max=float(a.max()), Median=float(np.median(a)), Sum=float(a.sum()))
    elif kind == "ftest":
        count(2, 2)
        a, b = data
        va, vb = a.var(ddof=1), b.var(ddof=1)
        if va <= 0 or vb <= 0:
            raise ValueError("F-test requires positive sample variances")
        f = float(va/vb)
        df1, df2 = len(a)-1, len(b)-1
        p = stats.f.sf(f, df1, df2) if tail == 1 else stats.f.cdf(f, df1, df2) if tail == 2 else min(1., 2*min(stats.f.cdf(f, df1, df2), stats.f.sf(f, df1, df2)))
        tested(f, p)
        result.update(DF1=float(df1), DF2=float(df2), LowerCritical=float(stats.f.ppf(alpha/(2 if tail == 4 else 1), df1, df2)), UpperCritical=float(stats.f.isf(alpha/(2 if tail == 4 else 1), df1, df2)))
    elif kind == "variances":
        count(2, 100)
        r = stats.bartlett(*data) if method == 0 else stats.levene(*data, center={1: "mean", 2: "median", 3: "trimmed"}[method], proportiontocut=.1)
        tested(r.statistic, r.pvalue)
    elif kind == "anova":
        count(2, 100)
        if any(a.var(ddof=1) == 0 for a in data):
            raise ValueError("ANOVA requires nonconstant groups")
        if method == 0:
            r = stats.f_oneway(*data)
            df1, df2 = len(data)-1, sum(map(len, data))-len(data)
            tested(r.statistic, r.pvalue)
        elif method in {1, 3}:
            n = np.array([len(a) for a in data])
            means = np.array([a.mean() for a in data])
            w = n/np.array([a.var(ddof=1) for a in data])
            k, total = len(data), w.sum()
            adjust = np.sum((1-w/total)**2/(n-1))
            df1, df2 = k-1, (k*k-1)/(3*adjust)
            if method == 1:
                df2 = np.floor(df2)
            f = np.sum(w*(means-np.sum(w*means)/total)**2)/(k-1)/(1+2*(k-2)*adjust/(k*k-1))
            tested(f, stats.f.sf(f, df1, df2))
        else:
            n = np.array([len(a) for a in data])
            means = np.array([a.mean() for a in data])
            variances = np.array([a.var(ddof=1) for a in data])
            components = (1-n/n.sum())*variances
            df1 = len(data)-1
            df2 = np.floor(components.sum()**2/np.sum(components**2/(n-1)))
            f = np.sum(n*(means-np.sum(n*means)/n.sum())**2)/components.sum()
            tested(f, stats.f.sf(f, df1, df2))
        result.update(DF1=float(df1), DF2=float(df2), Critical=float(stats.f.isf(alpha, df1, df2)))
    elif kind == "anova2":
        count(1, 1)
        a = raw[0]
        if a.ndim != (2 if method == 0 else 3) or min(a.shape) < 2 or not np.isfinite(a).all():
            raise ValueError("Use a complete matrix (rows=A, columns=B), or balanced 3D array (A,B,replicates), all dimensions >=2")
        rows, cols, replicates = *a.shape[:2], (1 if a.ndim == 2 else a.shape[2])
        cells = a if a.ndim == 2 else a.mean(axis=2)
        grand = a.mean()
        ssa = cols*replicates*np.sum((cells.mean(axis=1)-grand)**2)
        ssb = rows*replicates*np.sum((cells.mean(axis=0)-grand)**2)
        interaction = replicates*np.sum((cells-cells.mean(axis=1)[:, None]-cells.mean(axis=0)[None, :]+grand)**2)
        error = interaction if method == 0 else np.sum((a-cells[..., None])**2)
        dfe = (rows-1)*(cols-1) if method == 0 else rows*cols*(replicates-1)
        if error <= 0:
            raise ValueError("Two-way ANOVA requires positive residual variance")
        for label, ss, df in [("A", ssa, rows-1), ("B", ssb, cols-1), *(([("Interaction", interaction, (rows-1)*(cols-1))]) if method else [])]:
            f = float(ss/df/(error/dfe))
            result.update({label+"_F": f, label+"_DF": float(df), label+"_P": float(stats.f.sf(f, df, dfe))})
        result.update(ErrorDF=float(dfe), Alpha=alpha)
    elif kind == "correlation":
        a, b = paired()
        if np.ptp(a) == 0 or np.ptp(b) == 0:
            raise ValueError("Correlation requires nonconstant waves")
        r = (stats.pearsonr, stats.spearmanr, stats.kendalltau)[method](a, b, alternative=alternative)
        tested(r.statistic, r.pvalue)
        result["Pairs"] = float(len(a))
    elif kind == "multiple":
        count(2, 100)
        if len(data) > 30:
            raise ValueError("Tukey comparisons are limited to 30 groups")
        r = stats.tukey_hsd(*data)
        if not np.isfinite(r.statistic).all() or not np.isfinite(r.pvalue).all():
            raise ValueError("Tukey HSD is undefined for these groups; check within-group variation")
        ci = r.confidence_interval(1-alpha)
        for i in range(len(data)):
            for j in range(i+1, len(data)):
                prefix = f"Group{i+1}_vs_{j+1}_"
                result.update({prefix+"Difference": float(r.statistic[i,j]), prefix+"P": float(r.pvalue[i,j]), prefix+"Low": float(ci.low[i,j]), prefix+"High": float(ci.high[i,j])})
    elif kind == "normality":
        count(1, 1)
        if len(data[0]) < (3 if method else 8) or np.ptp(data[0]) == 0:
            raise ValueError("Normality test needs nonconstant observations (JB >=8, Shapiro >=3)")
        if method and len(data[0]) > 5000:
            raise ValueError("Shapiro-Wilk is limited to 5000 observations")
        r = (stats.jarque_bera, stats.shapiro)[method](data[0])
        tested(r.statistic, r.pvalue)
    elif kind == "circular":
        count(1, 1)
        a = data[0]
        n = len(a)
        resultant = abs(np.exp(1j*a).sum())
        z = resultant**2/n
        p = np.exp(np.sqrt(1+4*n+4*(n*n-resultant**2))-(1+2*n))
        tested(z, min(1., float(p)))
        result.update(MeanAngle=float(np.angle(np.exp(1j*a).mean())), MeanResultant=float(resultant/n))
    elif kind == "chi":
        count(2, 2)
        a, b = raw
        if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all() or np.any(a < 0) or np.any(b <= 0):
            raise ValueError("Observed/expected counts must match, be finite, observed >=0 and expected >0")
        r = stats.chisquare(a.ravel(), b.ravel())
        tested(r.statistic, r.pvalue)
        result["DF"] = float(a.size-1)
    elif kind == "contingency":
        count(1, 1)
        a = raw[0]
        if a.ndim != 2 or min(a.shape) < 2 or not np.isfinite(a).all() or np.any(a < 0) or np.any(a != np.floor(a)):
            raise ValueError("Use a matrix of nonnegative integer counts, at least 2x2")
        if method == 2:
            if a.shape != (2, 2):
                raise ValueError("Fisher exact test requires 2x2")
            r = stats.fisher_exact(a.astype(np.int64), alternative=alternative)
            tested(r.statistic, r.pvalue)
        else:
            chi, p, df, _ = tuple(stats.chi2_contingency(a, correction=method == 1))
            tested(chi, p)
            result["DF"] = float(df)
    elif kind == "resample":
        count(1, 1)
        a = data[0]
        if int(repeats) != repeats or not 100 <= repeats <= 100000 or int(seed) != seed or not 0 <= seed < 2**32 or len(a)*repeats > 20_000_000:
            raise ValueError("Use 100..100000 resamples, nonnegative 32-bit seed and at most 20 million sampled values")
        rng = np.random.default_rng(seed)
        means = np.array([rng.choice(a, len(a)).mean() for _ in range(int(repeats))])
        result.update(Mean=float(a.mean()), BootstrapSE=float(means.std(ddof=1)), Low=float(np.quantile(means, alpha/2)), High=float(np.quantile(means, 1-alpha/2)), Resamples=float(repeats), Seed=float(seed))
    elif kind == "ks":
        count(1 if method else 2, 1 if method else 2)
        r = stats.kstest(data[0], "norm", alternative=alternative) if method else stats.ks_2samp(*data, alternative=alternative)
        tested(r.statistic, r.pvalue)
    elif kind == "rank":
        count(2, 100 if method == 2 else 2)
        r = stats.mannwhitneyu(data[0], data[1], alternative=alternative) if method == 0 else stats.wilcoxon(*paired(), alternative=alternative) if method == 1 else stats.kruskal(*data)
        tested(r.statistic, r.pvalue)
    else:
        raise ValueError("Pointwise averaging creates waves; use the averaging workflow")
    return result
