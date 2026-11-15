# -*- coding: utf-8 -*-
"""R1 / E1 技能抽取方法对比（论文 5.3）——真实英文 JD + 平台标注金标准

  M1 规范名词典（基线）      仅匹配 LinkedIn 技能类名，词边界、忽略大小写
  M2 加权词典 + 否定过滤     规范名/别名/隐式表达全量匹配，按表达类型加权，
                             过滤否定语境，剔除易歧义的短缩写与 "Other"
  M3 LSA 语义扩展（进阶）    M2 结果并入 TF-IDF + 截断 SVD 的语义相似度命中

  统一按置信度取 Top-K（K=3，依据金标准平均每岗位 1.68 个技能），
  报告 micro Precision / Recall / F1 与单条耗时；另给不截断的原始对照。
"""
from __future__ import annotations
import re
import time
import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

from rcommon import (DATA, save_csv, print_table, hr, prf,
                     GOLD_SAMPLE, NEGATION_WINDOW, SEMANTIC_THRESHOLD,
                     TFIDF_MIN_DF, SEED)
from skill_dict import SKILLS, ALL_EXPRS

TOP_K = 3
WEIGHT = {"规范名": 1.0, "别名": 0.8, "隐式表达": 0.6}
NEG_WORDS = ["not required", "not necessary", "not needed", "no experience",
             "without", "is not", "are not", "not a requirement", "preferred, but not"]
SKIP_CANON = {"Other"}          # "other" 在英文 JD 中过于常见，不可作为可观测表达


def expr_meta():
    """为每个技能构造一个合并正则 + 表达类型表；过滤歧义短表达。"""
    out = {}
    for s, cfg in SKILLS.items():
        if s in SKIP_CANON:
            out[s] = (None, [])
            continue
        trip = []
        for kind, exprs in (("规范名", [s]), ("别名", cfg["aliases"]),
                            ("隐式表达", cfg["implicit"])):
            for e in exprs:
                if len(e) <= 3:            # IT / HR / QA / PR / BD 等歧义缩写
                    continue
                trip.append((e, kind))
        if not trip:
            out[s] = (None, [])
            continue
        pat = re.compile(r"\b(?:" + "|".join(re.escape(e) for e, _ in trip) + r")\b", re.I)
        out[s] = (pat, trip)
    return out


META = expr_meta()


def negated(text_low: str, pos: int) -> bool:
    for w in NEG_WORDS:
        j = text_low.rfind(w, 0, pos)
        if j >= 0 and pos - j <= NEGATION_WINDOW:
            return True
    return False


def m2_scores(desc: str) -> dict[str, float]:
    """返回 {技能: 加权置信度}，M1 由 restrict_to_canon 参数复用。"""
    d = desc.lower()
    scores: dict[str, float] = {}
    for s, (pat, trip) in META.items():
        if pat is None:
            continue
        for m in pat.finditer(d):
            if negated(d, m.start()):
                continue
            # 匹配到的具体表达 → 找它的类型权重（取最高权重）
            hit = m.group(0).lower()
            w = max((WEIGHT[k] for e, k in trip if e.lower() == hit), default=0.6)
            scores[s] = scores.get(s, 0.0) + w
    return scores


def topk(scores: dict[str, float], k: int = TOP_K) -> set[str]:
    return {s for s, _ in sorted(scores.items(), key=lambda x: (-x[1], x[0]))[:k]}


class LSA:
    def __init__(self, descs: list[str]):
        self.vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2),
                                   min_df=TFIDF_MIN_DF, max_features=30000,
                                   stop_words="english", sublinear_tf=True)
        X = self.vec.fit_transform(descs)
        n = min(64, X.shape[1] - 1, X.shape[0] - 1)
        self.svd = TruncatedSVD(n_components=max(2, n), random_state=SEED)
        self.D = normalize(self.svd.fit_transform(X))
        names, protos = [], []
        for s, (_, trip) in META.items():
            if not trip:
                continue
            names.append(s)
            protos.append(" ".join(e for e, _ in trip))
        P = normalize(self.svd.transform(self.vec.transform(protos)))
        self.names, self.P = names, P

    def sims(self, i: int) -> np.ndarray:
        return self.D[i] @ self.P.T

    def hits(self, i: int, threshold: float) -> dict[str, float]:
        v = self.sims(i)
        return {self.names[j]: float(v[j]) for j in np.where(v >= threshold)[0]}


def evaluate(name: str, preds: list[set[str]], truth: list[set[str]],
             seconds: float, prep: float = 0.0) -> dict:
    tp = fp = fn = 0
    for p, t in zip(preds, truth):
        tp += len(p & t); fp += len(p - t); fn += len(t - p)
    pr, rc, f1 = prf(tp, fp, fn)
    return {"方法": name, "Precision": round(pr, 4), "Recall": round(rc, 4),
            "F1": round(f1, 4), "单条耗时(ms)": round(seconds / len(preds) * 1000, 3),
            "预处理耗时(s)": round(prep, 3), "TP": tp, "FP": fp, "FN": fn}


