"""Evaluation engine (blueprint §21): datasets, scoring, metrics, reports, comparison, gates."""

from hawser_evals.compare import compare, render_compare_md
from hawser_evals.dataset import Dataset, EvalCase, ScoringConfig, load_dataset
from hawser_evals.gate import check_thresholds
from hawser_evals.report import render_md, write_report
from hawser_evals.runner import EvalConfig, EvalRun, run_eval
from hawser_evals.scoring import CaseScore, LeafScore, score_case

__all__ = [
    "CaseScore",
    "Dataset",
    "EvalCase",
    "EvalConfig",
    "EvalRun",
    "LeafScore",
    "ScoringConfig",
    "check_thresholds",
    "compare",
    "load_dataset",
    "render_compare_md",
    "render_md",
    "run_eval",
    "score_case",
    "write_report",
]
