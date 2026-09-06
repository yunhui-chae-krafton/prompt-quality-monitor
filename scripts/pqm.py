#!/usr/bin/env python3
"""Prompt Quality Monitor — CLI.

    pqm.py analyze                 결정론적 지표만, 전량. 무료.
    pqm.py grade --limit 300       LLM 루브릭 채점 (표본). 캐시됨.
    pqm.py dashboard               HTML 대시보드 생성
    pqm.py run --limit 300         위 셋을 순서대로

Korean lemmatization is much better with kiwipiepy installed; the tool falls
back to a regex approximation without it.
"""

import argparse
import json
import os
import sys

from pqm import analyze, extract, llm_grade, report

# Output lives in the user's home, not next to the code. When this ships as a
# plugin the code sits in a managed install directory that is replaced on every
# update, which would take the (paid-for) grade cache with it.
DEFAULT_OUT = os.environ.get(
    "PQM_OUT", os.path.expanduser("~/.prompt-quality-monitor")
)


def _load(args):
    """Extract, attach any cached LLM grades, then analyze.

    Order matters: `build` computes the correlations, and the LLM-graded ones
    are silently dropped if the grades are not on the records yet.
    """
    sessions, records = extract.load_all(
        root=args.root, projects=args.project, since=args.since
    )
    if not records:
        sys.exit("입력한 프롬프트를 찾지 못했습니다. --root / --project 를 확인하세요.")

    llm_grade.attach(records,
                     llm_grade.load_cache(os.path.join(args.out, "llm-cache.json")))
    return analyze.build(sessions, records)


def _persist(result, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    slim = {
        "sessions": result["sessions"],
        "kind_counts": result["kind_counts"],
        "correlations": result["correlations"],
        "vague_top": result["vague_top"],
        "monthly": result["monthly"],
        "weekly": result["weekly"],
        "prompts": [
            {k: v for k, v in r.items() if k != "features"} | {
                "features": {k: v for k, v in r["features"].items()
                             if k != "vague_terms"},
                "vague_terms": r["features"]["vague_terms"],
            }
            for r in result["prompts"]
        ],
    }
    path = os.path.join(out_dir, "analysis.json")
    with open(path, "w") as handle:
        json.dump(slim, handle, ensure_ascii=False)
    return path


def cmd_analyze(args):
    result = _load(args)
    path = _persist(result, args.out)

    md = report.render(result)
    md_path = os.path.join(args.out, "report.md")
    with open(md_path, "w") as handle:
        handle.write(md)

    print(md if args.print_report else f"분석 완료 → {path}\n리포트 → {md_path}")
    return result


def cmd_grade(args):
    result = _load(args)
    os.makedirs(args.out, exist_ok=True)
    cache = os.path.join(args.out, "llm-cache.json")
    info = llm_grade.grade(
        result["prompts"], cache, limit=args.limit, batch_size=args.batch,
        model=args.model, progress=lambda m: print(m, flush=True),
    )
    print(f"채점 완료: 신규 {info['new']}개, 누적 {info['graded']}개, "
          f"이번 비용 ${info['cost_usd']:.2f}")
    _persist(result, args.out)
    with open(os.path.join(args.out, "report.md"), "w") as handle:
        handle.write(report.render(result))
    return result


def cmd_dashboard(args):
    from pqm import dashboard
    result = _load(args)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "dashboard.html")
    with open(path, "w") as handle:
        handle.write(dashboard.render(result))
    print(f"대시보드 → {path}")


def cmd_run(args):
    cmd_grade(args)
    cmd_dashboard(args)


def main():
    # Shared options live on a parent parser so they are accepted on either
    # side of the subcommand; argparse otherwise rejects `analyze --out ...`.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=extract.DEFAULT_ROOT,
                        help="Claude 프로젝트 디렉토리 (기본: ~/.claude/projects)")
    common.add_argument("--project", action="append",
                        help="프로젝트 디렉토리명 부분 일치 필터 (반복 가능)")
    common.add_argument("--since", help="ISO 날짜. 이후 시작된 세션만")
    common.add_argument("--out", default=DEFAULT_OUT, help="출력 디렉토리")

    parser = argparse.ArgumentParser(
        parents=[common],
        description="Claude Code 세션 로그에서 내 프롬프트를 뽑아 점수화한다.")
    subs = parser.add_subparsers(dest="command", required=True)

    sub = subs.add_parser("analyze", parents=[common],
                          help="결정론적 지표 (무료, 전량)")
    sub.add_argument("--print-report", action="store_true",
                     help="리포트를 표준출력으로")
    sub.set_defaults(func=cmd_analyze)

    for name, func, help_text in (("grade", cmd_grade, "LLM 루브릭 채점 (표본)"),
                                  ("run", cmd_run, "채점 + 대시보드")):
        sub = subs.add_parser(name, parents=[common], help=help_text)
        sub.add_argument("--limit", type=int, default=300, help="채점 표본 크기")
        sub.add_argument("--batch", type=int, default=12, help="한 호출당 프롬프트 수")
        sub.add_argument("--model", default="claude-sonnet-5")
        sub.set_defaults(func=func)

    sub = subs.add_parser("dashboard", parents=[common],
                          help="HTML 대시보드 생성")
    sub.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