def main() -> None:
    hr()
    print("E1 技能抽取方法对比（真实数据 / 论文 5.3）")
    hr()
    jobs = pd.read_csv(DATA / "gold_jobs.csv", encoding="utf-8-sig")
    gold = pd.read_csv(DATA / "gold_standard.csv", encoding="utf-8-sig")
    gold["job_id"] = gold["job_id"].astype(str)   # CSV 中纯数字会被 pandas 推断为 int
    truth_map = gold.groupby("job_id")["skill_std"].apply(set).to_dict()
    jobs["job_id"] = jobs["job_id"].astype(str)
    descs = jobs.description.fillna("").astype(str).tolist()
    ids = jobs.job_id.tolist()
    truth = [truth_map.get(j, set()) for j in ids]
    print(f"  评测岗位 {len(ids)} 条，平台标注技能对 {sum(len(t) for t in truth)} 个，"
          f"平均 {np.mean([len(t) for t in truth]):.2f} 个/岗位")

    # M1 规范名词典：只保留规范名匹配，并过滤否定语境
    t0 = time.perf_counter()
    pred1, raw1 = [], []
    canon_pats = {s: re.compile(r"\b" + re.escape(s) + r"\b", re.I)
                  for s in SKILLS if s not in SKIP_CANON}
    for d in descs:
        dl = d.lower()
        sc = {s: 1.0 for s, p in canon_pats.items()
              if any(not negated(dl, m.start()) for m in p.finditer(dl))}
        raw1.append(set(sc))
        pred1.append(topk(sc))
    r1 = evaluate("M1 规范名词典（基线）", pred1, truth, time.perf_counter() - t0)

    # M2 加权词典 + 否定过滤
    t0 = time.perf_counter()
    scores2 = [m2_scores(d) for d in descs]
    raw2 = [set(s) for s in scores2]
    pred2 = [topk(s) for s in scores2]
    r2 = evaluate("M2 加权词典 + 否定过滤", pred2, truth, time.perf_counter() - t0)
    r2raw = evaluate("M2 不截断（对照）", raw2, truth, time.perf_counter() - t0)

    # M3 LSA 语义扩展
    tp = time.perf_counter()
    lsa = LSA(descs)
    prep = time.perf_counter() - tp
    t0 = time.perf_counter()
    sem_hits = [lsa.hits(i, SEMANTIC_THRESHOLD) for i in range(len(descs))]
    scores3 = []
    for sc, sh in zip(scores2, sem_hits):
        merged = dict(sc)
        for s, v in sh.items():
            merged[s] = merged.get(s, 0.0) + 0.5 + v
        scores3.append(merged)
    pred3 = [topk(s) for s in scores3]
    r3 = evaluate("M3 LSA 语义扩展（进阶）", pred3, truth,
                  time.perf_counter() - t0, prep)

    df = pd.DataFrame([r1, r2, r3, r2raw])
    save_csv(df, "e1_extraction.csv")
    print_table(df[["方法", "Precision", "Recall", "F1", "单条耗时(ms)", "预处理耗时(s)"]],
                "表 5-3 E1 抽取方法对比（Top-3 截断）")

    # 错误类型分析（以 M2 为例）
    err = []
    for i, d in enumerate(descs):
        dl = d.lower()
        for s in truth[i] - pred2[i]:
            trip = dict(META.get(s, (None, []))[1] or [])
            has_alias = any(re.search(r"\b" + re.escape(e) + r"\b", dl)
                            for e in SKILLS.get(s, {}).get("aliases", []) if len(e) > 3)
            has_impl = any(e.lower() in dl
                           for e in SKILLS.get(s, {}).get("implicit", []))
            if s in SKIP_CANON:
                kind = "不可观测类别（Other）"
            elif has_alias:
                kind = "别名已覆盖但置信度未进 Top-K"
            elif has_impl:
                kind = "隐式表达漏抽/权重不足"
            else:
                kind = "平台标注未体现在 JD 文本中"
            err.append({"job_id": ids[i], "错误类型": kind, "技能": s})
        for s in pred2[i] - truth[i]:
            err.append({"job_id": ids[i], "错误类型": "文本命中但平台未标注（FP）", "技能": s})
    edf = pd.DataFrame(err)
    if len(edf):
        save_csv(edf.head(300), "e1_error_samples.csv")
        sm = edf.groupby("错误类型").size().reset_index(name="数量")
        sm["占比"] = (sm["数量"] / len(edf)).round(4)
        save_csv(sm, "e1_error_types.csv")
        print_table(sm, "E1 错误类型归纳（M2）")

    # 每类技能的召回情况（论文 5.3 讨论「平台标注与文本可观测性差异」）
    per = []
    for s in sorted(SKILLS):
        n = sum(s in t for t in truth)
        hit = sum(s in p for p in pred2)
        per.append({"技能类别": s, "金标准岗位数": n, "M2 预测岗位数": hit,
                    "召回率": round(hit / n, 4) if n else None})
    save_csv(pd.DataFrame(per), "e1_per_skill_recall.csv")

    best = df.loc[df["F1"].idxmax(), "方法"]
    print(f"\n  [结论] F1 最优：{best}")
    print("  说明：平台标注包含大量 JD 文本未显式写出的隐式技能，"
          "因此绝对 Recall 存在上限，论文 5.8 需讨论该口径差异。")


if __name__ == "__main__":
    main()
